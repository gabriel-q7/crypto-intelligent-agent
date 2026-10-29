"""Cenários multi-turno A–H e contrato da API de conversa (offline, determinístico)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake import FakeListLLM

from app.agents.factory import KnowledgeSearcher
from app.api.container import AppContainer
from app.config import ConversationSettings, KnowledgeSettings
from app.knowledge.store import build_index
from app.main import create_app
from app.memory.long_term import InMemoryBackend
from tests.unit.conversation_fakes import RoutingFakeChat
from tests.unit.test_phase2_chains import FakeClient

PRICES = {"BTC": 81000.0, "ETH": 2400.0, "SOL": 150.0}


class Harness:
    def __init__(self, tmp_path, memory=None, llm=None, client=None):
        self.tmp_path = tmp_path
        self.memory = memory or InMemoryBackend()
        self.llm = llm or RoutingFakeChat()
        self.market = client or FakeClient(PRICES)
        self.knowledge = KnowledgeSettings(corpus_dir="knowledge/corpus", store_dir=str(tmp_path / "chroma"), embedding_model="fake")
        self.embeddings = DeterministicFakeEmbedding(size=32)
        if not (tmp_path / "chroma" / "manifest.json").exists():
            build_index(self.knowledge, self.embeddings)

    def app(self) -> TestClient:
        settings = ConversationSettings(db_path=str(self.tmp_path / "conv.sqlite3"), window_messages=4,
                                        summary_trigger_messages=2, context_budget_tokens=1500)

        def factory():
            return AppContainer(
                knowledge_settings=self.knowledge, llm=self.llm, agent_llm=FakeListLLM(responses=["Final Answer: ok"]),
                coinpaprika=self.market, knowledge=KnowledgeSearcher(self.knowledge, self.embeddings),
                conversation_settings=settings, memory_backend=self.memory, background_maintenance=False,
            )

        return TestClient(create_app(factory))


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


def new_session(api) -> str:
    response = api.post("/conversations", json={"title": "teste"})
    assert response.status_code == 201
    return response.json()["session_id"]


def say(api, sid, text) -> dict:
    response = api.post(f"/conversations/{sid}/messages", json={"message": text})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- cenários


def test_scenario_a_follow_up_adds_period_without_repeating_assets(harness):
    with harness.app() as api:
        sid = new_session(api)
        first = say(api, sid, "Compare BTC e ETH no mercado atual")
        second = say(api, sid, "Considere os últimos 30 dias")
    assert first["effective_intent"] == "asset_comparison" and first["status"] == "complete" and first["analysis_id"]
    assert second["intent"] == "follow_up" and second["effective_intent"] == "asset_comparison"
    assert second["entities"]["assets"] == ["BTC", "ETH"] and second["entities"]["period_days"] == 30
    assert second["context_sources"] == {"assets": "active_context", "period": "current_message"}
    assert not second["clarification_needed"]
    # 30 dias não existe no plano gratuito: limitação explícita, nada inventado.
    assert second["status"] == "partial" and "Não há dados de 30 dias" in second["answer"]
    assert any("30 dias indisponível" in item for item in second["limitations"])


def test_scenario_b_defi_dimension_keeps_context_and_reports_missing_source(harness):
    with harness.app() as api:
        sid = new_session(api)
        say(api, sid, "Compare BTC e ETH nos últimos 30 dias")
        reply = say(api, sid, "E quanto a DeFi?")
    assert reply["effective_intent"] == "defi_analysis" and reply["status"] == "unavailable"
    assert reply["entities"]["assets"] == ["BTC", "ETH"] and reply["entities"]["period_days"] == 30
    assert "DeFiLlama" in reply["answer"] and "BTC, ETH" in reply["answer"]
    assert reply["report"]["findings"] == []  # nenhuma métrica DeFi inventada


def test_scenario_c_previous_result_is_distinguished_from_fresh_data(harness):
    with harness.app() as api:
        sid = new_session(api)
        first = say(api, sid, "Compare BTC e ETH")
        reply = say(api, sid, "Agora compare com o resultado anterior")
    assert reply["effective_intent"] == "previous_result_reference"
    assert reply["referenced_analyses"] == [first["analysis_id"]]
    assert f"resultado anterior {first['analysis_id']}" in reply["answer"]
    assert "agora US$ 81.000,00" in reply["answer"] and "diferença +0,00%" in reply["answer"]
    assert any("HISTÓRICO" in item for item in reply["limitations"])
    assert {s["endpoint"] for s in reply["data_sources"]} >= {"/tickers/btc-coin", "/tickers/eth-coin"}


def test_scenario_c_ambiguous_reference_asks_for_clarification(harness):
    with harness.app() as api:
        sid = new_session(api)
        say(api, sid, "Compare BTC e ETH")
        say(api, sid, "Qual o preço do SOL?")
        reply = say(api, sid, "Compare com o resultado anterior")
    assert reply["status"] == "needs_clarification" and reply["clarification_needed"]
    assert "Há mais de uma análise anterior" in reply["answer"]


def test_scenario_d_correction_replaces_period(harness):
    with harness.app() as api:
        sid = new_session(api)
        first = say(api, sid, "Analise BTC nos últimos 7 dias")
        second = say(api, sid, "Eu quis dizer 30 dias, não 7")
        session = api.get(f"/conversations/{sid}").json()
    assert first["entities"]["period_days"] == 7 and "Variação em 7 dias" in first["answer"]
    assert second["entities"]["assets"] == ["BTC"] and second["entities"]["period_days"] == 30
    assert session["active_context"]["period_days"] == 30  # sem período contraditório no pedido ativo
    assert "7" not in (session["active_context"]["period"] or "")


def test_scenario_e_session_resumes_after_restart(harness):
    with harness.app() as api:
        sid = new_session(api)
        say(api, sid, "Analise BTC nos últimos 7 dias")
    # "Reinício": nova aplicação, novo contêiner, mesmo SQLite e mesma memória vetorial.
    with harness.app() as api:
        assert api.get(f"/conversations/{sid}").status_code == 200
        history = api.get(f"/conversations/{sid}/messages").json()
        reply = say(api, sid, "E nas últimas 24 horas?")
    assert [m["seq"] for m in history["messages"]] == [1, 2]
    assert reply["entities"]["assets"] == ["BTC"] and reply["context_sources"]["assets"] == "active_context"


def test_scenario_f_sessions_are_isolated(harness):
    with harness.app() as api:
        btc, eth = new_session(api), new_session(api)
        say(api, btc, "Analise BTC")
        say(api, eth, "Analise ETH")
        r_btc = say(api, btc, "E nos últimos 7 dias?")
        r_eth = say(api, eth, "E nos últimos 7 dias?")
        fresh = new_session(api)
        r_new = say(api, fresh, "E nos últimos 7 dias?")
    assert r_btc["entities"]["assets"] == ["BTC"] and r_eth["entities"]["assets"] == ["ETH"]
    assert r_new["clarification_needed"]  # sessão nova não herda nada
    assert all(h.text.find("ETH") < 0 for h in harness.memory.list(btc))
    assert all(h.text.find("BTC") < 0 for h in harness.memory.list(eth))


def test_scenario_g_long_conversation_summarizes_and_stays_in_budget(harness):
    with harness.app() as api:
        sid = new_session(api)
        replies = [say(api, sid, text) for text in [
            "Analise BTC", "E nos últimos 7 dias?", "Analise ETH", "Compare BTC e ETH",
            "E nas últimas 24 horas?", "Analise SOL", "E nos últimos 7 dias?",
        ]]
        summary = api.get(f"/conversations/{sid}/summary").json()
    assert summary["version"] >= 1 and summary["covers_until_seq"] >= 2
    assert summary["details"]["previous_results"]  # resultados antigos sobrevivem ao resumo
    assert all(r["context_usage"]["tokens"] <= r["context_usage"]["budget_tokens"] for r in replies)
    assert replies[-1]["context_usage"]["included"]["summary"] == 1
    episodes = [h for h in harness.memory.list(sid) if h.metadata.get("doc_type") == "analysis"]
    assert len(episodes) >= 4  # análises indexadas como memória episódica


def test_scenario_h_previous_result_after_long_interval_is_marked_stale(harness):
    data_time = datetime(2026, 10, 20, 1, tzinfo=UTC)  # horário dos dados do FakeClient
    with harness.app() as api:
        sid = new_session(api)
        first = say(api, sid, "Compare BTC e ETH")
        service = api.app.state.container.conversation
        service.clock = lambda: data_time + timedelta(minutes=5)
        recent = say(api, sid, "Compare com o resultado anterior")
        service.clock = lambda: data_time + timedelta(days=2)
        later = say(api, sid, "Compare os dois com o resultado anterior")
    assert recent["referenced_analyses"] == [first["analysis_id"]]
    assert "há 5 min" in recent["answer"] and "NÃO são dados atuais" not in recent["answer"]
    assert "há 48.0 h" in later["answer"] and "NÃO são dados atuais" in later["answer"]


def test_semantic_memory_extracted_from_preferences_only(harness):
    with harness.app() as api:
        sid = new_session(api)
        say(api, sid, "Compare BTC e ETH")
        pref = say(api, sid, "Prefiro sempre análises com foco em risco")
        say(api, sid, "Analise BTC")
        session = api.get(f"/conversations/{sid}").json()
    assert harness.memory.extracted_inputs == [["Prefiro sempre análises com foco em risco"]]
    # Preferência é confirmada, não dispara análise e não altera o pedido em andamento.
    assert pref["answer"].startswith("Anotado") and pref["report"] is None and pref["analysis_id"] is None
    assert session["active_context"]["assets"] == ["BTC"]


def test_summary_episode_is_replaced_not_accumulated(harness):
    with harness.app() as api:
        sid = new_session(api)
        for text in ["Analise BTC", "E nos últimos 7 dias?", "Analise ETH", "Analise SOL", "Compare BTC e ETH", "Analise BTC"]:
            say(api, sid, text)
        version = api.get(f"/conversations/{sid}/summary").json()["version"]
    summaries = [h for h in harness.memory.list(sid) if h.metadata.get("doc_type") == "summary"]
    assert version >= 2 and len(summaries) == 1


# --------------------------------------------------------------------------- contrato da API


def test_unknown_and_malformed_session_ids(harness):
    unknown = "0" * 32
    with harness.app() as api:
        for method, path in [("get", ""), ("get", "/messages"), ("get", "/summary"), ("delete", "")]:
            assert getattr(api, method)(f"/conversations/{unknown}{path}").status_code == 404
        assert api.post(f"/conversations/{unknown}/messages", json={"message": "oi oi"}).status_code == 404
        assert api.get("/conversations/nao-e-hex").status_code == 422
        sid = new_session(api)
        assert api.post(f"/conversations/{sid}/messages", json={"message": ""}).status_code == 422


def test_history_order_and_delete(harness):
    with harness.app() as api:
        sid = new_session(api)
        say(api, sid, "Analise BTC")
        history = api.get(f"/conversations/{sid}/messages", params={"limit": 1}).json()
        assert history["total"] == 2 and [m["seq"] for m in history["messages"]] == [2]
        assert history["messages"][0]["analysis_id"]
        assert api.delete(f"/conversations/{sid}").status_code == 204
        assert api.get(f"/conversations/{sid}").status_code == 404
    assert harness.memory.list(sid) == []


def test_memory_outage_does_not_break_turn(harness):
    class Broken(InMemoryBackend):
        def search(self, *args, **kwargs):
            raise ConnectionError("chroma indisponível")

    broken = Harness(harness.tmp_path, memory=Broken())
    with broken.app() as api:
        sid = new_session(api)
        reply = say(api, sid, "Analise BTC")
    assert reply["status"] == "complete"
    assert any("memória de longo prazo indisponível" in item for item in reply["limitations"])


def test_phase3_modules_use_no_deprecated_langchain_apis():
    """Import num interpretador limpo: nenhum LangChainDeprecationWarning nos módulos da Fase 3."""
    import subprocess
    import sys

    modules = ["app.conversation.chatbot", "app.conversation.context_builder", "app.memory.short_term",
               "app.memory.long_term", "app.intent.classifier", "app.intent.resolver",
               "app.persistence.sqlite_store", "app.api.conversation_routes"]
    code = (
        "import warnings, importlib\n"
        "from langchain_core._api.deprecation import LangChainDeprecationWarning\n"
        "warnings.simplefilter('error', LangChainDeprecationWarning)\n"
        f"[importlib.import_module(m) for m in {modules!r}]\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
