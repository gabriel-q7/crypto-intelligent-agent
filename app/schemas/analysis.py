import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.question import normalize_assets


class AnalysisIntent(StrEnum):
    PRICE_ANALYSIS = "price_analysis"
    COMPARISON = "comparison"
    TREND_ANALYSIS = "trend_analysis"
    RISK_ASSESSMENT = "risk_assessment"
    FUNDAMENTAL_ANALYSIS = "fundamental_analysis"
    GENERAL_INFORMATION = "general_information"
    OTHER = "other"


class AnswerStatus(StrEnum):
    # Pergunta conceitual respondida com conhecimento geral estável.
    ANSWERED = "answered"
    # A resposta depende de dados de mercado que ainda não são consultados.
    REQUIRES_MARKET_DATA = "requires_market_data"
    # Pergunta ambígua demais para responder ou planejar.
    NEEDS_CLARIFICATION = "needs_clarification"


# Valores monetários (US$ 100, 100 USD, 2 milhões de dólares) e percentuais (4%, 3,5 %).
# Heurística, não garantia: remove termos como "ataque de 51%" (falso positivo) e deixa passar
# "65 mil" sem moeda (falso negativo).
MARKET_FIGURE_PATTERN = re.compile(
    r"(?:US\$|R\$|\$|€|£)\s?\d"
    r"|\d[\d.,]*\s*(?:(?:mil|milhões|milhão|bilhões|bilhão|trilhões|trilhão)\s+(?:de\s+)?)?"
    r"(?:USD|BRL|EUR|dólares|reais)\b"
    r"|\d+(?:[.,]\d+)?\s?%",
    re.IGNORECASE,
)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

MARKET_DATA_NOTICE = (
    "Nesta fase não tenho acesso a dados de mercado atualizados e respondo apenas com o "
    "conhecimento do modelo, por isso não informo preços, rendimentos ou variações."
)


def strip_market_figures(text: str) -> tuple[str, list[str]]:
    """Remove frases com valores de mercado e acrescenta ``MARKET_DATA_NOTICE``.

    Retorna o texto resultante e os valores encontrados (lista vazia se nada mudou).
    """
    kept, found = [], []
    for sentence in SENTENCE_BOUNDARY.split(text.strip()):
        if match := MARKET_FIGURE_PATTERN.search(sentence):
            found.append(match.group())
        else:
            kept.append(sentence)
    if not found:
        return text.strip(), []
    return " ".join([*kept, MARKET_DATA_NOTICE]), found


class CryptoAnalysisRequest(BaseModel):
    """Saída da chain: plano de análise + resposta mínima.

    Não contém dados de mercado: perguntas que dependem deles recebem
    ``answer_status=requires_market_data`` e uma resposta sem números.
    """

    intent: AnalysisIntent = Field(description="Intenção principal da pergunta.")
    # Logo após intent: o modelo gera o JSON em ordem, e as etapas dependem deste campo.
    answer_status: AnswerStatus = Field(description="Tipo de resposta dada em 'answer'.")
    assets: list[str] = Field(description="Tickers dos ativos envolvidos, ex.: BTC, ETH.")
    requested_metrics: list[str] = Field(
        default_factory=list,
        description="Métricas necessárias para responder, ex.: preço, volume, market cap, TVL.",
    )
    period: str | None = Field(default=None, description="Período de análise ou null se ausente.")
    investigation_steps: list[str] = Field(
        default_factory=list,
        description="Etapas ordenadas para investigar a pergunta (vazia se já respondida).",
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Informações que faltam para uma resposta completa."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Premissas adotadas na interpretação."
    )
    answer: str = Field(
        min_length=1,
        description="Resposta mínima ao usuário, em 2 a 4 frases, sem valores de mercado.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confiança estimada na interpretação da pergunta (0 a 1)."
    )

    @field_validator("intent", "answer_status", mode="before")
    @classmethod
    def _normalize_enum(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("assets")
    @classmethod
    def _normalize_assets(cls, value: list[str]) -> list[str]:
        return normalize_assets(value)

    @model_validator(mode="after")
    def _remove_market_figures_from_answer(self) -> "CryptoAnalysisRequest":
        # O modelo não tem dados atuais: qualquer valor seria desatualizado ou alucinado.
        self.answer, found = strip_market_figures(self.answer)
        if found:
            self.assumptions.append(
                f"valores de mercado removidos da resposta por falta de dados atualizados: "
                f"{', '.join(found)}"
            )
        return self

    @model_validator(mode="after")
    def _steps_required_unless_answered(self) -> "CryptoAnalysisRequest":
        if self.answer_status is AnswerStatus.ANSWERED or self.investigation_steps:
            return self
        if not self.requested_metrics:
            raise ValueError("investigation_steps é obrigatório quando a pergunta não foi respondida")
        # Omissão comum do modelo em perguntas simples ("qual o preço atual?"): a etapa de coleta
        # é derivável das métricas e dos ativos, sem inventar nada.
        assets = ", ".join(self.assets) or "ativos citados"
        self.investigation_steps = [f"Obter {', '.join(self.requested_metrics)} de {assets}"]
        self.assumptions.append("etapa de investigação derivada automaticamente de requested_metrics")
        return self
