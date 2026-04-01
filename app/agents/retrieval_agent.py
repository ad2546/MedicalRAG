"""Retrieval Agent — hybrid vector + metadata search via pgvector."""

import logging
import uuid
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import RetrievalLog
from app.models.schemas import RetrievedDocument
from app.services.embedding_service import embedding_service
from app.services.tracing_service import tracing_service

logger = logging.getLogger(__name__)


class RetrievalAgent:
    """Fetches the top-K most relevant documents from pgvector with optional metadata filters."""

    async def run(
        self,
        db: AsyncSession,
        case_id: uuid.UUID,
        symptoms: list[str],
        metadata_filters: dict[str, Any] | None = None,
        hint: str | None = None,
        trace_id: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedDocument]:
        top_k = top_k or settings.top_k_docs
        query_text = embedding_service.build_query(symptoms, hint)
        query_vector = embedding_service.embed(query_text)

        async with tracing_service.span(trace_id or "none", "retrieval_agent"):
            docs = await self._vector_search(db, query_vector, metadata_filters, top_k)
            await self._log_retrieval(db, case_id, query_text, docs)

            tracing_service.trace_event(
                trace_id=trace_id or "none",
                event_name="retrieval",
                inputs={"symptoms": symptoms, "filters": metadata_filters, "hint": hint},
                outputs={"doc_ids": [str(d.id) for d in docs], "count": len(docs)},
            )

        logger.info("Retrieved %d documents for case %s", len(docs), case_id)
        return docs

    async def _vector_search(
        self,
        db: AsyncSession,
        query_vector: list[float],
        filters: dict[str, Any] | None,
        top_k: int,
    ) -> list[RetrievedDocument]:
        # Use raw asyncpg connection so pgvector codec handles numpy arrays correctly.
        # SQLAlchemy named-param binding silently returns 0 rows for vector parameters.
        np_vec = np.array(query_vector, dtype=np.float32)

        where_clauses = ["embedding IS NOT NULL"]
        positional_params: list[Any] = [np_vec]

        if filters:
            if "disease_category" in filters:
                positional_params.append(filters["disease_category"])
                where_clauses.append(f"disease_category = ${len(positional_params)}")
            if "evidence_type" in filters:
                positional_params.append(filters["evidence_type"])
                where_clauses.append(f"evidence_type = ${len(positional_params)}")

        positional_params.append(top_k)
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT
                id,
                content,
                source,
                disease_category,
                evidence_type,
                1 - (embedding <=> $1) AS score
            FROM documents
            WHERE {where_sql}
            ORDER BY embedding <=> $1
            LIMIT ${len(positional_params)}
        """

        # Use raw asyncpg connection — pgvector codec requires numpy arrays as params
        # and the SQLAlchemy text() binding silently returns 0 rows for vector types.
        from pgvector.asyncpg import register_vector as _register_vector
        raw = await db.connection()
        asyncpg_conn = (await raw.get_raw_connection()).driver_connection
        await _register_vector(asyncpg_conn)
        rows = await asyncpg_conn.fetch(sql, *positional_params)

        return [
            RetrievedDocument(
                id=row["id"],
                content=row["content"],
                source=row["source"],
                disease_category=row["disease_category"],
                evidence_type=row["evidence_type"],
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def _log_retrieval(
        self,
        db: AsyncSession,
        case_id: uuid.UUID,
        query: str,
        docs: list[RetrievedDocument],
    ) -> None:
        log = RetrievalLog(
            case_id=case_id,
            query=query,
            retrieved_doc_ids=[d.id for d in docs],
            scores=[d.score for d in docs],
        )
        db.add(log)
        await db.commit()


retrieval_agent = RetrievalAgent()
