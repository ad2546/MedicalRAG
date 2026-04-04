"""Two-level in-process cache for pipeline and LLM responses, plus a global
daily rate limiter that resets at UTC midnight.

Level 1 — case cache:
    Key: SHA256(sorted symptoms + vitals + labs)
    Value: serialised DiagnosisResponse dict
    TTL: 1 hour | max 500 entries

Level 2 — LLM prompt cache:
    Key: SHA256(model_id + messages JSON)
    Value: {"text": ..., "usage": ...}
    TTL: 24 hours | max 2000 entries

Global rate limiter:
    Counter resets at UTC midnight each day.
    Limit configured via GLOBAL_DAILY_REQUEST_LIMIT env var (default 200).
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from threading import Lock

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class GlobalRateLimiter:
    """Thread-safe daily request counter that resets at UTC midnight."""

    def __init__(self, daily_limit: int) -> None:
        self._limit = daily_limit
        self._count: int = 0
        self._day: int = datetime.now(UTC).toordinal()
        self._lock = Lock()

    def _reset_if_new_day(self) -> None:
        today = datetime.now(UTC).toordinal()
        if today != self._day:
            self._count = 0
            self._day = today

    def check_and_increment(self) -> bool:
        """Return True if the request is allowed (and count it), False if limit is exceeded."""
        with self._lock:
            self._reset_if_new_day()
            if self._count >= self._limit:
                return False
            self._count += 1
            return True

    def stats(self) -> dict:
        with self._lock:
            self._reset_if_new_day()
            return {
                "limit": self._limit,
                "used_today": self._count,
                "remaining_today": max(0, self._limit - self._count),
                "resets_at_utc": datetime.now(UTC).strftime("%Y-%m-%d 00:00:00 UTC (next day)"),
            }


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
                    "ttl_seconds": self._case_cache.ttl,
                },
                "llm_cache": {
                    "size": len(self._llm_cache),
                    "maxsize": self._llm_cache.maxsize,
                    "ttl_seconds": self._llm_cache.ttl,
                },
            }


cache_service = CacheService()


def _make_global_rate_limiter() -> GlobalRateLimiter:
    # Import here to avoid circular import (config → cache_service at module load)
    from app.config import settings  # noqa: PLC0415
    return GlobalRateLimiter(daily_limit=settings.global_daily_request_limit)


# Lazily initialised so config is fully loaded before we read the limit.
_global_rate_limiter: GlobalRateLimiter | None = None
_grl_lock = Lock()


def get_global_rate_limiter() -> GlobalRateLimiter:
    global _global_rate_limiter
    if _global_rate_limiter is None:
        with _grl_lock:
            if _global_rate_limiter is None:
                _global_rate_limiter = _make_global_rate_limiter()
    return _global_rate_limiter
