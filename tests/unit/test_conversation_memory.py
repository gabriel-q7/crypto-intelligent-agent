"""Memória curta (janela + resumo), memória longa (política e isolamento) e montagem de contexto."""

from datetime import UTC, datetime

import pytest

from app.config import ConversationSettings
from app.conversation.context_builder import build_context, tokens
from app.memory.long_term import InMemoryBackend, LazyMemoryBackend, MemoryHit, is_rejected_memory
from app.memory.short_term import ConversationSummarizer, deterministic_details, render_summary
from app.persistence.sqlite_store import ConversationStore, StoredMessage
from tests.unit.conversation_fakes import RoutingFakeChat

SETTINGS = ConversationSettings(window_messages=4, summary_trigger_messages=2, context_budget_tokens=400)


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "c.sqlite3")


def fill(store, session_id, n, assistant_meta=None):
    for i in range(1, n + 1):
        role = "user" if i % 2 else "assistant"
        store.append_message(session_id, role, f"mensagem {i}", (assistant_meta or {}) if role == "assistant" else {})


# --------------------------------------------------------------------------- resumo


def test_summary_triggers_only_outside_window(store):
    session = store.create_session()
    summarizer = ConversationSummarizer(RoutingFakeChat(), SETTINGS)
    fill(store, session.id, 5)  # 1 fora da janela (< 2)
    assert summarizer.maybe_update(store, session.id) is None
    fill(store, session.id, 1)  # 2 fora da janela
    summary = summarizer.maybe_update(store, session.id)
    assert summary.covers_until_seq == 2 and summary.version == 1
    assert summarizer.maybe_update(store, session.id) is None  # nada novo para resumir


def test_summary_keeps_previous_results_and_corrections_without_llm(store):
    session = store.create_session()
    store.append_message(session.id, "user", "Analise BTC em 7 dias", {"assets": ["BTC"]})
    store.append_message(session.id, "assistant", "ok", {"analysis_id": "a1", "intent": "market_analysis",
                                                          "assets": ["BTC"], "data_retrieved_at": "2026-10-28T10:00"})
    store.append_message(session.id, "user", "Eu quis dizer 30 dias", {"is_correction": True})
    fill(store, session.id, 4)
    summarizer = ConversationSummarizer(RoutingFakeChat(summary=None), SETTINGS)  # LLM inválido → fallback
    summary = summarizer.maybe_update(store, session.id)
    assert "análise a1 (market_analysis: BTC; dados de 2026-10-28T10:00)" in summary.details["previous_results"]
    assert any("30 dias" in c for c in summary.details["corrections"])
    assert "BTC" in summary.details["entities"]
    assert summary.summary.startswith("Pedidos anteriores")  # texto determinístico
    rendered = render_summary(summary)
    assert "Resultados anteriores (HISTÓRICOS)" in rendered


def test_incremental_summary_accumulates_details(store):
    session = store.create_session()
    summarizer = ConversationSummarizer(RoutingFakeChat(), SETTINGS)
    store.append_message(session.id, "user", "BTC", {"assets": ["BTC"]})
    fill(store, session.id, 5)
    first = summarizer.maybe_update(store, session.id)
    store.append_message(session.id, "user", "ETH", {"assets": ["ETH"]})
    fill(store, session.id, 5)  # empurra "ETH" para fora da janela (4)
    second = summarizer.maybe_update(store, session.id)
    assert second.version == first.version + 1
    assert {"BTC", "ETH"} <= set(second.details["entities"])


def test_deterministic_details_ignore_messages_without_metadata():
    now = datetime.now(UTC)
    details = deterministic_details([StoredMessage("s", 1, "user", "oi", {}, now)])
    assert all(values == [] for values in details.values())


# --------------------------------------------------------------------------- memória longa


@pytest.mark.parametrize(
    "text, rejected",
    [("O preço do ETH é US$ 2.329,72", True), ("BTC subiu 3%", True), ("The assistant acknowledged it", True),
     ("Usuário prefere análises com foco em risco", False), ("Período de análise corrigido para 30 dias", False)],
)
def test_memory_retention_policy(text, rejected):
    assert is_rejected_memory(text) is rejected


def test_backend_isolation_and_delete():
    backend = InMemoryBackend()
    backend.add_episode("A", "Análise BTC ETH comparação", {"category": "episodic", "source_ref": "analysis:1"})
    backend.extract_from_user("B", ["Prefiro foco em risco no ETH", "O ETH custa US$ 2.000"])
    assert [h.text for h in backend.search("A", "comparação BTC", 5)] == ["Análise BTC ETH comparação"]
    assert backend.search("A", "risco ETH", 5) == [] or all(h.metadata.get("source_ref") for h in backend.search("A", "risco ETH", 5))
    assert [h.text for h in backend.list("B")] == ["Prefiro foco em risco no ETH"]  # preço rejeitado
    backend.delete_session("A")
    assert backend.list("A") == [] and len(backend.list("B")) == 1


def test_lazy_backend_retries_creation():
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("ollama fora")
        return InMemoryBackend()

    lazy = LazyMemoryBackend(factory)
    with pytest.raises(ConnectionError):
        lazy.search("s", "x", 1)
    assert lazy.search("s", "x", 1) == [] and len(attempts) == 2


# --------------------------------------------------------------------------- contexto


def message(seq, role, content, **meta):
    return StoredMessage("s", seq, role, content, meta, datetime(2026, 10, 28, 12, tzinfo=UTC))


def test_context_priority_drops_memories_then_oldest_messages():
    recent = [message(i, "user" if i % 2 else "assistant", "texto " * 30) for i in range(1, 7)]
    memories = [MemoryHit(str(i), "memória " * 20, score=s, metadata={"category": "episodic"}) for i, s in [(1, 0.9), (2, 0.5)]]
    context = build_context("pergunta", {"assets": ["BTC"]}, "resumo curto", recent, memories, budget_tokens=320)
    assert context.tokens <= 340  # orçamento + rótulos fixos
    assert context.included["summary"] == 1 and context.included["current_message"] == 1
    assert context.dropped.get("memories", 0) >= 1
    if context.dropped.get("recent_messages"):
        assert "[6 ·" in context.text and "[1 ·" not in context.text  # cortou as mais antigas


def test_context_deduplicates_memories_already_in_window_or_summary():
    recent = [message(1, "user", "Compare BTC e ETH"), message(2, "assistant", "ok", analysis_id="a1")]
    memories = [
        MemoryHit("m1", "Análise a1 de BTC", 0.9, {"source_ref": "analysis:a1", "category": "episodic"}),
        MemoryHit("m2", "Resumo da sessão", 0.9, {"doc_type": "summary"}),
        MemoryHit("m3", "Prefere foco em risco", 0.6, {"category": "semantic"}),
    ]
    context = build_context("e agora?", None, "", recent, memories, budget_tokens=1000)
    assert "Prefere foco em risco" in context.text
    assert "Análise a1 de BTC" not in context.text and "Resumo da sessão" not in context.text
    assert context.memory_refs == ["m3"]


def test_context_marks_history_as_not_current():
    context = build_context("x", None, "resumo", [message(1, "user", "y")], [], 1000)
    assert "HISTÓRICOS" in context.text


def test_token_approximation_is_monotonic():
    assert tokens("") == 0 < tokens("abc") < tokens("abc " * 100)
