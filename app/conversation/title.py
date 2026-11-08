"""Título automático da sessão: gerado pelo modelo após o primeiro turno.

Roda na manutenção em background (mesmo executor do resumo), então não pesa na latência
da resposta. Se o modelo falhar ou devolver algo inútil, o título vira a primeira pergunta
truncada, para a sessão nunca ficar identificada só pelo id.
"""

import json
import logging
import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.persistence.sqlite_store import StoredMessage
from app.services.llm import with_transient_retry

logger = logging.getLogger(__name__)

TITLE_PROMPT = """Você dá títulos curtos a conversas sobre criptoativos.
Gere um título de 3 a 7 palavras, em português, que resuma o tema da conversa.
Responda SOMENTE com JSON: {"title": "título sem aspas internas, sem markdown e sem ponto final"}."""

TITLE_MAX_CHARS = 60
FALLBACK_MAX_CHARS = 50
ANSWER_EXCERPT_CHARS = 600


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,.;:-") + "…"


def clean_title(raw: str) -> str:
    """Aceita ``{"title": ...}`` (LLM em modo JSON) ou texto; devolve a primeira linha útil,
    sem rótulos, aspas, markdown e pontuação final."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        raw = str(parsed.get("title") or parsed.get("titulo") or parsed.get("título") or "")
    elif isinstance(parsed, str):
        raw = parsed
    line = next((stripped for stripped in (ln.strip() for ln in raw.splitlines()) if stripped), "")
    line = re.sub(r"^(t[íi]tulo\s*:\s*)", "", line, flags=re.IGNORECASE)
    line = line.strip(" \t#*_`\"'“”‘’«»").rstrip(" .!;:")
    return _truncate(line, TITLE_MAX_CHARS)


def fallback_title(first_user_message: str) -> str:
    return _truncate(first_user_message, FALLBACK_MAX_CHARS) or "Conversa"


def generate_title(llm: BaseChatModel | None, messages: list[StoredMessage]) -> str:
    """Título a partir da primeira pergunta e da primeira resposta."""
    question = next((m.content for m in messages if m.role == "user"), "")
    answer = next((m.content for m in messages if m.role == "assistant"), "")
    if llm is not None and question:
        human = f"PERGUNTA:\n{question}\n\nRESPOSTA:\n{_truncate(answer, ANSWER_EXCERPT_CHARS)}"
        try:
            reply = with_transient_retry(llm).invoke([SystemMessage(content=TITLE_PROMPT), HumanMessage(content=human)])
            title = clean_title(str(reply.content))
            if len(title) >= 3:
                return title
        except Exception as exc:  # noqa: BLE001 - título é cosmético; cai no fallback
            logger.warning("title llm failed, using fallback: %s", exc)
    return fallback_title(question)
