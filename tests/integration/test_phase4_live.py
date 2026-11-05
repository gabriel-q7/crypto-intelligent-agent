"""Integração real da Fase 4: CoinPaprika → SQLite, Text-to-Query com Ollama, retrieval na coleção
Chroma existente (``crypto_knowledge``) com Query Rewriting e agente principal com as novas ferramentas.

Execução explícita:
    pytest -m network tests/integration/test_phase4_live.py                    # só ingestão real
    pytest -m "integration or network" tests/integration/test_phase4_live.py   # tudo
"""

import json

import httpx
import pytest

from app.agents.factory import KnowledgeSearcher, build_agent
from app.config import (
    AgentSettings,
    MarketDataSettings,
    get_knowledge_settings,
    get_retrieval_settings,
    get_settings,
)
from app.knowledge.store import KnowledgeIndexError
from app.market_data.ingestion import MarketDataIngestor
from app.market_data.store import MarketDataStore
from app.market_data.text_to_query import TextToQueryService
from app.retrieval.agent import RetrievalAgent
from app.retrieval.rewriter import QueryRewriter
from app.services.llm import build_llm
from app.tools.coinpaprika import CoinPaprikaClient
from tests.integration.test_phase2_live import _ollama_has

ASSETS = ["btc-bitcoin", "eth-ethereum", "sol-solana", "xrp-xrp", "doge-dogecoin"]


@pytest.fixture(scope="module")
def client():
    try:
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        pytest.skip("sem acesso à CoinPaprika")
    return CoinPaprikaClient()


@pytest.fixture(scope="module")
def store(client, tmp_path_factory):
    store = MarketDataStore(tmp_path_factory.mktemp("market") / "market.sqlite3")
    report = MarketDataIngestor(client, store).ingest(ASSETS)
    assert report.failed == 0, report.items
    return store


@pytest.fixture(scope="module")
def llm():
    if not _ollama_has(get_settings().ollama_model):
        pytest.skip("Ollama ou modelo de chat indisponível")
    return build_llm()


@pytest.fixture(scope="module")
def knowledge():
    settings = get_knowledge_settings()
    if not _ollama_has(settings.embedding_model):
        pytest.skip("modelo de embedding indisponível")
    searcher = KnowledgeSearcher(settings)
    try:
        searcher.index()
    except KnowledgeIndexError:
        pytest.skip("coleção crypto_knowledge não existe; rode POST /knowledge/ingest")
    return searcher


# --------------------------------------------------------------------------- ingestão real


@pytest.mark.network
def test_live_ingestion_persists_real_snapshots(store, client):
    coverage = store.coverage()
    assert coverage.assets == len(ASSETS) and coverage.snapshots == len(ASSETS)
    rows = {r["symbol"]: r for r in store.snapshots()}
    assert rows["BTC"]["price_usd"] > 0 and rows["BTC"]["source_updated_at"] and rows["BTC"]["retrieved_at"]
    # Coleta repetida dentro da janela de atualização do provedor não duplica.
    again = MarketDataIngestor(client, store).ingest(["btc-bitcoin"])
    assert again.items[0].status in {"duplicate", "inserted"}
    if again.items[0].status == "duplicate":
        assert store.coverage().snapshots == len(ASSETS)


# --------------------------------------------------------------------------- Text-to-Query real


@pytest.mark.integration
@pytest.mark.network
def test_live_text_to_query_on_populated_database(store, llm):
    service = TextToQueryService(llm, store, MarketDataSettings())
    result = service.run("Quais são os 3 maiores ativos por market cap no banco?")
    assert result.status == "ok", result.message
    assert result.sql and result.sql.upper().startswith(("SELECT", "WITH")) and "LIMIT" in result.sql.upper()
    caps = {r["symbol"]: r for r in store.snapshots()}
    expected = sorted(caps, key=lambda s: caps[s]["market_cap_usd"] or 0, reverse=True)[:3]
    assert [r.get("symbol") for r in result.rows] == expected
    gone = service.run("Qual ativo mais subiu nos últimos 90 dias?")
    assert gone.status == "insufficient_coverage" and gone.sql is None
    tvl = service.run("Qual o TVL da Uniswap?")
    assert tvl.status in {"unsupported", "invalid_query"} and not tvl.rows


# --------------------------------------------------------------------------- retrieval real


@pytest.mark.integration
def test_live_retrieval_uses_existing_collection_without_rewrite_when_suitable(knowledge, llm):
    agent = RetrievalAgent(knowledge.index, QueryRewriter(llm), get_retrieval_settings())
    result = agent.retrieve("Explique como funciona proof of stake")
    assert result.status == "ok" and not result.rewrite_applied
    assert {d.source for d in result.documents} & {"blockchain-basics.md", "ethereum-staking.md"}
    assert all(d.chunk_id and d.metadata.get("source") for d in result.documents)


@pytest.mark.integration
def test_live_query_rewriting_fixes_vocabulary_mismatch(knowledge, llm):
    """Caso real de docs/phase-4-evidence.md: a pergunta não usa 'halving'; o trecho da seção
    Halving (bitcoin.md#1) fica em 3º na busca original e deve subir com a reescrita."""
    agent = RetrievalAgent(knowledge.index, QueryRewriter(llm), get_retrieval_settings())
    result = agent.retrieve("Por que a recompensa dos mineradores cai pela metade?")
    original_ids = [d.chunk_id for d in result.original_documents]
    used_ids = [d.chunk_id for d in result.documents]
    print(json.dumps({"rewritten_query": result.rewritten_query, "original": original_ids, "used": used_ids,
                      "decision": result.decision}, ensure_ascii=False))
    assert original_ids.index("bitcoin.md#1") == 2  # 3º na busca original
    # A saída do Ollama varia entre execuções (docs/phase-4-evidence.md, 1.2): em 5 execuções o
    # trecho ficou em 1º quatro vezes e em 2º uma vez. O critério é melhorar a posição.
    assert result.rewrite_applied and "bitcoin.md#1" in used_ids[:2]


@pytest.mark.integration
def test_live_rewrite_does_not_fabricate_evidence_for_absent_topic(knowledge, llm):
    """'Queima de moedas' não existe no corpus: nenhuma evidência, com ou sem reescrita."""
    agent = RetrievalAgent(knowledge.index, QueryRewriter(llm), get_retrieval_settings())
    result = agent.retrieve("Como funciona a queima de moedas?")
    assert result.status == "no_relevant_documents" and result.documents == []


# --------------------------------------------------------------------------- agente principal


@pytest.mark.integration
@pytest.mark.network
def test_live_agent_selects_new_tools(store, client, knowledge, llm):
    retrieval = RetrievalAgent(knowledge.index, QueryRewriter(llm), get_retrieval_settings())
    text_to_query = TextToQueryService(llm, store, MarketDataSettings())
    agent = build_agent(
        client, knowledge, AgentSettings(max_iterations=5), retrieval=retrieval, market_db=text_to_query
    )
    report = agent.run("Quais ativos da nossa base de dados têm o maior market cap? Liste os 3 primeiros.")
    tools = [c.tool for c in report.tool_calls]
    print(json.dumps({"tools": tools, "status": report.status, "answer": report.answer}, ensure_ascii=False))
    assert "query_market_database" in tools
    concept = agent.run("Explique como funciona proof of stake")
    assert "search_knowledge_base" in [c.tool for c in concept.tool_calls]
    assert any(f.source_tool == "search_knowledge_base" for f in concept.findings)
