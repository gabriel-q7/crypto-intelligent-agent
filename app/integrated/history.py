"""Adaptador de histórico persistente para o agente integrado (substitui ``RunnableWithMessageHistory``).

Fronteira de responsabilidades:
- **SQLite da Fase 3 (``ConversationStore``)**: única fonte da verdade das mensagens. Quem grava é
  o ``IntegratedAgentService``, uma vez por turno (pergunta e resposta).
- **Mem0 (``MemoryBackend``)**: memórias episódicas e semânticas da sessão (busca filtrada por sessão).
- **Este adaptador**: só LÊ. Monta as mensagens LangChain que o agente recebe: um bloco de
  contexto (resumo da sessão + memórias recuperadas) e a janela recente (Human/AI), sem a
  mensagem atual. Não grava nada, então não há como duplicar mensagens.

``RunnableWithMessageHistory`` e ``BaseChatMessageHistory`` estão depreciados no langchain-core
1.7.6 (verificado em 2026-11-05) e não são usados; o checkpointer do LangGraph também não, para
não criar uma segunda persistência da conversa.
"""

import logging
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.config import ConversationSettings
from app.memory.long_term import MemoryBackend
from app.memory.short_term import render_summary
from app.persistence.sqlite_store import ConversationStore

logger = logging.getLogger(__name__)


@dataclass
class HistoryBundle:
    messages: list[BaseMessage]
    window_messages: int = 0
    memory_refs: list[str] = field(default_factory=list)
    has_summary: bool = False
    note: str | None = None


class SessionHistoryAdapter:
    def __init__(self, store: ConversationStore, memory: MemoryBackend, settings: ConversationSettings) -> None:
        self.store, self.memory, self.settings = store, memory, settings

    def load(self, session_id: str, query: str, before_seq: int | None = None) -> HistoryBundle:
        """Histórico até ``before_seq`` (exclusivo): normalmente a mensagem do usuário recém-gravada."""
        recent = self.store.list_messages(session_id, last=self.settings.window_messages + 1)
        if before_seq is not None:
            recent = [m for m in recent if m.seq < before_seq]
        recent = recent[-self.settings.window_messages:]

        context_parts: list[str] = []
        summary = self.store.get_summary(session_id)
        summary_text = render_summary(summary) if summary else ""
        if summary_text:
            context_parts.append(f"Resumo das mensagens anteriores desta conversa:\n{summary_text}")
        refs, note = [], None
        try:
            hits = self.memory.search(session_id, query, self.settings.memory_top_k)
        except Exception as exc:  # noqa: BLE001 - memória longa é auxiliar
            logger.warning("memory recall failed session=%s: %s", session_id, exc)
            hits, note = [], f"memória de longo prazo indisponível neste turno ({type(exc).__name__})"
        window_text = " ".join(m.content for m in recent)
        hits = [h for h in hits if h.text[:80] not in window_text]  # já presente na janela
        if hits:
            refs = [h.metadata.get("source_ref") or h.id for h in hits]
            lines = "\n".join(f"- {h.text[:300]}" for h in hits)
            context_parts.append(f"Memórias desta conversa (podem conter dados ANTIGOS; não são atuais):\n{lines}")

        messages: list[BaseMessage] = []
        if context_parts:
            messages.append(SystemMessage(content="\n\n".join(context_parts)))
        for m in recent:
            messages.append(HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content))
        return HistoryBundle(messages=messages, window_messages=len(recent), memory_refs=refs,
                             has_summary=bool(summary_text), note=note)
