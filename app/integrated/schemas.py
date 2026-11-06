"""Modelos do agente integrado (Fase 5)."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report import DataSource, Finding
from app.verification.schemas import VerificationResult

ToolCategory = Literal["api", "sql", "rag", "deterministic", "unknown"]


class ToolStep(BaseModel):
    """Um passo da trajetória: o que o agente chamou, com quais argumentos e o que voltou."""

    order: int
    tool: str
    category: ToolCategory
    args: dict = {}
    ok: bool
    error_kind: str | None = None
    error_message: str | None = None
    output: str = Field(default="", description="Observação bruta (JSON), truncada em 4.000 caracteres.")


class AgentMessageRequest(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    include_trace: bool = Field(default=False, description="Inclui a trajetória completa (saídas das ferramentas).")


class AgentReply(BaseModel):
    session_id: str
    user_seq: int
    assistant_seq: int
    answer: str
    status: Literal["complete", "partial", "failed"]
    verification: VerificationResult
    original_answer: str | None = Field(default=None, description="Resposta antes da correção (se houve).")
    tools_used: list[str] = []
    trajectory: list[ToolStep] = []
    findings: list[Finding] = []
    data_sources: list[DataSource] = []
    limitations: list[str] = []
    history_messages: int = Field(default=0, description="Mensagens anteriores entregues ao agente.")
    retrieved_memories: list[str] = []
    analysis_id: str | None = None
    elapsed_s: float = 0.0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
