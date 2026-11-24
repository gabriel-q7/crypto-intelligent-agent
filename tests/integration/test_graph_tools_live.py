"""Projeto LangGraph, Etapa 2: grafo ReAct com as 9 ferramentas reais, verificação real e correção única.

Mesmas peças do agente integrado (ferramentas, Text-to-Query, Retrieval Agent com Query Rewriting, verificador),
obtidas do ``AppContainer``: nada é recriado. Cenários combinados exigidos: API + SQL e API + RAG.

Execução: pytest -m "integration or network" tests/integration/test_graph_tools_live.py -v -s
"""

import json

import httpx
import pytest

from app.api.container import AppContainer
from app.config import ConversationSettings, get_agent_settings, get_settings
from app.graph import build_react_graph, graph_config, run_react
from tests.integration.test_phase2_live import _ollama_has

pytestmark = [pytest.mark.integration, pytest.mark.network]


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    if not _ollama_has(get_settings().ollama_model):
        pytest.skip("Ollama ou modelo indisponível")
    try:
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        pytest.skip("sem acesso à CoinPaprika")
    db = tmp_path_factory.mktemp("conv") / "conv.sqlite3"
    c = AppContainer(conversation_settings=ConversationSettings(db_path=str(db)), background_maintenance=False)
    if c.market_store.coverage().snapshots == 0:
        c.market_ingestor().ingest(list(c.market_settings.default_assets)[:5])
    tools = list(c.integrated.tools.values())
    assert len(tools) == 9
    yield build_react_graph(c.tool_llm, tools, verifier=c.integrated.verifier, prefetch=True)
    c.close()


def ask(graph, case: str, question: str):
    run = run_react(graph, question, config=graph_config(get_agent_settings().max_iterations, case=case))
    v = run.verification
    print(json.dumps({
        "case": case, "q": question, "model": get_settings().ollama_model,
        "tools": [{"tool": s.tool, "category": s.category, "args": s.args, "ok": s.ok, "error": s.error_kind,
                   "latency_s": lat} for s, lat in zip(run.steps, run.tool_latencies)],
        "rag_retrieval": [o.get("retrieval") for o in run.observations if o.get("tool") == "search_knowledge_base"],
        "sources": sorted({(o.get("source") or {}).get("provider", "?") for o in run.observations}),
        "llm_calls": run.llm_calls, "status": run.status,
        "verification": v.summary() if v else None, "corrected": bool(v and v.correction_attempted),
        "original_answer": (run.original_answer or "")[:300] or None,
        "answer": run.answer[:600], "elapsed_s": run.elapsed_s,
    }, ensure_ascii=False))
    return run


def ok_tools(run) -> set[str]:
    return {s.tool for s in run.steps if s.ok}


def test_api_plus_sql(graph):
    run = ask(graph, "api+sql", "Compare o preço atual do Bitcoin com o último snapshot do Bitcoin armazenado no banco local.")
    assert {"get_market_data", "query_market_database"} <= ok_tools(run)
    assert run.answer and run.verification is not None


def test_api_plus_rag(graph):
    run = ask(graph, "api+rag", "O que é Proof of Stake e qual é o preço atual do Ethereum?")
    assert {"get_market_data", "search_knowledge_base"} <= ok_tools(run)
    rag = next(o for o in run.observations if o.get("tool") == "search_knowledge_base")
    assert "retrieval" in rag and rag["retrieval"]["decision"]  # Retrieval Agent com Query Rewriting em uso


def test_sql_only(graph):
    run = ask(graph, "sql", "No banco local de snapshots, quais são os 3 ativos com maior market cap?")
    assert "query_market_database" in ok_tools(run)
    assert "get_market_data" not in {s.tool for s in run.steps}


def test_rag_only(graph):
    run = ask(graph, "rag", "Como funciona o staking no Ethereum?")
    assert "search_knowledge_base" in ok_tools(run)


def test_tool_failure_is_reported(graph):
    run = ask(graph, "error", "Qual é o preço atual da criptomoeda XQZWV?")
    failed = [s for s in run.steps if not s.ok]
    assert failed, "a ferramenta deveria falhar para um ativo inexistente"
    assert run.status in {"partial", "failed"}
    assert run.metadata["limitations"]


def test_network_comparison_prefetch(graph):
    run = ask(graph, "prefetch", "Compare a tecnologia da Solana vs Ethereum.")
    assert run.steps[0].tool == "search_knowledge_base"
    assert {"search_knowledge_base", "get_asset_profile", "get_market_data"} <= ok_tools(run)
