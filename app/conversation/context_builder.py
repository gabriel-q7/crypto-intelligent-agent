"""Montagem do contexto da conversa com orçamento de tokens e prioridade determinística.

Seções, da mais para a menos prioritária:
1. mensagem atual (nunca cortada);
2. contexto ativo (pedido em andamento);
3. resumo da sessão;
4. mensagens recentes (a janela; ao cortar, sai a mais ANTIGA primeiro);
5. memórias recuperadas (ao cortar, sai a de MENOR score primeiro).

Instruções de sistema e dados atuais não entram aqui: ficam nos prompts das chains, que já
os incluem com prioridade máxima. O orçamento (``CONTEXT_BUDGET_TOKENS``) cobre apenas o
contexto conversacional que é somado a eles, dentro de ``OLLAMA_NUM_CTX``.

Tokens: ``count_tokens_approximately`` do langchain-core (≈ 4 caracteres por token). O
Ollama não expõe o tokenizer; a aproximação é conservadora o bastante com o orçamento
bem abaixo da janela do modelo.

Sem duplicação: memórias episódicas cujo ``source_ref`` já aparece na janela ou no resumo,
e o próprio resumo indexado como memória, são descartados.
"""

import json
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

from app.memory.long_term import MemoryHit
from app.persistence.sqlite_store import StoredMessage

HISTORY_NOTE = "Valores de mercado citados abaixo são HISTÓRICOS (veja o horário); não são dados atuais."


def tokens(text: str) -> int:
    return count_tokens_approximately([HumanMessage(content=text)]) if text else 0


@dataclass
class AssembledContext:
    text: str
    tokens: int
    included: dict[str, int] = field(default_factory=dict)  # seção -> itens incluídos
    dropped: dict[str, int] = field(default_factory=dict)  # seção -> itens cortados pelo orçamento
    memory_refs: list[str] = field(default_factory=list)


def _render_message(message: StoredMessage) -> str:
    stamp = message.created_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"[{message.seq} · {stamp}] {message.role}: {message.content}"


def _referenced(messages: list[StoredMessage], summary_text: str) -> set[str]:
    refs = {f"analysis:{m.metadata['analysis_id']}" for m in messages if m.metadata.get("analysis_id")}
    return refs | {ref for ref in refs if ref.split(":", 1)[1] in summary_text}


def build_context(
    current_message: str,
    active_context: dict | None,
    summary_text: str,
    recent: list[StoredMessage],
    memories: list[MemoryHit],
    budget_tokens: int,
) -> AssembledContext:
    # Mensagem atual sai da janela (ela já é enviada como pergunta).
    recent = [m for m in recent if not (m.role == "user" and m.content == current_message and m is recent[-1])]
    seen_refs = _referenced(recent, summary_text)
    memories = [
        m for m in memories
        if m.metadata.get("doc_type") != "summary" and m.metadata.get("source_ref") not in seen_refs
    ]
    memories = sorted(memories, key=lambda m: m.score, reverse=True)

    fixed = [f"MENSAGEM ATUAL: {current_message}"]
    if active_context:
        fixed.append("PEDIDO EM ANDAMENTO: " + json.dumps(active_context, ensure_ascii=False))
    used = sum(tokens(t) for t in fixed)
    included = {"current_message": 1, "active_context": int(bool(active_context))}
    dropped: dict[str, int] = {}

    def fits(text: str) -> bool:
        return used + tokens(text) <= budget_tokens

    sections: list[str] = list(fixed)
    if summary_text:
        if fits(summary_text):
            sections.append("RESUMO DA SESSÃO:\n" + summary_text)
            used += tokens(summary_text)
            included["summary"] = 1
        else:
            dropped["summary"] = 1

    # Janela: mantém as mais recentes que couberem.
    kept_messages: list[str] = []
    for message in reversed(recent):
        line = _render_message(message)
        if fits(line):
            kept_messages.insert(0, line)
            used += tokens(line)
        else:
            dropped["recent_messages"] = dropped.get("recent_messages", 0) + 1
    if kept_messages:
        sections.append("MENSAGENS RECENTES:\n" + "\n".join(kept_messages))
    included["recent_messages"] = len(kept_messages)

    kept_memories: list[str] = []
    refs: list[str] = []
    for memory in memories:
        line = f"- ({memory.metadata.get('category', 'memória')}) {memory.text}"
        if fits(line):
            kept_memories.append(line)
            refs.append(memory.metadata.get("source_ref") or memory.id)
            used += tokens(line)
        else:
            dropped["memories"] = dropped.get("memories", 0) + 1
    if kept_memories:
        sections.append("MEMÓRIAS RELEVANTES DA SESSÃO:\n" + "\n".join(kept_memories))
    included["memories"] = len(kept_memories)

    if len(sections) > len(fixed):
        sections.insert(len(fixed), HISTORY_NOTE)
    text = "\n\n".join(sections)
    return AssembledContext(text=text, tokens=tokens(text), included=included, dropped=dropped, memory_refs=refs)
