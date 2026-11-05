"""Fase 4: ferramenta determinística, seleção de ferramentas pelo agente ReAct e API (offline)."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agents.factory import KnowledgeSearcher, build_agent_tools
from app.agents.runner import CryptoAgent
from app.api.container import AppContainer
from app.config import AgentSettings, KnowledgeSettings, MarketDataSettings, RetrievalSettings
from app.knowledge.store import build_index
from app.main import create_app
from app.market_data.text_to_query import TextToQueryService
from app.retrieval.agent import RetrievalAgent
from app.retrieval.rewriter import QueryRewriter
from app.schemas.report import ReportStatus
from app.tools.calculation_tools import PercentChangeInput, build_calculation_tools
from tests.unit.phase4_fakes import FakeIndex, paprika_client, populated_store, sql_reply

AGENT_SETTINGS = AgentSettings(max_iterations=4, max_execution_time_s=30)
TOP_SQL = "SELECT symbol, name, percent_change_24h FROM latest_snapshots WHERE percent_change_24h > 10"
KNOWLEDGE = {"proof of stake": [("blockchain-basics.md#3", "Fundamentos de blockchain", 0.25)]}


# --------------------------------------------------------------------------- cálculo determinístico


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("100, 112.5", 12.5), ("de 200 para 150", -25.0), ("old=81.545,05 new=82.000", 0.558)],
)
def test_percent_change_tool(raw, expected):
    tool = build_calculation_tools()[0]
    payload = json.loads(tool.run(raw))
    assert payload["ok"] and payload["data"]["percent_change"] == pytest.approx(expected, abs=0.01)


def test_percent_change_rejects_bad_input():
    tool = build_calculation_tools()[0]
    assert json.loads(tool.run("só um 10"))["error"]["kind"] == "invalid_argument"
    assert json.loads(tool.run("0, 10"))["error"]["kind"] == "invalid_argument"
    with pytest.raises(ValueError):
        PercentChangeInput.from_text("1 2 3")


# --------------------------------------------------------------------------- agente principal


def tools_for(tmp_path, sql_replies=(), rewrite_replies=()):
    store = populated_store(tmp_path)
    text_to_query = TextToQueryService(FakeListChatModel(responses=list(sql_replies) or ["{}"]), store, MarketDataSettings())
    retrieval = RetrievalAgent(
        lambda: FakeIndex(KNOWLEDGE), QueryRewriter(FakeListChatModel(responses=list(rewrite_replies) or ["{}"])),
        RetrievalSettings(),
    )
    knowledge = KnowledgeSearcher(KnowledgeSettings(), DeterministicFakeEmbedding(size=8))
    return build_agent_tools(paprika_client(), knowledge, retrieval, text_to_query)


def test_main_agent_registers_all_tool_categories(tmp_path):
    names = [t.name for t in tools_for(tmp_path)]
    assert names == [
        "search_crypto_asset", "get_market_data", "get_market_details", "get_global_market",
        "search_knowledge_base", "calculate_percent_change", "query_market_database",
    ]


def run_agent(tmp_path, responses, **kwargs):
    agent = CryptoAgent(FakeListLLM(responses=responses), tools_for(tmp_path, **kwargs), AGENT_SETTINGS, model_name="fake")
    return agent, agent.run("pergunta")


def test_agent_selects_text_to_query_and_grounds_answer_in_rows(tmp_path):
    agent, report = run_agent(tmp_path, [
        "Thought: filtro no banco.\nAction: query_market_database\nAction Input: Quais ativos subiram mais de 10%?",
        "Thought: ok.\nFinal Answer: No banco de snapshots, só SOL subiu 12,4% em 24h. Já o BTC subiu 40%.",
    ], sql_replies=[sql_reply(TOP_SQL)])
    assert [c.tool for c in report.tool_calls] == ["query_market_database"] and report.tool_calls[0].ok
    assert "12,4%" in report.answer and "40%" not in report.answer  # valor sem linha no banco é removido
    assert any(f.statement.startswith("Banco de snapshots") and "SOL" in f.statement for f in report.findings)
    assert report.data_sources[0].provider.startswith("SQLite local")


def test_agent_combines_market_api_and_retrieval(tmp_path):
    _, report = run_agent(tmp_path, [
        "Thought: preço atual.\nAction: get_market_data\nAction Input: BTC",
        "Thought: conceito.\nAction: search_knowledge_base\nAction Input: proof of stake",
        "Thought: ok.\nFinal Answer: O BTC está em US$ 81.545,05 (CoinPaprika). Proof of stake usa validadores com garantia.",
    ])
    assert [c.tool for c in report.tool_calls] == ["get_market_data", "search_knowledge_base"]
    assert report.status is ReportStatus.COMPLETE
    assert {s.tool for s in report.data_sources} == {"get_market_data", "search_knowledge_base"}
    assert any("blockchain-basics.md" in f.statement for f in report.findings)


def test_agent_reports_missing_database_coverage(tmp_path):
    _, report = run_agent(tmp_path, [
        "Thought: banco.\nAction: query_market_database\nAction Input: Qual ativo mais subiu nos últimos 90 dias?",
        "Thought: sem cobertura.\nFinal Answer: O banco não cobre 90 dias; não há como responder.",
    ])
    call = report.tool_calls[0]
    assert not call.ok and call.error_kind == "insufficient_coverage"
    assert report.status is ReportStatus.PARTIAL and any("insufficient_coverage" in u for u in report.uncertainties)


# --------------------------------------------------------------------------- API


def make_app(tmp_path, chat_responses):
    knowledge = KnowledgeSettings(corpus_dir="knowledge/corpus", store_dir=str(tmp_path / "chroma"), embedding_model="fake")
    embeddings = DeterministicFakeEmbedding(size=32)
    build_index(knowledge, embeddings)
    market = MarketDataSettings(db_path=str(tmp_path / "api-market.sqlite3"))

    def factory():
        return AppContainer(
            knowledge_settings=knowledge, llm=FakeListChatModel(responses=chat_responses),
            agent_llm=FakeListLLM(responses=["Final Answer: ok"]), coinpaprika=paprika_client(),
            knowledge=KnowledgeSearcher(knowledge, embeddings), market_settings=market,
            retrieval_settings=RetrievalSettings(rewrite_mode="never", relevance_max_distance=2.0),
            background_maintenance=False,
        )

    return TestClient(create_app(factory))


def test_api_ingest_coverage_query_and_retrieve(tmp_path):
    with make_app(tmp_path, [sql_reply(TOP_SQL)]) as client:
        assert client.post("/market-data/query", json={"question": "Quais subiram?"}).json()["status"] == "missing_data"
        ingest = client.post("/market-data/ingest", json={"assets": ["BTC", "eth-ethereum", "sol-solana"]}).json()
        assert ingest["inserted"] == 3 and ingest["coverage"]["snapshots"] == 3
        assert client.get("/market-data/coverage").json()["symbols"] == ["BTC", "ETH", "SOL"]
        assert client.get("/market-data/snapshots", params={"symbol": "sol"}).json()[0]["price_usd"] == 180.25
        query = client.post("/market-data/query", json={"question": "Quais ativos subiram mais de 10%?"}).json()
        assert query["status"] == "ok" and query["rows"][0]["symbol"] == "SOL" and "?" in query["sql"]
        retrieved = client.post("/knowledge/retrieve", json={"question": "O que é staking?", "k": 2}).json()
        assert retrieved["status"] == "ok" and len(retrieved["documents"]) == 2 and not retrieved["rewrite_applied"]
        assert client.get("/health").json()["market_snapshots"] == 3
        # Fase 2 preservada: a busca simples continua disponível.
        assert client.get("/knowledge/search", params={"q": "staking"}).status_code == 200


def test_mixed_question_without_documents_is_partial(tmp_path):
    agent = CryptoAgent(FakeListLLM(responses=[
        "Thought: preço.\nAction: get_market_data\nAction Input: BTC",
        "Thought: ok.\nFinal Answer: O BTC está em US$ 81.545,05. Proof of work usa mineração.",
    ]), tools_for(tmp_path), AGENT_SETTINGS, model_name="fake")
    report = agent.run("Compare o preço do BTC e explique a diferença entre proof of work e proof of stake")
    assert report.status is ReportStatus.PARTIAL
    assert any("parte conceitual" in u for u in report.uncertainties)


def test_sql_filter_values_are_grounded(tmp_path):
    _, report = run_agent(tmp_path, [
        "Thought: banco.\nAction: query_market_database\nAction Input: Quais ativos subiram mais de 10%?",
        "Thought: ok.\nFinal Answer: Só SOL subiu mais de 10% em 24h (12,4%).",
    ], sql_replies=[sql_reply(TOP_SQL)])
    assert "mais de 10%" in report.answer and not report.uncertainties
