"""
Retrieval quality metrics computed locally from a list of RetrievedDocuments.

No LLM call is required — all metrics derive from cosine similarity scores
and document metadata.

Metrics emitted per pipeline run
─────────────────────────────────
doc_count              int   – number of documents retrieved
avg_score              float – mean cosine similarity (0–1)
max_score              float – highest similarity score
min_score              float – lowest similarity score
hit_rate               float – 1.0 if any doc ≥ 0.5, else 0.0
high_relevance_count   int   – docs with score ≥ 0.70
medium_relevance_count int   – docs with 0.50 ≤ score < 0.70
low_relevance_count    int   – docs with score < 0.50
category_diversity     float – unique disease categories / doc_count (0–1)
top_score_bucket       str   – "excellent" ≥0.85 | "good" ≥0.70 | "fair" ≥0.50 | "poor"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.models.schemas import RetrievedDocument

logger = logging.getLogger(__name__)

_HIGH_THRESHOLD   = 0.70
_MEDIUM_THRESHOLD = 0.50


@dataclass(frozen=True)
class RetrievalMetrics:
    doc_count: int              = 0
    avg_score: float            = 0.0
    max_score: float            = 0.0
    min_score: float            = 0.0
    hit_rate: float             = 0.0   # 1.0 or 0.0
    high_relevance_count: int   = 0
    medium_relevance_count: int = 0
    low_relevance_count: int    = 0
    category_diversity: float   = 0.0
    top_score_bucket: str       = "poor"

    def to_dict(self) -> dict:
        return {
            "retrieval.doc_count":              self.doc_count,
            "retrieval.avg_score":              round(self.avg_score, 4),
            "retrieval.max_score":              round(self.max_score, 4),
            "retrieval.min_score":              round(self.min_score, 4),
            "retrieval.hit_rate":               self.hit_rate,
            "retrieval.high_relevance_count":   self.high_relevance_count,
            "retrieval.medium_relevance_count": self.medium_relevance_count,
            "retrieval.low_relevance_count":    self.low_relevance_count,
            "retrieval.category_diversity":     round(self.category_diversity, 4),
            "retrieval.top_score_bucket":       self.top_score_bucket,
        }


class RetrievalMetricsService:
    """Compute retrieval quality metrics from a list of RetrievedDocuments."""

    def compute(self, documents: list[RetrievedDocument]) -> RetrievalMetrics:
        """
        Compute all retrieval metrics from the given document list.

        Returns a frozen RetrievalMetrics dataclass. Call `.to_dict()` to get
        a flat dict suitable for OTel span attributes.
        """
        if not documents:
            logger.debug("No documents retrieved — returning empty metrics")
            return RetrievalMetrics()

        scores = [d.score for d in documents]
        doc_count = len(scores)

        avg_score = sum(scores) / doc_count
        max_score = max(scores)
        min_score = min(scores)

        high_relevance   = sum(1 for s in scores if s >= _HIGH_THRESHOLD)
        medium_relevance = sum(1 for s in scores if _MEDIUM_THRESHOLD <= s < _HIGH_THRESHOLD)
        low_relevance    = sum(1 for s in scores if s < _MEDIUM_THRESHOLD)

        hit_rate = 1.0 if any(s >= _MEDIUM_THRESHOLD for s in scores) else 0.0

        # Category diversity: how spread across disease categories are the docs?
        categories = [d.disease_category for d in documents if d.disease_category]
        unique_categories = len(set(categories))
        category_diversity = unique_categories / doc_count if doc_count > 0 else 0.0

        # Top-score quality bucket
        if max_score >= 0.85:
            top_bucket = "excellent"
        elif max_score >= 0.70:
            top_bucket = "good"
        elif max_score >= 0.50:
            top_bucket = "fair"
        else:
            top_bucket = "poor"

        metrics = RetrievalMetrics(
            doc_count=doc_count,
            avg_score=avg_score,
            max_score=max_score,
            min_score=min_score,
            hit_rate=hit_rate,
            high_relevance_count=high_relevance,
            medium_relevance_count=medium_relevance,
            low_relevance_count=low_relevance,
            category_diversity=category_diversity,
            top_score_bucket=top_bucket,
        )

        logger.info(
            "Retrieval metrics — docs=%d avg=%.3f max=%.3f hit_rate=%.1f bucket=%s diversity=%.2f",
            doc_count, avg_score, max_score, hit_rate, top_bucket, category_diversity,
        )
        return metrics


retrieval_metrics_service = RetrievalMetricsService()
