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
from functools import partial

import oci
import oci.auth.signers
import oci.config
import oci.generative_ai_inference
import oci.generative_ai_inference.models as gen_models
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.config import settings

logger = logging.getLogger(__name__)


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
        chat_resp = response.data.chat_response
        text = chat_resp.choices[0].message.content[0].text
        usage = getattr(chat_resp, "usage", None)

        logger.debug(
            "OCI GenAI tokens — prompt: %d, completion: %d",
            getattr(usage, "prompt_tokens", 0) if usage else 0,
            getattr(usage, "completion_tokens", 0) if usage else 0,
        )
        return {
            "text": text,
            "usage": {
                "prompt_tokens":     getattr(usage, "prompt_tokens",     0) if usage else 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total_tokens":      getattr(usage, "total_tokens",      0) if usage else 0,
            },
        }

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
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("JSON parse failed. Raw model output:\n%s", cleaned)
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
            messages[0]["content"] += "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no prose."

        result = await self._achat(
            model_id=settings.oci_model_gen,
            messages=messages,
            temperature=0.1,
            max_tokens=4096,
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
