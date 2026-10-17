from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.schemas.question import normalize_assets


class AnalysisIntent(StrEnum):
    PRICE_ANALYSIS = "price_analysis"
    COMPARISON = "comparison"
    TREND_ANALYSIS = "trend_analysis"
    RISK_ASSESSMENT = "risk_assessment"
    FUNDAMENTAL_ANALYSIS = "fundamental_analysis"
    GENERAL_INFORMATION = "general_information"
    OTHER = "other"


class CryptoAnalysisRequest(BaseModel):
    """Plano de análise estruturado produzido pelo LLM (saída da chain).

    Descreve O QUE investigar; não contém dados de mercado.
    """

    intent: AnalysisIntent = Field(description="Intenção principal da pergunta.")
    assets: list[str] = Field(description="Tickers dos ativos envolvidos, ex.: BTC, ETH.")
    requested_metrics: list[str] = Field(
        default_factory=list,
        description="Métricas necessárias para responder, ex.: preço, volume, market cap, TVL.",
    )
    period: str | None = Field(default=None, description="Período de análise ou null se ausente.")
    investigation_steps: list[str] = Field(
        min_length=1, description="Etapas ordenadas para investigar a pergunta."
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Informações que faltam para uma resposta completa."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Premissas adotadas na interpretação."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confiança estimada na interpretação da pergunta (0 a 1)."
    )

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("assets")
    @classmethod
    def _normalize_assets(cls, value: list[str]) -> list[str]:
        return normalize_assets(value)
