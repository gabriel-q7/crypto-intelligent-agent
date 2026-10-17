from pydantic import BaseModel, Field, field_validator


def normalize_assets(assets: list[str]) -> list[str]:
    """Normaliza tickers para maiúsculas, remove vazios e duplicados preservando a ordem."""
    seen: dict[str, None] = {}
    for asset in assets:
        symbol = asset.strip().upper()
        if symbol:
            seen.setdefault(symbol, None)
    return list(seen)


class CryptoQuestion(BaseModel):
    """Pergunta do usuário sobre criptomoedas (entrada da chain)."""

    question: str = Field(min_length=3, description="Pergunta em linguagem natural.")
    assets: list[str] = Field(default_factory=list, description="Ativos citados, ex.: BTC, ETH.")
    period: str | None = Field(default=None, description="Período de análise, ex.: 'últimos 30 dias'.")
    context: str | None = Field(default=None, description="Contexto adicional opcional.")

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("a pergunta deve ter ao menos 3 caracteres")
        return value

    @field_validator("assets")
    @classmethod
    def _normalize_assets(cls, value: list[str]) -> list[str]:
        return normalize_assets(value)
