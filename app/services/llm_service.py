"""OCI Generative AI inference using the native OCI Python SDK.

Authentication is resolved in this order:
  1. Instance Principal  — when OCI_USE_INSTANCE_PRINCIPAL=true (OCI compute VMs)
  2. Security token      — local dev after `oci session authenticate`
  3. API key             — standard ~/.oci/config with key_file + fingerprint

Two model slots:
  - oci_model_gen  — richer model for the diagnosis pipeline
  - oci_model_chat — lighter model for the interactive /chat endpoint
"""

import asyncio
import json
import logging
import os
import re
import time
from functools import partial

from json_repair import repair_json
from opentelemetry import trace as otel_trace

try:
    from monocle_apptrace.instrumentation.common.constants import MONOCLE_SDK_VERSION
    from monocle_apptrace.instrumentation.common.utils import get_monocle_version
    _MONOCLE_VERSION = get_monocle_version()
except Exception:
    MONOCLE_SDK_VERSION = "monocle_apptrace.version"
    _MONOCLE_VERSION = "unknown"

import oci
import oci.auth.signers
import oci.config
import oci.generative_ai_inference
import oci.generative_ai_inference.models as gen_models
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.config import settings
from app.services.cache_service import cache_service

logger = logging.getLogger(__name__)
_otel_tracer = otel_trace.get_tracer("medicalrag.llm")


def _build_client() -> oci.generative_ai_inference.GenerativeAiInferenceClient:
    endpoint = (
        f"https://inference.generativeai.{settings.oci_region}.oci.oraclecloud.com"
    )

    if settings.oci_use_instance_principal:
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        return oci.generative_ai_inference.GenerativeAiInferenceClient(
            config={}, signer=signer, service_endpoint=endpoint
        )

    config = oci.config.from_file(profile_name=settings.oci_config_profile)
    region = config.get("region", settings.oci_region)
    endpoint = f"https://inference.generativeai.{region}.oci.oraclecloud.com"

    sec_token_file = config.get("security_token_file")
    if sec_token_file:
        with open(os.path.expanduser(sec_token_file)) as f:
            token = f.read().strip()
        with open(os.path.expanduser(config["key_file"]), "rb") as f:
            private_key = load_pem_private_key(f.read(), password=None)
        signer = oci.auth.signers.SecurityTokenSigner(token, private_key)
        return oci.generative_ai_inference.GenerativeAiInferenceClient(
            config=config, signer=signer, service_endpoint=endpoint
        )

    return oci.generative_ai_inference.GenerativeAiInferenceClient(
        config=config, service_endpoint=endpoint
    )


def _to_oci_messages(messages: list[dict]) -> list:
    result = []
    for msg in messages:
        content = [gen_models.TextContent(text=msg["content"])]
        role = msg["role"]
        if role == "system":
            result.append(gen_models.SystemMessage(content=content))
        elif role == "user":
            result.append(gen_models.UserMessage(content=content))
        elif role == "assistant":
            result.append(gen_models.AssistantMessage(content=content))
    return result


