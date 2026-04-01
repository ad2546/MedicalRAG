import uuid
from typing import Any

from pydantic import BaseModel, Field


# ── Case input ──────────────────────────────────────────────────────────────

class VitalsSchema(BaseModel):
    bp: str | None = None
    hr: float | None = None
    temp: float | None = None


class HistorySchema(BaseModel):
    smoker: bool | None = None
    prior_conditions: list[str] = Field(default_factory=list)


class CaseRequest(BaseModel):
    case_id: uuid.UUID | None = None  # optional; server generates one if missing
    symptoms: list[str]
    vitals: VitalsSchema = Field(default_factory=VitalsSchema)
    history: HistorySchema = Field(default_factory=HistorySchema)
    labs: dict[str, Any] = Field(default_factory=dict)


class CaseResponse(BaseModel):
    case_id: uuid.UUID
    message: str = "Case submitted successfully"


# ── Diagnosis output ─────────────────────────────────────────────────────────

ConfidenceLevel = str  # "low" | "medium" | "high"


class DiagnosisEntry(BaseModel):
    condition: str
    confidence: ConfidenceLevel
    evidence_ids: list[uuid.UUID]
    reasoning: str | None = None


class DiagnosisResponse(BaseModel):
    case_id: uuid.UUID
    initial_diagnosis: list[DiagnosisEntry]
    reflection_diagnosis: list[DiagnosisEntry]
    final_diagnosis: list[DiagnosisEntry]
    disclaimer: str = "Not a medical diagnosis; consult a clinician before making any clinical decisions."
    remaining_requests: int | None = None


# ── Internal pipeline types ──────────────────────────────────────────────────

class RetrievedDocument(BaseModel):
    id: uuid.UUID
    content: str
    source: str | None
    disease_category: str | None
    evidence_type: str | None
    score: float


class DiagnosisStageResult(BaseModel):
    stage: str
    diagnoses: list[DiagnosisEntry]
    reasoning: str
    evidence_ids: list[uuid.UUID]
    needs_reretrival: bool = False
    missing_evidence_hint: str | None = None


# ── Document citation ─────────────────────────────────────────────────────────

class DocumentCitation(BaseModel):
    id: uuid.UUID
    content: str
    source: str | None
    disease_category: str | None
    evidence_type: str | None


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str
    usage: dict[str, int]
    remaining_requests: int | None = None


# ── Auth ──────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserInfoResponse(BaseModel):
    id: uuid.UUID
    email: str
    request_limit: int
    requests_used: int
    remaining_requests: int


class AuthResponse(BaseModel):
    message: str
    user: UserInfoResponse
