"""POST /case — submit a new patient case and kick off the pipeline."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.auth import UserQuota, consume_user_request_quota
from app.database import get_db
from app.models.db_models import Case
from app.models.schemas import CaseRequest, CaseResponse, DiagnosisResponse
from app.pipeline import pipeline

router = APIRouter(prefix="/case", tags=["cases"])


@router.post("", response_model=DiagnosisResponse, status_code=201)
async def submit_case(
    payload: CaseRequest,
    quota: UserQuota = Depends(consume_user_request_quota),
    db: AsyncSession = Depends(get_db),
):
    """Submit a new patient case; runs the full RAG pipeline synchronously."""
    case_id = payload.case_id or uuid.uuid4()

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
        result = await pipeline.run(db=db, case=payload, case_id=case_id)
    except RetryError as exc:
        raise HTTPException(status_code=500, detail="Pipeline failed after retries") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc

    return result.model_copy(update={"remaining_requests": quota.remaining_requests})
