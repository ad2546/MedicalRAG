"""GET /documents/{doc_id} — fetch a single document for citation display."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.db_models import Document, User
from app.models.schemas import DocumentCitation

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{doc_id}", response_model=DocumentCitation)
async def get_document(
    doc_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return document content and metadata for citation rendering."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return DocumentCitation(
        id=doc.id,
        content=doc.content,
        source=doc.source,
        disease_category=doc.disease_category,
        evidence_type=doc.evidence_type,
    )
