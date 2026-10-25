"""API HTTP com dependências falsas: LLM roteirizado, CoinPaprika simulada, embeddings determinísticos."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agents.factory import KnowledgeSearcher
from app.api.container import AppContainer, SessionStore
from app.config import AgentSettings, CoinPaprikaSettings, KnowledgeSettings
from app.main import create_app
from app.tools.coinpaprika import CoinPaprikaClient
from tests.conftest import VALID_OUTPUT
from tests.unit.test_coinpaprika_client import SEARCH, TICKER


def paprika(request: httpx.Request) -> httpx.Response:
    if request.url.path.startswith("/v1/search"):
        return httpx.Response(200, json=SEARCH)
    if request.url.path == "/v1/tickers/btc-bitcoin":
        return httpx.Response(200, json=TICKER)
    return httpx.Response(404, json={"error": "id not found"})


def make_client(tmp_path, chat_responses=(), agent_responses=()) -> TestClient:
    knowledge = KnowledgeSettings(corpus_dir="knowledge/corpus", index_dir=str(tmp_path / "idx"), embedding_model="fake")
    agent_settings = AgentSettings(max_iterations=3)

    def factory() -> AppContainer:
        return AppContainer(
            agent_settings=agent_settings,
            knowledge_settings=knowledge,
            llm=FakeListChatModel(responses=list(chat_responses) or ["{}"]),
            agent_llm=FakeListLLM(responses=list(agent_responses) or ["Final Answer: ok"]),
            coinpaprika=CoinPaprikaClient(CoinPaprikaSettings(base_url="https://test.local/v1"),
                                          transport=httpx.MockTransport(paprika), sleep=lambda _: None),
            knowledge=KnowledgeSearcher(knowledge, DeterministicFakeEmbedding(size=32)),
            sessions=SessionStore(agent_settings, max_sessions=2),
        )

    return TestClient(create_app(factory))


def test_analyze_phase1(tmp_path):
    with make_client(tmp_path, [json.dumps(VALID_OUTPUT)]) as client:
        response = client.post("/analyze", json={"question": "Compare BTC e ETH", "assets": ["btc", "eth"]})
    assert response.status_code == 200
    assert response.json()["assets"] == ["BTC", "ETH"]


def test_analyze_invalid_model_output_is_502(tmp_path):
    with make_client(tmp_path, ["isto não é json"]) as client:
        response = client.post("/analyze", json={"question": "Compare BTC e ETH"})
    assert response.status_code == 502 and response.json()["error"] == "invalid_model_output"


def test_analyze_request_validation_is_422(tmp_path):
    with make_client(tmp_path) as client:
        assert client.post("/analyze", json={"question": "x"}).status_code == 422


def test_analyze_batch_isolates_failures(tmp_path):
    with make_client(tmp_path, [json.dumps(VALID_OUTPUT), "inválido"]) as client:
        response = client.post("/analyze/batch", json={"questions": [{"question": "BTC?"}, {"question": "ETH?"}]})
    assert [item["ok"] for item in response.json()] == [True, False]


def test_analyze_stream_ndjson(tmp_path):
    with make_client(tmp_path, [json.dumps(VALID_OUTPUT)]) as client:
        response = client.post("/analyze/stream", json={"question": "Compare BTC e ETH"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert len(events) > 2 and events[0]["type"] == "partial"
    assert events[-1]["type"] == "final" and events[-1]["data"]["assets"] == ["BTC", "ETH"]


def test_agent_uses_tool_keeps_session_and_trace(tmp_path):
    responses = [
        "Thought: dados.\nAction: get_market_data\nAction Input: BTC",
        "Final Answer: O BTC está em US$ 81.545,05 (CoinPaprika).",
        "Final Answer: Sem nova consulta.",
    ]
    with make_client(tmp_path, agent_responses=responses) as client:
        first = client.post("/agent", json={"question": "Preço do BTC?", "session_id": "s1", "include_trace": True}).json()
        second = client.post("/agent", json={"question": "E agora?", "session_id": "s1"}).json()
        assert client.delete("/agent/sessions/s1").status_code == 204
        assert client.delete("/agent/sessions/s1").status_code == 404
    report = first["report"]
    assert report["status"] == "complete" and "81.545,05" in report["answer"]
    assert report["data_sources"][0]["endpoint"] == "/tickers/btc-bitcoin"
    assert any(e["kind"] == "tool" for e in first["trace"])
    assert second["session_id"] == "s1" and second["trace"] is None


def test_sessions_are_bounded():
    store = SessionStore(AgentSettings(), max_sessions=2)
    for sid in ("a", "b", "c"):
        store.get(sid)
    assert len(store) == 2 and not store.delete("a")


def test_route_defi_unavailable(tmp_path):
    with make_client(tmp_path, [json.dumps({"route": "defi_analysis", "reason": "TVL"})]) as client:
        body = client.post("/route", json={"question": "TVL do Aave?"}).json()
    assert body["report"]["status"] == "unavailable"


def test_knowledge_ingest_search_and_missing_index(tmp_path):
    with make_client(tmp_path) as client:
        missing = client.get("/knowledge/search", params={"q": "halving"})
        assert missing.status_code == 503 and missing.json()["error"] == "knowledge_index_unavailable"
        manifest = client.post("/knowledge/ingest").json()
        assert manifest["chunks"] > 5
        hits = client.get("/knowledge/search", params={"q": "halving", "k": 2}).json()
    assert len(hits) == 2 and hits[0]["source"].endswith(".md")


def test_health_reports_degraded_without_ollama(tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("app.api.routes.httpx.get", offline)
    with make_client(tmp_path) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded" and body["ollama_reachable"] is False
    assert body["knowledge_index"] is False


@pytest.mark.parametrize("asset", ["btc-bitcoin"])
def test_coinpaprika_health_checks(tmp_path, asset):
    with make_client(tmp_path) as client:
        body = client.get("/health/coinpaprika", params={"asset": asset}).json()
    by_name = {c["check"]: c for c in body["checks"]}
    assert by_name["ticker"]["ok"] and by_name["missing_asset_is_not_found"]["ok"]
    assert by_name["invalid_id_rejected"]["ok"]
