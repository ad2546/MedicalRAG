"""Two-level in-process cache for pipeline and LLM responses.

Level 1 — case cache:
    Key: SHA256(sorted symptoms + vitals + labs)
    Value: serialised DiagnosisResponse dict
    TTL: 1 hour | max 500 entries

Level 2 — LLM prompt cache:
    Key: SHA256(model_id + messages JSON)
    Value: {"text": ..., "usage": ...}
    TTL: 24 hours | max 2000 entries
"""

import hashlib
import json
import logging
from threading import Lock

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self) -> None:
        self._case_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)
        self._llm_cache: TTLCache = TTLCache(maxsize=2000, ttl=86400)
        self._lock = Lock()

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _sha256(data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()

    def case_key(self, symptoms: list[str], vitals: dict, labs: dict) -> str:
        payload = json.dumps(
            {
                "symptoms": sorted(s.lower().strip() for s in symptoms),
                "vitals": vitals or {},
                "labs": labs or {},
            },
            sort_keys=True,
        )
        return self._sha256(payload)

    def llm_key(self, model_id: str, messages: list[dict]) -> str:
        payload = json.dumps({"model": model_id, "messages": messages}, sort_keys=True)
        return self._sha256(payload)

    # ── case cache ─────────────────────────────────────────────────────────
    def get_case(self, key: str) -> dict | None:
        with self._lock:
            value = self._case_cache.get(key)
        if value is not None:
            logger.debug("Cache HIT (case) key=%s", key[:12])
        return value

    def set_case(self, key: str, value: dict) -> None:
        with self._lock:
            self._case_cache[key] = value
        logger.debug("Cache SET (case) key=%s", key[:12])

    # ── LLM prompt cache ───────────────────────────────────────────────────
    def get_llm(self, key: str) -> dict | None:
        with self._lock:
            value = self._llm_cache.get(key)
        if value is not None:
            logger.debug("Cache HIT (llm) key=%s", key[:12])
        return value

    def set_llm(self, key: str, value: dict) -> None:
        with self._lock:
            self._llm_cache[key] = value
        logger.debug("Cache SET (llm) key=%s", key[:12])

    # ── stats ──────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self._lock:
            return {
                "case_cache": {
                    "size": len(self._case_cache),
                    "maxsize": self._case_cache.maxsize,
                    "ttl": self._case_cache.ttl,
                },
                "llm_cache": {
                    "size": len(self._llm_cache),
                    "maxsize": self._llm_cache.maxsize,
                    "ttl": self._llm_cache.ttl,
                },
            }


cache_service = CacheService()
