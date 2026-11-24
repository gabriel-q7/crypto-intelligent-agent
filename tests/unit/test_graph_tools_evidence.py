"""Projeto LangGraph, Etapa 2: todas as categorias de ferramenta, evidência, verificação e correção única no grafo."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START

from app.graph import build_react_graph, graph_config, run_react
from app.graph.nodes import turn_messages
from app.graph.run import turn_input
from app.integrated.service import ADVICE_NOTICE
from app.integrated.tools import AssetArgs, QuestionArgs
from tests.unit.phase5_fakes import OBS_BTC, OBS_ETH, OBS_RAG, OBS_SQL_FAIL, ScriptedVerifier, result, scripted, tool_call

OBS_SQL = {
    "ok": True, "tool": "query_market_database",
    "data": {"sql": "SELECT symbol, price_usd, collected_at FROM market_snapshots WHERE symbol='BTC'",
             "rows": [{"symbol": "BTC", "price_usd": 80100.0, "collected_at": "2026-11-04T12:00:00Z"}]},
    "source": {"provider": "SQLite (snapshots CoinPaprika)", "endpoint": "market_snapshots",
               "retrieved_at": "2026-11-05T00:51:00Z"},
}
OBS_PROFILE = {"ok": True, "tool": "get_asset_profile", "data": {"symbol": "SOL", "proof_type": "Proof of Stake"},
               "source": {"provider": "CoinPaprika", "endpoint": "/coins/sol-solana"}}


def tools(calls: list | None = None, sql_ok: bool = True) -> list[StructuredTool]:
    def log(name, arg):
        if calls is not None:
            calls.append((name, arg))

    def get_market_data(asset: str) -> str:
        log("get_market_data", asset)
        return json.dumps({"BTC": OBS_BTC, "ETH": OBS_ETH}.get(asset.upper(), {**OBS_BTC, "data": {"symbol": asset.upper()}}))

    def get_asset_profile(asset: str) -> str:
        log("get_asset_profile", asset)
        return json.dumps(OBS_PROFILE)

    def query_market_database(question: str) -> str:
        log("query_market_database", question)
        return json.dumps(OBS_SQL if sql_ok else OBS_SQL_FAIL)

    def search_knowledge_base(question: str) -> str:
        log("search_knowledge_base", question)
        return json.dumps(OBS_RAG)

    return [
        StructuredTool.from_function(get_market_data, name="get_market_data", description="preço", args_schema=AssetArgs),
        StructuredTool.from_function(get_asset_profile, name="get_asset_profile", description="perfil", args_schema=AssetArgs),
        StructuredTool.from_function(query_market_database, name="query_market_database", description="sql",
                                     args_schema=QuestionArgs),
        StructuredTool.from_function(search_knowledge_base, name="search_knowledge_base", description="rag",
                                     args_schema=QuestionArgs),
    ]


def calls_msg(*calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}", "type": "tool_call"}
                                             for i, (n, a) in enumerate(calls)])


# --------------------------------------------------------------------------- estrutura


def test_full_graph_structure():
    graph = build_react_graph(scripted("ok"), tools(), verifier=ScriptedVerifier(), prefetch=True).get_graph()
    assert set(graph.nodes) == {START, "prefetch", "agent", "tools", "collect_evidence", "verify_response",
                                "revise_response", "finalize", END}
    edges = {(e.source, e.target, e.conditional) for e in graph.edges}
    assert {(START, "prefetch", False), ("prefetch", "agent", False), ("agent", "tools", True),
            ("agent", "collect_evidence", True), ("tools", "agent", False),
            ("collect_evidence", "verify_response", False), ("verify_response", "revise_response", True),
            ("verify_response", "finalize", True), ("revise_response", "agent", False),
            ("finalize", END, False)} <= edges


# --------------------------------------------------------------------------- ferramentas combinadas e evidência


def test_api_plus_sql_evidence_keeps_provenance_and_latency():
    llm = scripted(calls_msg(("get_market_data", {"asset": "BTC"}),
                             ("query_market_database", {"question": "último snapshot do BTC"})),
                   "BTC está em US$ 81.545,05 ao vivo; o snapshot armazenado registrou US$ 80.100,00.")
    verifier = ScriptedVerifier(result("verified"))
    run = run_react(build_react_graph(llm, tools(), verifier=verifier), "Compare o BTC atual com o snapshot.")

    assert [(s.tool, s.category) for s in run.steps] == [("get_market_data", "api"), ("query_market_database", "sql")]
    assert run.observations == [OBS_BTC, OBS_SQL]  # fonte e horários intactos
    assert all(isinstance(lat, float) for lat in run.tool_latencies)
    assert verifier.calls == [("Compare o BTC atual com o snapshot.", run.answer, 2)]
    assert run.status == "complete" and run.verification.status == "verified"


def test_api_plus_rag_combination():
    llm = scripted(calls_msg(("search_knowledge_base", {"question": "o que é PoS"}), ("get_market_data", {"asset": "ETH"})),
                   "PoS: validadores bloqueiam moedas (base local). ETH está em US$ 2.426,11 (CoinPaprika).")
    run = run_react(build_react_graph(llm, tools(), verifier=ScriptedVerifier(result("verified"))),
                    "O que é PoS e qual o preço do ETH?")
    assert {s.category for s in run.steps} == {"api", "rag"}
    assert OBS_RAG in run.observations and OBS_ETH in run.observations


def test_sql_failure_then_api_fallback_is_reported_as_partial():
    llm = scripted(tool_call("query_market_database", {"question": "BTC 90 dias"}, "c1"),
                   tool_call("get_market_data", {"asset": "BTC"}, "c2"),
                   "O banco local não cobre 90 dias; preço atual US$ 81.545,05 (CoinPaprika).")
    run = run_react(build_react_graph(llm, tools(sql_ok=False), verifier=ScriptedVerifier(result("verified"))),
                    "Qual a média do BTC armazenada no banco local?")
    assert [s.ok for s in run.steps] == [False, True]
    assert run.status == "partial"
    assert run.metadata["limitations"] == ["query_market_database: insufficient_coverage — A pergunta pede 90 dias, "
                                           "mas os snapshots cobrem 0 dia(s)"]


def test_evidence_covers_only_current_turn():
    old = [HumanMessage(content="Preço do ETH?"), tool_call("get_market_data", {"asset": "ETH"}, "old"),
           ToolMessage(content=json.dumps(OBS_ETH), tool_call_id="old", name="get_market_data"),
           AIMessage(content="ETH US$ 2.426.")]
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC US$ 81.545.")
    run = run_react(build_react_graph(llm, tools()), "E o BTC?", history=old)
    assert run.observations == [OBS_BTC]
    assert len(run.messages) == len(old) + 4


def test_turn_messages_starts_at_turn_id():
    state = turn_input("nova", [HumanMessage(content="antiga", id="h1")])
    assert [m.content for m in turn_messages(state)] == ["nova"]


# --------------------------------------------------------------------------- verificação e correção única


def test_contradicted_answer_is_revised_once_and_passes():
    calls: list = []
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC está em US$ 90.000.",
                   "BTC está em US$ 81.545,05 (CoinPaprika).")
    verifier = ScriptedVerifier(result("contradicted", instructions=["CORRIJA: preço errado"]), result("verified"))
    run = run_react(build_react_graph(llm, tools(calls), verifier=verifier), "Preço do BTC?")

    assert calls == [("get_market_data", "BTC")]  # a correção não repete a chamada
    feedback = [m for m in run.messages if isinstance(m, HumanMessage)][-1]
    assert "Revisão automática" in feedback.content and "CORRIJA: preço errado" in feedback.content
    assert run.original_answer == "BTC está em US$ 90.000."
    assert run.answer == "BTC está em US$ 81.545,05 (CoinPaprika)."
    v = run.verification
    assert v.correction_attempted and v.corrected_passed and v.original_status == "contradicted"
    assert [c[2] for c in verifier.calls] == [1, 1]  # a 2ª verificação vê a mesma evidência
    assert len(run.llm_calls) == 3


def test_failed_revision_gives_cautious_answer_and_stops():
    llm = scripted("BTC está em US$ 90.000.", "BTC está em US$ 95.000.")
    verifier = ScriptedVerifier(result("contradicted"), result("contradicted"))
    run = run_react(build_react_graph(llm, tools(), verifier=verifier), "Preço do BTC?")
    assert len(verifier.calls) == 2  # nunca há segunda correção
    assert "Aviso: a verificação automática não confirmou toda a resposta." in run.answer
    assert run.verification.correction_attempted and not run.verification.corrected_passed
    assert run.status == "partial"


def test_empty_answer_triggers_correction_even_if_verified():
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "", "BTC US$ 81.545,05 (CoinPaprika).")
    verifier = ScriptedVerifier(result("verified"), result("verified"))
    run = run_react(build_react_graph(llm, tools(), verifier=verifier), "Preço do BTC?")
    feedback = [m for m in run.messages if isinstance(m, HumanMessage)][-1].content
    assert "a resposta final veio vazia" in feedback
    assert run.answer == "BTC US$ 81.545,05 (CoinPaprika)."


def test_skipped_lookup_on_refusal_asks_for_tools():
    llm = scripted("Não tenho dados sobre isso.", tool_call("get_market_data", {"asset": "SOL"}, "c1"),
                   "SOL US$ 150 (CoinPaprika). Não faço recomendação de investimento.")
    run = run_react(build_react_graph(llm, tools(), verifier=ScriptedVerifier(result("verified"), result("verified"))),
                    "Qual devo investir: SOL ou ETH?")
    feedback = next(m for m in run.messages if isinstance(m, HumanMessage) and "Revisão automática" in m.content)
    assert "nenhuma ferramenta foi chamada" in feedback.content
    assert [s.tool for s in run.steps] == ["get_market_data"]


def test_verification_failed_is_not_corrected_and_is_flagged():
    llm = scripted("BTC US$ 81.545,05.")
    verifier = ScriptedVerifier(result("verification_failed"))
    run = run_react(build_react_graph(llm, tools(), verifier=verifier), "Preço do BTC?")
    assert len(verifier.calls) == 1
    assert run.answer.endswith("esta resposta não foi verificada.")
    assert run.original_answer is None


def test_advice_notice_is_guaranteed():
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC está em US$ 81.545,05.")
    run = run_react(build_react_graph(llm, tools(), verifier=ScriptedVerifier(result("verified"))),
                    "Vale a pena comprar BTC?")
    assert run.answer.endswith(ADVICE_NOTICE)


# --------------------------------------------------------------------------- pré-consulta (comparação entre redes)


def test_prefetch_runs_fixed_lookups_only_for_network_comparison():
    calls: list = []
    llm = scripted("Solana e Ethereum usam PoS; preços com fonte e horário.")
    run = run_react(build_react_graph(llm, tools(calls), verifier=ScriptedVerifier(result("verified")), prefetch=True),
                    "Compare a tecnologia da Solana vs Ethereum")
    assert [c[0] for c in calls] == ["search_knowledge_base"] * 3 + ["get_asset_profile"] * 2 + ["get_market_data"] * 2
    assert [s.order for s in run.steps] == list(range(1, 8))
    assert run.tool_latencies == [None] * 7  # pré-consulta roda fora do ToolNode

    calls.clear()
    run_react(build_react_graph(scripted("BTC US$ 1."), tools(calls), prefetch=True), "Preço do BTC?")
    assert calls == []


def test_config_allows_one_correction_pass():
    llm = scripted(*[tool_call("get_market_data", {"asset": "BTC"}, f"c{i}") for i in range(6)], "primeira",
                   *[tool_call("get_market_data", {"asset": "ETH"}, f"d{i}") for i in range(6)], "segunda")
    verifier = ScriptedVerifier(result("contradicted"), result("verified"))
    run = run_react(build_react_graph(llm, tools(), verifier=verifier), "x", config=graph_config(6))
    assert run.answer == "segunda" and len(run.steps) == 12


def test_history_question_without_price_history_uses_baseline_rule():
    """Regra do projeto-base (caso real de 2026-11-13): preço em dias passados sem get_price_history → correção."""
    llm = scripted(tool_call("query_market_database", {"question": "BTC 90 dias"}, "c1"),
                   "O banco local não retornou registros.", "Sem histórico disponível.")
    run = run_react(build_react_graph(llm, tools(sql_ok=False),
                                      verifier=ScriptedVerifier(result("verified"), result("verified"))),
                    "Preço do BTC nos últimos 90 dias?")
    feedback = next(m for m in run.messages if isinstance(m, HumanMessage) and "Revisão automática" in m.content)
    assert "get_price_history (asset='BTC', days=90)" in feedback.content
