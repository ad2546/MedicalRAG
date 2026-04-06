"""LLM inference — Groq (default) or OCI Generative AI (fallback).

Provider selection via LLM_PROVIDER env var:
  LLM_PROVIDER=groq  (default) — Groq cloud, OpenAI-compatible, free tier
  LLM_PROVIDER=oci             — OCI GenAI native SDK

Groq setup:  set GROQ_API_KEY=gsk_...
OCI setup:   set OCI_COMPARTMENT_ID + ~/.oci/config (or OCI_USE_INSTANCE_PRINCIPAL=true)
"""

import asyncio
import json
import logging
import re
import time

from json_repair import repair_json
from opentelemetry import trace as otel_trace

try:
    from monocle_apptrace.instrumentation.common.constants import MONOCLE_SDK_VERSION
    from monocle_apptrace.instrumentation.common.utils import get_monocle_version
    _MONOCLE_VERSION = get_monocle_version()
except Exception:
    MONOCLE_SDK_VERSION = "monocle_apptrace.version"
    _MONOCLE_VERSION = "unknown"

from app.config import settings
from app.services.cache_service import cache_service

logger = logging.getLogger(__name__)
_otel_tracer = otel_trace.get_tracer("medicalrag.llm")

# ── Lazy client singletons ─────────────────────────────────────────────────────
_groq_client = None
_oci_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from openai import AsyncOpenAI
        _groq_client = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _groq_client


def _get_oci_client():
    global _oci_client
    if _oci_client is None:
        import os
        import oci
        import oci.auth.signers
        import oci.config
        import oci.generative_ai_inference
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        endpoint = (
            f"https://inference.generativeai.{settings.oci_region}.oci.oraclecloud.com"
        )

        if settings.oci_use_instance_principal:
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            _oci_client = oci.generative_ai_inference.GenerativeAiInferenceClient(
                config={}, signer=signer, service_endpoint=endpoint
            )
        else:
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
                _oci_client = oci.generative_ai_inference.GenerativeAiInferenceClient(
                    config=config, signer=signer, service_endpoint=endpoint
                )
            else:
                _oci_client = oci.generative_ai_inference.GenerativeAiInferenceClient(
                    config=config, service_endpoint=endpoint
                )
    return _oci_client


class LLMService:

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Strip markdown fences and parse JSON, with repair fallback."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start: end + 1]
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

    async def _achat_groq(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_format: str | None = None,
    ) -> dict:
        cache_key = cache_service.llm_key(model_id, messages)
        cached = cache_service.get_llm(cache_key)
        if cached is not None:
            return cached

        t0 = time.perf_counter()
        kwargs: dict = dict(
            model=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        # monocle auto-instruments AsyncCompletions.create and creates a proper
        # inference span (entity.1.type=inference.openai, span.type=inference,
        # token counts in the "metadata" event).  A manual wrapper span is not
        # needed here — it would duplicate or shadow monocle's span and cause
        # Okahu to see the call as workflow.generic instead of GenAI.
        response = await _get_groq_client().chat.completions.create(**kwargs)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        text = response.choices[0].message.content
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0

        logger.debug(
            "Groq — model=%s prompt=%d completion=%d latency=%dms",
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

    async def _achat_oci(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> dict:
        import oci.generative_ai_inference.models as gen_models

        cache_key = cache_service.llm_key(model_id, messages)
        cached = cache_service.get_llm(cache_key)
        if cached is not None:
            return cached

        def _to_oci_messages(msgs: list[dict]) -> list:
            out = []
            for msg in msgs:
                content = [gen_models.TextContent(text=msg["content"])]
                role = msg["role"]
                if role == "system":
                    out.append(gen_models.SystemMessage(content=content))
                elif role == "user":
                    out.append(gen_models.UserMessage(content=content))
                elif role == "assistant":
                    out.append(gen_models.AssistantMessage(content=content))
            return out

        def _sync() -> dict:
            with _otel_tracer.start_as_current_span("oci.generativeai.chat") as span:
                span.set_attribute("gen_ai.system", "oci_generativeai")
                span.set_attribute("gen_ai.request.model", model_id)
                span.set_attribute("gen_ai.request.max_tokens", max_tokens)
                span.set_attribute("gen_ai.request.temperature", temperature)
                span.set_attribute("llm.request.type", "chat")
                span.set_attribute(MONOCLE_SDK_VERSION, _MONOCLE_VERSION)
                span.set_attribute("workflow.name", settings.okahu_service_name)
                span.set_attribute("entity.1.name", settings.okahu_service_name)
                span.set_attribute("entity.1.type", "workflow.generic")

                t0 = time.perf_counter()
                response = _get_oci_client().chat(
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
                return {
                    "text": text,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                }

        result = await asyncio.get_event_loop().run_in_executor(None, _sync)
        cache_service.set_llm(cache_key, result)
        return result

    async def _achat(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_format: str | None = None,
    ) -> dict:
        if settings.llm_provider == "oci":
            return await self._achat_oci(model_id, messages, temperature, max_tokens)
        return await self._achat_groq(model_id, messages, temperature, max_tokens, response_format)

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

        model_id = (
            settings.oci_model_gen
            if settings.llm_provider == "oci"
            else settings.groq_model_gen
        )
        result = await self._achat(
            model_id=model_id,
            messages=messages,
            temperature=0.1,
            max_tokens=8192,
            response_format=response_format,
        )
        content = (
            self._extract_json(result["text"])
            if response_format == "json_object"
            else result["text"]
        )
        return {"content": content, "usage": result["usage"]}

    async def chat_messages(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> dict:
        """Multi-turn chat used by the /chat router."""
        model_id = (
            settings.oci_model_chat
            if settings.llm_provider == "oci"
            else settings.groq_model_chat
        )
        result = await self._achat(
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {"reply": result["text"], "usage": result["usage"]}

    def build_case_context(self, case: dict, documents: list[dict], max_docs: int | None = None, max_doc_chars: int | None = None) -> str:
        max_docs = max_docs or settings.max_docs_per_prompt
        max_doc_chars = max_doc_chars or settings.max_doc_chars
        evidence_text = "\n\n".join(
            f"[DOC {i+1} | id={d['id']} | category={d.get('disease_category','?')} | type={d.get('evidence_type','?')}]\n{d['content'][:max_doc_chars]}"
            for i, d in enumerate(documents[:max_docs])
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
