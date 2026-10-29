"""Schemas da interpretação de mensagens na conversa (Fase 3)."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.question import normalize_assets


class ConversationIntent(StrEnum):
    MARKET_ANALYSIS = "market_analysis"
    DEFI_ANALYSIS = "defi_analysis"
    ASSET_COMPARISON = "asset_comparison"
    HISTORICAL_ANALYSIS = "historical_analysis"
    FOLLOW_UP = "follow_up"
    PREVIOUS_RESULT_REFERENCE = "previous_result_reference"
    GENERAL_RESEARCH = "general_research"


# Intenções que podem ser executadas; follow_up e previous_result_reference
# são resolvidas para uma destas (ou para a comparação com resultado anterior).
EXECUTABLE_INTENTS = {
    ConversationIntent.MARKET_ANALYSIS,
    ConversationIntent.DEFI_ANALYSIS,
    ConversationIntent.ASSET_COMPARISON,
    ConversationIntent.HISTORICAL_ANALYSIS,
    ConversationIntent.GENERAL_RESEARCH,
}

ContextSource = Literal["current_message", "active_context", "recent_history", "summary", "semantic_memory"]


class ExtractedRequest(BaseModel):
    """O que a mensagem atual pede, antes de combinar com o contexto da sessão."""

    intent: ConversationIntent = ConversationIntent.GENERAL_RESEARCH
    assets: list[str] = Field(default=[], max_length=5)
    protocols: list[str] = []
    networks: list[str] = []
    metrics: list[str] = []
    period: str | None = None
    period_days: int | None = Field(default=None, ge=1, le=3650)
    referenced_analysis_id: str | None = None
    is_correction: bool = False
    # A mensagem retoma as entidades em foco por referência ("eles", "os dois", "them").
    refers_to_active: bool = False
    requires_external_data: bool = False
    missing_information: list[str] = []
    assumptions: list[str] = []

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("assets")
    @classmethod
    def _normalize_assets(cls, value: list[str]) -> list[str]:
        return normalize_assets(value)


class ResolvedRequest(BaseModel):
    """Pedido final após aplicar o contexto ativo, correções e referências."""

    intent: ConversationIntent
    # Intenção que será executada (follow_up herda a do contexto ativo).
    effective_intent: ConversationIntent
    assets: list[str] = []
    protocols: list[str] = []
    networks: list[str] = []
    metrics: list[str] = []
    period: str | None = None
    period_days: int | None = None
    referenced_analysis_id: str | None = None
    requires_external_data: bool = False
    missing_information: list[str] = []
    assumptions: list[str] = []
    # Origem de cada campo resolvido (ex.: {"assets": "active_context", "period": "current_message"}).
    context_sources: dict[str, ContextSource] = {}
    clarification_question: str | None = None
    is_correction: bool = False
    new_topic: bool = False
