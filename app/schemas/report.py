"""Saída validada da Fase 2 (agente ReAct e chains roteadas)."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReportStatus(StrEnum):
    # Todos os dados essenciais obtidos e resposta verificada.
    COMPLETE = "complete"
    # Parte dos dados falhou; a resposta diz o que faltou.
    PARTIAL = "partial"
    # Dados essenciais não obtidos: não há análise, só a explicação da falha.
    FAILED = "failed"
    # Capacidade fora do escopo desta fase (ex.: DeFi via DeFiLlama).
    UNAVAILABLE = "unavailable"


class DataSource(BaseModel):
    provider: str
    tool: str
    endpoint: str | None = None
    source_updated_at: datetime | None = Field(
        default=None, description="Horário de atualização informado pela fonte."
    )
    retrieved_at: datetime | None = Field(default=None, description="Horário da consulta.")


class Finding(BaseModel):
    """Fato extraído diretamente de uma observação de ferramenta (não gerado pelo LLM)."""

    statement: str
    source_tool: str
    source_updated_at: datetime | None = None
    retrieved_at: datetime | None = None


class ToolCall(BaseModel):
    tool: str
    input: str
    ok: bool
    error_kind: str | None = None
    error_message: str | None = None


class CryptoReport(BaseModel):
    question: str
    mode: str = Field(description="'react_agent' ou 'route:<rota>'.")
    status: ReportStatus
    answer: str = Field(min_length=1)
    key_points: list[str] = Field(default=[], description="Pontos do LLM já verificados contra os dados.")
    findings: list[Finding] = []
    data_sources: list[DataSource] = []
    uncertainties: list[str] = []
    tool_calls: list[ToolCall] = []
    decision_summary: list[str] = Field(
        default=[], description="Resumo das decisões (ferramentas escolhidas), sem raciocínio privado."
    )
    model: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
