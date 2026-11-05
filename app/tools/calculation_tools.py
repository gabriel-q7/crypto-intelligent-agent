"""Ferramentas determinísticas (hardcoded): cálculo sem LLM.

``calculate_percent_change`` recebe dois valores e devolve a variação percentual. O agente usa
quando precisa comparar dois números que JÁ vieram de outras ferramentas (ex.: preço atual ×
preço de um snapshot). O resultado entra nas observações, então o valor calculado passa na
verificação de números da resposta final.
"""

import re

from langchain_core.tools import Tool
from pydantic import BaseModel, model_validator

from app.services.grounding import parse_number
from app.tools.market_tools import _error, _ok

TOOL_NAME = "calculate_percent_change"
NUMBER = re.compile(r"-?\d+(?:[.,]\d+)*")


class PercentChangeInput(BaseModel):
    old: float
    new: float

    @model_validator(mode="after")
    def _non_zero_base(self) -> "PercentChangeInput":
        if self.old == 0:
            raise ValueError("o valor inicial não pode ser zero")
        return self

    @classmethod
    def from_text(cls, raw: str) -> "PercentChangeInput":
        """Aceita "100, 110", "de 100 para 110", "old=100 new=110" (pt-BR ou en)."""
        numbers = [parse_number(n) for n in NUMBER.findall(raw or "")]
        numbers = [n for n in numbers if n is not None]
        if len(numbers) != 2:
            raise ValueError(f"informe exatamente dois números (inicial e final); recebido {raw!r}")
        return cls(old=numbers[0], new=numbers[1])


class PercentChangeOutput(BaseModel):
    old: float
    new: float
    absolute_change: float
    percent_change: float


def percent_change(data: PercentChangeInput) -> PercentChangeOutput:
    delta = data.new - data.old
    return PercentChangeOutput(
        old=data.old, new=data.new, absolute_change=delta, percent_change=delta / abs(data.old) * 100
    )


def build_calculation_tools() -> list[Tool]:
    def run(raw: str) -> str:
        try:
            data = PercentChangeInput.from_text(raw)
        except ValueError as exc:
            return _error(TOOL_NAME, "invalid_argument", str(exc).splitlines()[0])
        return _ok(TOOL_NAME, percent_change(data).model_dump(), {"provider": "cálculo local (determinístico)"})

    return [
        Tool(
            name=TOOL_NAME,
            func=run,
            description=(
                "Calcula a variação percentual entre dois valores já obtidos de outras ferramentas. "
                "Entrada: valor inicial e valor final separados por vírgula (ex.: '100, 112.5'). "
                "Não busca dados."
            ),
        )
    ]
