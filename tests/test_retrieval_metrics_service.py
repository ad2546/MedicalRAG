"""Tests for retrieval_metrics_service.py."""

import uuid

from app.models.schemas import RetrievedDocument
from app.services.retrieval_metrics_service import RetrievalMetricsService


def _doc(score: float, category: str | None = "cardiology") -> RetrievedDocument:
    return RetrievedDocument(
        id=uuid.uuid4(),
        content="test content",
        source="test",
        disease_category=category,
        evidence_type="guideline",
        score=score,
    )


class TestRetrievalMetricsServiceEmpty:
    def test_empty_returns_zero_metrics(self):
        svc = RetrievalMetricsService()
        m = svc.compute([])
        assert m.doc_count == 0
        assert m.avg_score == 0.0
        assert m.hit_rate == 0.0
        assert m.top_score_bucket == "poor"

    def test_empty_to_dict_has_all_keys(self):
        svc = RetrievalMetricsService()
        d = svc.compute([]).to_dict()
        assert "retrieval.doc_count" in d
        assert "retrieval.avg_score" in d
        assert "retrieval.hit_rate" in d
        assert "retrieval.top_score_bucket" in d


class TestRetrievalMetricsServiceScores:
    def test_doc_count(self):
        svc = RetrievalMetricsService()
        m = svc.compute([_doc(0.9), _doc(0.8), _doc(0.7)])
        assert m.doc_count == 3

    def test_avg_score(self):
        svc = RetrievalMetricsService()
        m = svc.compute([_doc(0.8), _doc(0.6)])
        assert abs(m.avg_score - 0.7) < 0.001

    def test_max_and_min_score(self):
        svc = RetrievalMetricsService()
        m = svc.compute([_doc(0.9), _doc(0.5), _doc(0.3)])
        assert m.max_score == 0.9
        assert m.min_score == 0.3

    def test_hit_rate_one_when_any_above_threshold(self):
        svc = RetrievalMetricsService()
        m = svc.compute([_doc(0.3), _doc(0.6)])
        assert m.hit_rate == 1.0

    def test_hit_rate_zero_when_all_below_threshold(self):
        svc = RetrievalMetricsService()
        m = svc.compute([_doc(0.3), _doc(0.4)])
        assert m.hit_rate == 0.0

    def test_relevance_buckets(self):
        svc = RetrievalMetricsService()
        docs = [_doc(0.9), _doc(0.6), _doc(0.3)]  # high, medium, low
        m = svc.compute(docs)
        assert m.high_relevance_count == 1
        assert m.medium_relevance_count == 1
        assert m.low_relevance_count == 1

    def test_top_score_bucket_excellent(self):
        svc = RetrievalMetricsService()
        assert svc.compute([_doc(0.9)]).top_score_bucket == "excellent"

    def test_top_score_bucket_good(self):
        svc = RetrievalMetricsService()
        assert svc.compute([_doc(0.75)]).top_score_bucket == "good"

    def test_top_score_bucket_fair(self):
        svc = RetrievalMetricsService()
        assert svc.compute([_doc(0.55)]).top_score_bucket == "fair"

    def test_top_score_bucket_poor(self):
        svc = RetrievalMetricsService()
        assert svc.compute([_doc(0.3)]).top_score_bucket == "poor"

    def test_category_diversity_single_category(self):
        svc = RetrievalMetricsService()
        docs = [_doc(0.8, "cardiology"), _doc(0.7, "cardiology")]
        m = svc.compute(docs)
        assert m.category_diversity == 0.5  # 1 unique / 2 docs

    def test_category_diversity_all_different(self):
        svc = RetrievalMetricsService()
        docs = [_doc(0.8, "cardiology"), _doc(0.7, "neurology")]
        m = svc.compute(docs)
        assert m.category_diversity == 1.0

    def test_category_diversity_ignores_none(self):
        svc = RetrievalMetricsService()
        docs = [_doc(0.8, None), _doc(0.7, None)]
        m = svc.compute(docs)
        assert m.category_diversity == 0.0

    def test_to_dict_rounds_floats(self):
        svc = RetrievalMetricsService()
        d = svc.compute([_doc(1 / 3)]).to_dict()
        # Should be rounded to 4 decimal places
        assert len(str(d["retrieval.avg_score"]).split(".")[-1]) <= 4
