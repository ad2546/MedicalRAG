"""POST /workflow/run — Okahu Cloud-compatible external workflow trigger.

Authentication: Bearer token (WORKFLOW_API_KEY env var).
This endpoint is designed to be called by external orchestrators — n8n, Okahu
Cloud workflow triggers, cron jobs, or any HTTP client — without needing a
browser session cookie.

Every run is:
  - Traced end-to-end via Monocle/OTel (visible in Okahu Cloud dashboard)
  - Written to pipeline_audit with source="workflow"
  - Cached: identical cases return immediately from cache
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.config import settings
from app.database import get_db
from app.models.db_models import Case
from app.models.schemas import CaseRequest, DiagnosisResponse
from app.pipeline import pipeline
from app.services.cache_service import cache_service

router = APIRouter(prefix="/workflow", tags=["workflow"])
_bearer = HTTPBearer(auto_error=True)


def _verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    if not settings.workflow_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow endpoint is disabled (WORKFLOW_API_KEY not configured)",
        )
    if credentials.credentials != settings.workflow_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid workflow API key",
        )


class WorkflowRunResponse(DiagnosisResponse):
    trace_id: str | None = None
    cache_hit: bool = False
    cache_stats: dict | None = None


@router.post("/run", response_model=WorkflowRunResponse, status_code=200)
async def workflow_run(
    payload: CaseRequest,
    _: None = Depends(_verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the full diagnosis pipeline from an external system.

    - Accepts the same CaseRequest body as POST /case
    - Returns a full DiagnosisResponse plus trace_id and cache_hit flag
    - Every call is visible in Okahu Cloud as a traced workflow execution
    """
    case_id = payload.case_id or uuid.uuid4()

    # Check case cache before persisting to DB
    cache_key = cache_service.case_key(
        payload.symptoms,
        payload.vitals.model_dump(),
        payload.labs or {},
    )
    cached = cache_service.get_case(cache_key)
    if cached is not None:
        return WorkflowRunResponse(
            **cached,
            cache_hit=True,
            cache_stats=cache_service.stats(),
        )

    case_record = Case(
        id=case_id,
        symptoms={"items": payload.symptoms},
        vitals=payload.vitals.model_dump(exclude_none=True),
        history=payload.history.model_dump(exclude_none=True),
        labs=payload.labs,
    )
    db.add(case_record)
    await db.commit()

    try:
        result = await pipeline.run(
            db=db,
            case=payload,
            case_id=case_id,
            source="workflow",
        )
    except RetryError as exc:
        raise HTTPException(status_code=500, detail="Pipeline failed after retries") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc

    return WorkflowRunResponse(
        **result.model_dump(),
        cache_hit=False,
        cache_stats=cache_service.stats(),
    )


@router.get("/cache/stats")
async def cache_stats(_: None = Depends(_verify_api_key)):
    """Return current cache utilisation stats."""
    return cache_service.stats()


@router.delete("/cache", status_code=204)
async def clear_cache(_: None = Depends(_verify_api_key)):
    """Flush both caches (e.g. after bulk document import)."""
    cache_service._case_cache.clear()
    cache_service._llm_cache.clear()
