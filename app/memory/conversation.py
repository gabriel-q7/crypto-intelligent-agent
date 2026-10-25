"""Memória de conversa de curto prazo, limitada em turnos e em caracteres.

- Turnos além de ``max_turns`` são descartados (os mais antigos primeiro).
- O histórico renderizado nunca passa de ``max_chars`` (cabe na janela de contexto do modelo).
- Respostas com dados de mercado são marcadas com o horário da consulta e com o aviso de que
  são HISTÓRICAS: o agente deve buscar de novo em vez de reapresentá-las como atuais.
"""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

ANSWER_LIMIT = 500
EMPTY_HISTORY = "(sem histórico)"
STALE_WARNING = (
    "Histórico da conversa. Valores de mercado citados aqui são HISTÓRICOS (veja o horário) "
    "e NÃO devem ser apresentados como atuais: consulte as ferramentas novamente."
)


@dataclass(frozen=True)
class ConversationTurn:
    question: str
    answer: str
    at: datetime
    market_data_at: datetime | None = None

    def render(self) -> str:
        answer = self.answer if len(self.answer) <= ANSWER_LIMIT else self.answer[: ANSWER_LIMIT - 1] + "…"
        stamp = self.at.strftime("%Y-%m-%d %H:%M UTC")
        line = f"[{stamp}] Usuário: {self.question}\n[{stamp}] Agente: {answer}"
        if self.market_data_at:
            line += f"\n(dados de mercado desta resposta obtidos em {self.market_data_at.isoformat(timespec='seconds')} — históricos)"
        return line


class BoundedConversationMemory:
    def __init__(self, max_turns: int = 4, max_chars: int = 3000) -> None:
        if max_turns < 1 or max_chars < 200:
            raise ValueError("max_turns >= 1 e max_chars >= 200")
        self.max_chars = max_chars
        self._turns: deque[ConversationTurn] = deque(maxlen=max_turns)

    @property
    def turns(self) -> list[ConversationTurn]:
        return list(self._turns)

    def add(self, question: str, answer: str, market_data_at: datetime | None = None) -> None:
        self._turns.append(ConversationTurn(question, answer, datetime.now(UTC), market_data_at))

    def clear(self) -> None:
        self._turns.clear()

    def render(self) -> str:
        """Turnos mais recentes primeiro até o limite de caracteres, exibidos em ordem cronológica."""
        if not self._turns:
            return EMPTY_HISTORY
        budget = self.max_chars - len(STALE_WARNING) - 1
        selected: list[str] = []
        for turn in reversed(self._turns):
            block = turn.render()
            if len(block) + 2 > budget:
                break
            selected.append(block)
            budget -= len(block) + 2
        if not selected:
            return EMPTY_HISTORY
        return STALE_WARNING + "\n" + "\n\n".join(reversed(selected))
