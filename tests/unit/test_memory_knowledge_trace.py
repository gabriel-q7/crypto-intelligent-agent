import json
import logging
from datetime import UTC, datetime

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.tools import Tool

from app.config import KnowledgeSettings
from app.knowledge.store import KnowledgeIndexError, build_index, load_index, search
from app.memory.conversation import EMPTY_HISTORY, STALE_WARNING, BoundedConversationMemory
from app.observability.callbacks import TraceCallbackHandler
from app.observability.logging import SecretRedactionFilter
from app.tools.knowledge_tool import build_knowledge_tool

# --------------------------------------------------------------------------- memória


def test_memory_is_bounded_by_turns():
    memory = BoundedConversationMemory(max_turns=2, max_chars=5000)
    for i in range(5):
        memory.add(f"pergunta {i}", f"resposta {i}")
    assert [t.question for t in memory.turns] == ["pergunta 3", "pergunta 4"]


def test_memory_render_respects_char_limit_and_keeps_newest():
    memory = BoundedConversationMemory(max_turns=10, max_chars=600)
    for i in range(10):
        memory.add(f"pergunta {i}", "x" * 150)
    rendered = memory.render()
    assert len(rendered) <= 600
    assert "pergunta 9" in rendered and "pergunta 0" not in rendered


def test_memory_marks_market_data_as_historical():
    memory = BoundedConversationMemory()
    memory.add("preço do BTC?", "US$ 81.528,29", market_data_at=datetime(2026, 10, 20, 1, tzinfo=UTC))
    rendered = memory.render()
    assert rendered.startswith(STALE_WARNING)
    assert "obtidos em 2026-10-20T01:00:00+00:00 — históricos" in rendered


def test_empty_memory_and_limits():
    assert BoundedConversationMemory().render() == EMPTY_HISTORY
    with pytest.raises(ValueError):
        BoundedConversationMemory(max_turns=0)


def test_long_answers_are_truncated():
    memory = BoundedConversationMemory(max_chars=5000)
    memory.add("q", "y" * 2000)
    assert "y" * 600 not in memory.render()


# --------------------------------------------------------------------------- Chroma


@pytest.fixture
def knowledge(tmp_path) -> KnowledgeSettings:
    return KnowledgeSettings(corpus_dir="knowledge/corpus", store_dir=str(tmp_path / "chroma"), embedding_model="fake")


def test_index_build_persist_load_and_search(knowledge):
    embeddings = DeterministicFakeEmbedding(size=32)
    _, manifest = build_index(knowledge, embeddings)
    assert manifest["chunks"] > 5
    assert "README.md" not in manifest["files"]
    index = load_index(knowledge, embeddings)
    hits = search(index, "halving do bitcoin", k=2)
    assert len(hits) == 2 and all(h.source.endswith(".md") for h in hits)


def test_reindex_recreates_collection_without_duplicates(knowledge):
    embeddings = DeterministicFakeEmbedding(size=32)
    _, first = build_index(knowledge, embeddings)
    store, second = build_index(knowledge, embeddings)
    assert second["chunks"] == first["chunks"]
    assert len(store.get()["ids"]) == first["chunks"]
    assert second["vector_store"] == "chroma"


def test_missing_collection_is_explicit(knowledge):
    embeddings = DeterministicFakeEmbedding(size=8)
    build_index(knowledge, embeddings)
    # Manifesto presente, mas a coleção configurada não existe no Chroma.
    other = KnowledgeSettings(**{**knowledge.__dict__, "collection": "outra"})
    with pytest.raises(KnowledgeIndexError, match="ausente"):
        load_index(other, embeddings)


def test_missing_index_is_explicit(knowledge):
    with pytest.raises(KnowledgeIndexError, match="knowledge/ingest"):
        load_index(knowledge, DeterministicFakeEmbedding(size=8))


def test_embedding_model_mismatch_requires_reindex(knowledge):
    build_index(knowledge, DeterministicFakeEmbedding(size=8))
    other = KnowledgeSettings(**{**knowledge.__dict__, "embedding_model": "outro"})
    with pytest.raises(KnowledgeIndexError, match="reindexe"):
        load_index(other, DeterministicFakeEmbedding(size=8))


def test_corpus_has_no_market_prices():
    from pathlib import Path

    from app.services.grounding import extract_figures

    for path in Path("knowledge/corpus").glob("*.md"):
        assert extract_figures(path.read_text()) == [], path.name


def test_knowledge_tool_reports_unavailable_index():
    def missing():
        raise KnowledgeIndexError("índice não encontrado")

    result = json.loads(build_knowledge_tool(missing, top_k=2).invoke("o que é TVL"))
    assert result["ok"] is False and result["error"]["kind"] == "index_unavailable"


def test_knowledge_tool_returns_sources(knowledge):
    embeddings = DeterministicFakeEmbedding(size=32)
    build_index(knowledge, embeddings)
    tool = build_knowledge_tool(lambda: load_index(knowledge, embeddings), top_k=2)
    result = json.loads(tool.invoke("stablecoin"))
    assert result["ok"] and len(result["data"]) == 2 and result["data"][0]["source"].endswith(".md")


# --------------------------------------------------------------------------- observabilidade


def test_trace_records_tool_success_and_failure():
    trace = TraceCallbackHandler()
    ok = Tool(name="t_ok", func=lambda _: json.dumps({"ok": True}), description="d")
    bad = Tool(name="t_bad", func=lambda _: json.dumps({"ok": False, "error": {"kind": "timeout", "message": "m"}}), description="d")
    ok.invoke("x", config={"callbacks": [trace]})
    bad.invoke("x", config={"callbacks": [trace]})
    tools = [e for e in trace.events if e.kind == "tool"]
    assert [(e.name, e.ok) for e in tools] == [("t_ok", True), ("t_bad", False)]
    assert tools[1].detail.startswith("error=timeout")
    assert all(e.duration_ms is not None for e in tools)


def test_secret_redaction_filter():
    record = logging.LogRecord("x", logging.INFO, "f", 1, "chave=%s", ("s3cr3t",), None)
    SecretRedactionFilter(["s3cr3t"]).filter(record)
    assert record.getMessage() == "chave=***"
