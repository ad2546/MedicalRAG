"""Tests for cache_service.py — CacheService and GlobalRateLimiter."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.services.cache_service import CacheService, GlobalRateLimiter


# ---------------------------------------------------------------------------
# GlobalRateLimiter
# ---------------------------------------------------------------------------

class TestGlobalRateLimiter:
    def test_allows_requests_under_limit(self):
        limiter = GlobalRateLimiter(daily_limit=3)
        assert limiter.check_and_increment() is True
        assert limiter.check_and_increment() is True
        assert limiter.check_and_increment() is True

    def test_blocks_at_limit(self):
        limiter = GlobalRateLimiter(daily_limit=2)
        limiter.check_and_increment()
        limiter.check_and_increment()
        assert limiter.check_and_increment() is False

    def test_resets_on_new_day(self):
        limiter = GlobalRateLimiter(daily_limit=1)
        limiter.check_and_increment()
        assert limiter.check_and_increment() is False

        # Simulate a new day by backdating the stored ordinal
        limiter._day = datetime.now(UTC).toordinal() - 1
        assert limiter.check_and_increment() is True

    def test_stats_returns_correct_fields(self):
        limiter = GlobalRateLimiter(daily_limit=10)
        limiter.check_and_increment()
        limiter.check_and_increment()
        stats = limiter.stats()
        assert stats["limit"] == 10
        assert stats["used_today"] == 2
        assert stats["remaining_today"] == 8
        assert "resets_at_utc" in stats

    def test_stats_remaining_never_negative(self):
        limiter = GlobalRateLimiter(daily_limit=1)
        limiter.check_and_increment()
        limiter.check_and_increment()  # over limit
        stats = limiter.stats()
        assert stats["remaining_today"] >= 0

    def test_limit_zero_always_blocks(self):
        limiter = GlobalRateLimiter(daily_limit=0)
        assert limiter.check_and_increment() is False


# ---------------------------------------------------------------------------
# CacheService — keys
# ---------------------------------------------------------------------------

class TestCacheServiceKeys:
    def test_case_key_is_deterministic(self):
        svc = CacheService()
        k1 = svc.case_key(["fever", "cough"], {"hr": 90}, {"wbc": 12})
        k2 = svc.case_key(["fever", "cough"], {"hr": 90}, {"wbc": 12})
        assert k1 == k2

    def test_case_key_normalises_symptom_order(self):
        svc = CacheService()
        k1 = svc.case_key(["cough", "fever"], {}, {})
        k2 = svc.case_key(["fever", "cough"], {}, {})
        assert k1 == k2

    def test_case_key_normalises_symptom_case(self):
        svc = CacheService()
        k1 = svc.case_key(["Fever"], {}, {})
        k2 = svc.case_key(["fever"], {}, {})
        assert k1 == k2

    def test_case_key_differs_for_different_symptoms(self):
        svc = CacheService()
        k1 = svc.case_key(["fever"], {}, {})
        k2 = svc.case_key(["chest pain"], {}, {})
        assert k1 != k2

    def test_llm_key_is_deterministic(self):
        svc = CacheService()
        msgs = [{"role": "user", "content": "hello"}]
        assert svc.llm_key("model-a", msgs) == svc.llm_key("model-a", msgs)

    def test_llm_key_differs_for_different_models(self):
        svc = CacheService()
        msgs = [{"role": "user", "content": "hello"}]
        assert svc.llm_key("model-a", msgs) != svc.llm_key("model-b", msgs)


# ---------------------------------------------------------------------------
# CacheService — get/set
# ---------------------------------------------------------------------------

class TestCacheServiceGetSet:
    def test_case_cache_miss_returns_none(self):
        svc = CacheService()
        assert svc.get_case("nonexistent") is None

    def test_case_cache_hit_returns_value(self):
        svc = CacheService()
        svc.set_case("key1", {"result": "data"})
        assert svc.get_case("key1") == {"result": "data"}

    def test_llm_cache_miss_returns_none(self):
        svc = CacheService()
        assert svc.get_llm("nonexistent") is None

    def test_llm_cache_hit_returns_value(self):
        svc = CacheService()
        svc.set_llm("key1", {"text": "response", "usage": {}})
        assert svc.get_llm("key1") == {"text": "response", "usage": {}}

    def test_stats_reflects_cache_size(self):
        svc = CacheService()
        svc.set_case("k1", {"x": 1})
        svc.set_case("k2", {"x": 2})
        stats = svc.stats()
        assert stats["case_cache"]["size"] == 2
        assert stats["llm_cache"]["size"] == 0

    def test_stats_has_correct_config(self):
        svc = CacheService()
        stats = svc.stats()
        assert stats["case_cache"]["maxsize"] == 500
        assert stats["case_cache"]["ttl_seconds"] == 3600
        assert stats["llm_cache"]["maxsize"] == 2000
        assert stats["llm_cache"]["ttl_seconds"] == 86400