class LLMService:
    def __init__(self) -> None:
        self._client: oci.generative_ai_inference.GenerativeAiInferenceClient | None = None

    def _get_client(self) -> oci.generative_ai_inference.GenerativeAiInferenceClient:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _sync_chat(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> dict:
        # ── LLM prompt cache ──────────────────────────────────────────────
        cache_key = cache_service.llm_key(model_id, messages)
        cached = cache_service.get_llm(cache_key)
        if cached is not None:
            return cached

        # ── OTel span (visible in Okahu Cloud as an LLM call) ─────────────
        with _otel_tracer.start_as_current_span("oci.generativeai.chat") as span:
            span.set_attribute("gen_ai.system", "oci_generativeai")
            span.set_attribute("gen_ai.request.model", model_id)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            span.set_attribute("gen_ai.request.temperature", temperature)
            span.set_attribute("llm.request.type", "chat")
            # Required by monocle exporter to pass skip_export filter
            span.set_attribute(MONOCLE_SDK_VERSION, _MONOCLE_VERSION)
            # Required by Okahu Cloud to associate trace with the correct app
            span.set_attribute("workflow.name", settings.okahu_service_name)
            span.set_attribute("entity.1.name", settings.okahu_service_name)
            span.set_attribute("entity.1.type", "workflow.generic")

            t0 = time.perf_counter()
            response = self._get_client().chat(
                gen_models.ChatDetails(
                    compartment_id=settings.oci_compartment_id,
                    serving_mode=gen_models.OnDemandServingMode(model_id=model_id),
                    chat_request=gen_models.GenericChatRequest(
                        api_format=gen_models.BaseChatRequest.API_FORMAT_GENERIC,
                        messages=_to_oci_messages(messages),
                        max_tokens=max_tokens,
                        temperature=temperature,
                        is_stream=False,
                    ),
                )
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)

            chat_resp = response.data.chat_response
            text = chat_resp.choices[0].message.content[0].text
            usage = getattr(chat_resp, "usage", None)

            prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tokens = getattr(usage, "total_tokens", 0) if usage else 0

            span.set_attribute("gen_ai.usage.prompt_tokens", prompt_tokens)
            span.set_attribute("gen_ai.usage.completion_tokens", completion_tokens)
            span.set_attribute("gen_ai.usage.total_tokens", total_tokens)
            span.set_attribute("llm.latency_ms", latency_ms)

            logger.debug(
                "OCI GenAI — model=%s prompt=%d completion=%d latency=%dms",
                model_id, prompt_tokens, completion_tokens, latency_ms,
            )

        result = {
            "text": text,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
        cache_service.set_llm(cache_key, result)
        return result

    async def _achat(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._sync_chat, model_id, messages, temperature, max_tokens)
        )

    @staticmethod
    def _extract_json(text: str) -> dict:
        # Strip markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        # Extract outermost JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Standard JSON parse failed, attempting repair...")
            try:
                repaired = repair_json(cleaned, return_objects=True)
                if isinstance(repaired, dict):
                    return repaired
                return json.loads(repair_json(cleaned))
            except Exception:
                logger.error("JSON repair failed. Raw model output:\n%s", cleaned)
                raise

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: str = "json_object",
    ) -> dict:
        """Single-turn structured call used by pipeline agents."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if response_format == "json_object":
            messages[0]["content"] += (
                "\n\nIMPORTANT: Respond with valid JSON only. "
                "No markdown fences, no prose, no comments. "
                "All string values must be single-line — no literal newlines inside strings. "
                "Use \\n if you need a line break inside a value."
            )

        result = await self._achat(
            model_id=settings.oci_model_gen,
            messages=messages,
            temperature=0.1,
            max_tokens=8192,
        )
        content = self._extract_json(result["text"]) if response_format == "json_object" else result["text"]
        return {"content": content, "usage": result["usage"]}

    async def chat_messages(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> dict:
        """Multi-turn chat used by the /chat router."""
        result = await self._achat(
            model_id=settings.oci_model_chat,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {"reply": result["text"], "usage": result["usage"]}

    def build_case_context(self, case: dict, documents: list[dict]) -> str:
        evidence_text = "\n\n".join(
            f"[DOC {i+1} | id={d['id']} | category={d.get('disease_category','?')} | type={d.get('evidence_type','?')}]\n{d['content']}"
            for i, d in enumerate(documents)
        )
        return (
            f"PATIENT CASE:\n"
            f"Symptoms: {', '.join(case.get('symptoms', []))}\n"
            f"Vitals: {json.dumps(case.get('vitals', {}))}\n"
            f"History: {json.dumps(case.get('history', {}))}\n"
            f"Labs: {json.dumps(case.get('labs', {}))}\n\n"
            f"RETRIEVED EVIDENCE:\n{evidence_text}"
        )


llm_service = LLMService()
