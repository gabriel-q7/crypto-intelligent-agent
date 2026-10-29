"""Modelos de requisição e resposta da conversa (Fase 3)."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.intent.schemas import ConversationIntent
from app.schemas.report import CryptoReport, DataSource

SESSION_ID_PATTERN = r"^[a-f0-9]{32}$"

TurnStatus = Literal["complete", "partial", "failed", "unavailable", "needs_clarification"]


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class SessionInfo(BaseModel):
    session_id: str
    title: str | None
    created_at: datetime
    last_activity_at: datetime
    message_count: int
    active_context: dict


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=2, max_length=1000)


class MessageView(BaseModel):
    seq: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    intent: str | None = None
    analysis_id: str | None = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageView]
    total: int


class SummaryResponse(BaseModel):
    session_id: str
    summary: str | None
    details: dict = {}
    covers_until_seq: int = 0
    version: int = 0
    updated_at: datetime | None = None


class ResolvedEntities(BaseModel):
    assets: list[str] = []
    protocols: list[str] = []
    networks: list[str] = []
    metrics: list[str] = []
    period: str | None = None
    period_days: int | None = None


class ContextUsage(BaseModel):
    tokens: int
    budget_tokens: int
    included: dict[str, int]
    dropped: dict[str, int]


class ConversationReply(BaseModel):
    """Resposta de um turno: texto ao usuário + o que foi entendido + evidências."""

    session_id: str
    user_seq: int
    assistant_seq: int
    answer: str
    status: TurnStatus
    intent: ConversationIntent
    effective_intent: ConversationIntent
    entities: ResolvedEntities
    # De onde veio cada entidade: mensagem atual ou contexto ativo da sessão.
    context_sources: dict[str, str] = {}
    referenced_analyses: list[str] = []
    retrieved_memories: list[str] = []
    data_sources: list[DataSource] = []
    missing_information: list[str] = []
    limitations: list[str] = []
    clarification_needed: bool = False
    analysis_id: str | None = None
    report: CryptoReport | None = None
    context_usage: ContextUsage | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
