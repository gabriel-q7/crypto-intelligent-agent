"""Modelos de requisição e resposta da API HTTP."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import CryptoAnalysisRequest, CryptoQuestion
from app.schemas.report import CryptoReport

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class AnalyzeRequest(CryptoQuestion):
    composition: Literal["lcel", "imperative"] = "lcel"


class BatchAnalyzeRequest(BaseModel):
    questions: list[CryptoQuestion] = Field(min_length=1, max_length=10)


class BatchItem(BaseModel):
    ok: bool
    result: CryptoAnalysisRequest | None = None
    error: str | None = None


class AgentRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    session_id: str | None = Field(
        default=None,
        pattern=SESSION_ID_PATTERN,
        description="Mesma sessão = memória de conversa compartilhada (limitada). Omitido = sessão nova.",
    )
    include_trace: bool = False


class RouteRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    assets: list[str] = Field(default=[], max_length=5, description="Ativos explícitos (pula a extração).")
    router: Literal["modern", "legacy"] = "modern"
    include_trace: bool = False


class ReportResponse(BaseModel):
    report: CryptoReport
    session_id: str | None = None
    trace: list[dict] | None = None


class KnowledgeHitResponse(BaseModel):
    source: str
    title: str
    score: float
    content: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    chat_model: str
    embedding_model: str
    ollama_reachable: bool
    models_available: dict[str, bool]
    knowledge_index: bool
    active_sessions: int
