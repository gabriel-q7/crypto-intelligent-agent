"""Projeto LangGraph, Etapa 1: StateGraph ReAct explícito (agent → ToolNode → agent, tools_condition)."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START

from app.graph import build_react_graph, graph_config, run_react
from app.graph.nodes import finalize
from app.integrated.tools import AssetArgs
from tests.unit.phase5_fakes import OBS_BTC, OBS_ETH, scripted, tool_call


def market_tool(calls: list | None = None, fail: bool = False) -> StructuredTool:
    by_asset = {"BTC": OBS_BTC, "ETH": OBS_ETH}

    def get_market_data(asset: str) -> str:
        if calls is not None:
            calls.append(asset)
        if fail:
            raise RuntimeError("falha inesperada")
        return json.dumps(by_asset[asset.upper()])

    return StructuredTool.from_function(get_market_data, name="get_market_data", description="preço atual",
                                        args_schema=AssetArgs)


def two_calls(*assets: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "get_market_data", "args": {"asset": a}, "id": f"c{i}", "type": "tool_call"}
        for i, a in enumerate(assets)
    ])


# --------------------------------------------------------------------------- estrutura


def test_graph_structure_is_explicit():
    graph = build_react_graph(scripted("ok"), [market_tool()]).get_graph()
    assert set(graph.nodes) == {START, "agent", "tools", "finalize", END}
    edges = {(e.source, e.target, e.conditional) for e in graph.edges}
    assert (START, "agent", False) in edges
    assert ("agent", "tools", True) in edges       # tools_condition: há tool_calls
    assert ("agent", "finalize", True) in edges    # tools_condition: sem tool_calls
    assert ("tools", "agent", False) in edges      # volta ao raciocínio após a ferramenta
    assert ("finalize", END, False) in edges


# --------------------------------------------------------------------------- ciclo ReAct


def test_react_cycle_agent_tools_agent():
    calls: list = []
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC está em US$ 81.545,05 (CoinPaprika).")
    run = run_react(build_react_graph(llm, [market_tool(calls)]), "Preço do BTC?", config=graph_config(6))

    assert calls == ["BTC"]
    assert [type(m) for m in run.messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert run.answer == "BTC está em US$ 81.545,05 (CoinPaprika)."
    assert [s.tool for s in run.steps] == ["get_market_data"] and run.steps[0].ok
    assert run.observations == [OBS_BTC]
    assert [c["tool_calls"] for c in run.llm_calls] == [["get_market_data"], []]


def test_multiple_tool_calls_in_one_message_then_another_round():
    calls: list = []
    llm = scripted(two_calls("BTC", "ETH"), tool_call("get_market_data", {"asset": "BTC"}, "c9"), "Comparação pronta.")
    run = run_react(build_react_graph(llm, [market_tool(calls)]), "BTC vs ETH", config=graph_config(6))

    assert calls == ["BTC", "ETH", "BTC"]
    assert [s.order for s in run.steps] == [1, 2, 3]
    assert len(run.llm_calls) == 3
    assert run.answer == "Comparação pronta."


def test_no_tool_call_goes_straight_to_finalize():
    calls: list = []
    run = run_react(build_react_graph(scripted("Não faço previsões de preço."), [market_tool(calls)]), "O BTC vai subir?")
    assert calls == [] and run.steps == []
    assert [type(m) for m in run.messages] == [HumanMessage, AIMessage]
    assert run.answer == "Não faço previsões de preço."


def test_tool_exception_becomes_error_observation_and_loop_continues():
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "A consulta falhou; não há preço disponível.")
    run = run_react(build_react_graph(llm, [market_tool(fail=True)]), "Preço do BTC?")
    tool_msg = next(m for m in run.messages if isinstance(m, ToolMessage))
    assert tool_msg.status == "error"
    assert run.steps[0].ok is False
    assert run.steps[0].error_kind == "tool_error" and "falha inesperada" in run.steps[0].error_message
    assert run.answer.startswith("A consulta falhou")


def test_iterations_are_bounded():
    llm = scripted(*[tool_call("get_market_data", {"asset": "BTC"}, f"c{i}") for i in range(20)])
    with pytest.raises(GraphRecursionError):
        run_react(build_react_graph(llm, [market_tool()]), "loop", config=graph_config(2))


def test_limit_allows_exactly_max_iterations():
    llm = scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"),
                   tool_call("get_market_data", {"asset": "ETH"}, "c2"), "fim")
    run = run_react(build_react_graph(llm, [market_tool()]), "dois passos", config=graph_config(2))
    assert len(run.steps) == 2 and run.answer == "fim"


# --------------------------------------------------------------------------- estado


def test_system_prompt_is_sent_but_not_stored_in_state():
    seen: list = []
    llm = scripted("resposta")
    original = llm.invoke

    def spy(messages, *args, **kwargs):
        seen.append(messages)
        return original(messages, *args, **kwargs)

    object.__setattr__(llm, "invoke", spy)
    run = run_react(build_react_graph(llm, [market_tool()], system_prompt="SISTEMA"), "oi")
    assert isinstance(seen[0][0], SystemMessage) and seen[0][0].content == "SISTEMA"
    assert not any(isinstance(m, SystemMessage) for m in run.messages)


def test_history_and_user_query_are_kept():
    history = [HumanMessage(content="Preço do BTC?"), AIMessage(content="US$ 81 mil.")]
    graph = build_react_graph(scripted("E o ETH está em US$ 2.426."), [market_tool()])
    state = graph.invoke({"messages": [*history, HumanMessage(content="E o ETH?")], "user_query": "E o ETH?"})
    assert state["user_query"] == "E o ETH?"
    assert len(state["messages"]) == 4


def test_finalize_ignores_ai_messages_with_tool_calls():
    state = {"messages": [HumanMessage(content="x"), tool_call("get_market_data", {"asset": "BTC"}, "c1")]}
    assert finalize(state) == {"final_response": ""}


def test_graph_config_sets_limit_thread_and_metadata():
    config = graph_config(6, thread_id="s1", session="abc")
    assert config["recursion_limit"] == 15
    assert config["configurable"] == {"thread_id": "s1"}
    assert config["metadata"] == {"architecture": "react_graph", "session": "abc"}
