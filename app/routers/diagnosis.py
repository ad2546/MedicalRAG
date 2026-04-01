"""GET /diagnosis/{case_id} — retrieve stored diagnosis output for a case."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.db_models import DiagnosisOutput, User
from app.models.schemas import DiagnosisEntry, DiagnosisResponse

router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])

DISCLAIMER = "Not a medical diagnosis; consult a clinician before making any clinical decisions."


@router.get("/{case_id}", response_model=DiagnosisResponse)
async def get_diagnosis(
    case_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return initial, reflection, and final diagnosis for an existing case."""
    result = await db.execute(
        select(DiagnosisOutput)
        .where(DiagnosisOutput.case_id == case_id)
        .order_by(DiagnosisOutput.created_at)
    )
    outputs = result.scalars().all()

    if not outputs:
        raise HTTPException(status_code=404, detail=f"No diagnosis found for case {case_id}")

    stage_map: dict[str, list[DiagnosisOutput]] = {"initial": [], "reflection": [], "final": []}
    for row in outputs:
        if row.stage in stage_map:
            stage_map[row.stage].append(row)

    def _entries(rows: list[DiagnosisOutput]) -> list[DiagnosisEntry]:
        if not rows:
            return []
        latest = rows[-1]
        return [
            DiagnosisEntry(
                condition=d["condition"],
                confidence=d["confidence"],
                evidence_ids=[uuid.UUID(eid) for eid in d.get("evidence_ids", [])],
                reasoning=d.get("reasoning"),
            )
            for d in latest.diagnosis.get("diagnoses", [])
        ]

    return DiagnosisResponse(
        case_id=case_id,
        initial_diagnosis=_entries(stage_map["initial"]),
        reflection_diagnosis=_entries(stage_map["reflection"]),
        final_diagnosis=_entries(stage_map["final"]),
        disclaimer=DISCLAIMER,
    )
