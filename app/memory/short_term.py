"""Memória de curto prazo: janela deslizante + resumo incremental da sessão.

- **Janela:** as últimas ``MEMORY_WINDOW_MESSAGES`` mensagens (padrão 10) vão literais ao modelo.
- **Resumo:** quando há pelo menos ``SUMMARY_TRIGGER_MESSAGES`` mensagens fora da janela ainda não
  resumidas, elas são condensadas junto com o resumo anterior (versão otimista no SQLite).

O que precisa sobreviver ao resumo NÃO depende do LLM: entidades, correções e resultados
anteriores (id da análise + horário dos dados) são extraídos dos metadados das mensagens de
forma determinística e acumulados em ``details``. O LLM só redige o texto corrido; se falhar,
um texto determinístico é usado.
"""

import json
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field, ValidationError

from app.config import ConversationSettings
from app.persistence.sqlite_store import ConversationStore, StaleSummaryError, StoredMessage, StoredSummary
from app.services.llm import with_transient_retry

logger = logging.getLogger(__name__)

DETAIL_KEYS = ("entities", "corrections", "constraints", "previous_results", "open_questions")
DETAIL_LIMIT = 20


class SummaryText(BaseModel):
    summary: str = Field(min_length=1)
    constraints: list[str] = []
    open_questions: list[str] = []


SUMMARY_PROMPT = """Você resume uma conversa sobre criptoativos para uso futuro do próprio assistente.
Responda SOMENTE com JSON: {"summary": "texto em português, até 6 frases", "constraints": ["restrições ou preferências
analíticas declaradas pelo usuário"], "open_questions": ["perguntas do usuário ainda não respondidas"]}.
Preserve: ativos, períodos, correções do usuário e resultados citados (com o horário dos dados).
Valores de mercado do histórico são ANTIGOS: se citar algum, diga de quando é. Não invente nada."""


def window(store: ConversationStore, session_id: str, size: int) -> list[StoredMessage]:
    return store.list_messages(session_id, last=size)


def _merge(old: list[str], new: list[str]) -> list[str]:
    return list(dict.fromkeys([*old, *new]))[-DETAIL_LIMIT:]


def deterministic_details(messages: list[StoredMessage]) -> dict[str, list[str]]:
    """Fatos que precisam sobreviver ao resumo, extraídos dos metadados (sem LLM)."""
    details: dict[str, list[str]] = {key: [] for key in DETAIL_KEYS}
    for message in messages:
        meta = message.metadata
        details["entities"] += meta.get("assets", []) + meta.get("protocols", [])
        if message.role == "user" and meta.get("is_correction"):
            details["corrections"].append(f"msg {message.seq}: {message.content[:160]}")
        if message.role == "assistant" and meta.get("analysis_id"):
            stamp = meta.get("data_retrieved_at") or message.created_at.isoformat(timespec="minutes")
            assets = ", ".join(meta.get("assets", [])) or "sem ativos"
            details["previous_results"].append(
                f"análise {meta['analysis_id']} ({meta.get('intent')}: {assets}; dados de {stamp})"
            )
        if message.role == "assistant" and meta.get("clarification"):
            details["open_questions"].append(message.content[:160])
    return {key: list(dict.fromkeys(values)) for key, values in details.items()}


def _fallback_text(previous: StoredSummary | None, messages: list[StoredMessage]) -> str:
    asks = [m.content[:120] for m in messages if m.role == "user"]
    prefix = previous.summary + " " if previous else ""
    return (prefix + "Pedidos anteriores do usuário: " + " | ".join(asks)).strip()


class ConversationSummarizer:
    def __init__(self, llm: BaseChatModel | None, settings: ConversationSettings) -> None:
        self.llm = llm
        self.settings = settings

    def pending(self, store: ConversationStore, session_id: str) -> tuple[StoredSummary | None, list[StoredMessage]]:
        """Mensagens fora da janela ainda não resumidas."""
        summary = store.get_summary(session_id)
        covered = summary.covers_until_seq if summary else 0
        messages = store.list_messages(session_id, after_seq=covered)
        outside = messages[: max(0, len(messages) - self.settings.window_messages)]
        return summary, outside

    def maybe_update(self, store: ConversationStore, session_id: str) -> StoredSummary | None:
        summary, outside = self.pending(store, session_id)
        if len(outside) < self.settings.summary_trigger_messages:
            return None
        return self.update(store, session_id, summary, outside)

    def update(
        self,
        store: ConversationStore,
        session_id: str,
        previous: StoredSummary | None,
        messages: list[StoredMessage],
    ) -> StoredSummary | None:
        details = {key: list((previous.details if previous else {}).get(key, [])) for key in DETAIL_KEYS}
        for key, values in deterministic_details(messages).items():
            details[key] = _merge(details[key], values)

        text, generated = None, None
        if self.llm is not None:
            transcript = "\n".join(f"[{m.seq}] {m.role}: {m.content}" for m in messages)
            human = (
                f"RESUMO ANTERIOR:\n{previous.summary if previous else '(nenhum)'}\n\n"
                f"FATOS JÁ REGISTRADOS:\n{json.dumps(details, ensure_ascii=False)}\n\nNOVAS MENSAGENS:\n{transcript}"
            )
            try:
                reply = with_transient_retry(self.llm).invoke([SystemMessage(content=SUMMARY_PROMPT), HumanMessage(content=human)])
                generated = SummaryText.model_validate(JsonOutputParser().invoke(reply))
                text = generated.summary
            except (ValidationError, ValueError) as exc:
                logger.warning("summary llm invalid, using deterministic fallback: %s", exc)
        if generated:
            details["constraints"] = _merge(details["constraints"], generated.constraints)
            details["open_questions"] = _merge(details["open_questions"], generated.open_questions)
        text = (text or _fallback_text(previous, messages))[: self.settings.summary_max_chars]

        try:
            return store.save_summary(
                session_id, text, details, covers_until_seq=messages[-1].seq,
                expected_version=previous.version if previous else 0,
            )
        except StaleSummaryError:
            # Outra requisição atualizou antes; ela já cobriu (ou cobrirá) estas mensagens.
            logger.info("summary update skipped (stale version) session=%s", session_id)
            return None


def render_summary(summary: StoredSummary | None) -> str:
    if summary is None:
        return ""
    parts = [f"Resumo (até a mensagem {summary.covers_until_seq}): {summary.summary}"]
    labels = {
        "entities": "Entidades", "corrections": "Correções do usuário", "constraints": "Restrições",
        "previous_results": "Resultados anteriores (HISTÓRICOS)", "open_questions": "Perguntas em aberto",
    }
    for key, label in labels.items():
        if values := summary.details.get(key):
            parts.append(f"{label}: " + "; ".join(values))
    return "\n".join(parts)
