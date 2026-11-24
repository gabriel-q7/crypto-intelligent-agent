"""Projeto LangGraph, Etapa 3: checkpointer SQLite, thread_id, continuação, reinício, isolamento e retomada."""

import json
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph import build_react_graph, open_checkpointer, resume_react, run_react, thread_id_for
from app.graph.checkpointer import close_checkpointer, delete_session_threads, pending_nodes, thread_config
from tests.unit.phase5_fakes import ScriptedToolChat, ScriptedVerifier, result, scripted, tool_call
from tests.unit.test_graph_react import market_tool


class SpyChat(ScriptedToolChat):
    """Chat roteirizado que registra as mensagens recebidas e pode falhar na N-ésima chamada (uma vez)."""

    seen: list = []
    fail_on: int = 0
    calls: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.calls += 1
        self.seen.append(list(messages))
        if self.calls == self.fail_on:
            raise ConnectionError("Ollama fora do ar")
        return super()._generate(messages, *args, **kwargs)


def spy(*messages, fail_on: int = 0) -> SpyChat:
    base = scripted(*messages)
    return SpyChat(messages=base.messages, seen=[], fail_on=fail_on)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "checkpoints.sqlite3"


def contents(messages) -> list[str]:
    return [m.content for m in messages if isinstance(m, HumanMessage | AIMessage) and m.content]


# --------------------------------------------------------------------------- 1–8: continuação entre requisições


def test_follow_up_resumes_from_checkpoint_in_a_separate_request(db):
    saver = open_checkpointer(db)
    calls: list = []
    llm = spy(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC está em US$ 81.545,05 (CoinPaprika).",
              tool_call("get_market_data", {"asset": "ETH"}, "c2"), "O ETH está em US$ 2.426,11; o BTC, US$ 81.545,05.")
    graph = build_react_graph(llm, [market_tool(calls)], checkpointer=saver)
    thread = thread_config(thread_id_for("sessao-a"))

    first = run_react(graph, "Qual o preço do BTC?", config=thread)              # 1–2: conversa e execução
    saved = graph.get_state(thread)                                               # 3: checkpoint persistido
    assert saved.next == () and saved.values["final_response"] == first.answer
    assert len(saved.values["messages"]) == 4

    second = run_react(graph, "E o ETH, comparado a ele?", config=thread)        # 5: nova requisição, mesma thread
    turn2_input = llm.seen[2]                                                     # 6: estado anterior recuperado
    assert any(isinstance(m, ToolMessage) and "81545.05" in m.content for m in turn2_input)
    assert "Qual o preço do BTC?" in contents(turn2_input)
    assert calls == ["BTC", "ETH"]                                                # 7–8: continua sem repetir o BTC
    assert [s.tool for s in second.steps] == ["get_market_data"]                  # evidência só do turno atual
    assert second.observations[0]["data"]["symbol"] == "ETH"
    assert len(second.llm_calls) == 2                                             # métricas zeradas por turno
    assert len(graph.get_state(thread).values["messages"]) == 8
    close_checkpointer(saver)


def test_turn_fields_are_reset_so_each_turn_gets_its_own_correction(db):
    saver = open_checkpointer(db)
    llm = scripted("BTC US$ 90.000.", "BTC US$ 81.545,05.", "ETH US$ 9.000.", "ETH US$ 2.426,11.")
    verifier = ScriptedVerifier(result("contradicted"), result("verified"), result("contradicted"), result("verified"))
    graph = build_react_graph(llm, [market_tool()], verifier=verifier, checkpointer=saver)
    thread = thread_config(thread_id_for("sessao-b"))

    assert run_react(graph, "Preço do BTC?", config=thread).answer == "BTC US$ 81.545,05."
    second = run_react(graph, "E do ETH?", config=thread)
    assert second.answer == "ETH US$ 2.426,11." and second.original_answer == "ETH US$ 9.000."
    assert len(verifier.calls) == 4
    close_checkpointer(saver)


def test_history_argument_is_rejected_on_a_thread_with_state(db):
    saver = open_checkpointer(db)
    graph = build_react_graph(scripted("a", "b"), [market_tool()], checkpointer=saver)
    thread = thread_config("t1")
    run_react(graph, "oi", config=thread)
    with pytest.raises(ValueError, match="checkpoint"):
        run_react(graph, "de novo", history=[HumanMessage(content="oi")], config=thread)
    close_checkpointer(saver)


# --------------------------------------------------------------------------- 9–10: reinício da aplicação


def test_checkpoint_survives_restart_new_connection_and_new_graph(db):
    saver = open_checkpointer(db)
    graph = build_react_graph(scripted(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC US$ 81.545,05."),
                              [market_tool()], checkpointer=saver)
    run_react(graph, "Preço do BTC?", config=thread_config("sessao-c"))
    close_checkpointer(saver)                                                     # "desliga" a aplicação

    saver = open_checkpointer(db)                                                 # nova conexão, novo grafo
    llm = spy("Você perguntou sobre o BTC: US$ 81.545,05.")
    graph = build_react_graph(llm, [market_tool()], checkpointer=saver)
    assert len(graph.get_state(thread_config("sessao-c")).values["messages"]) == 4
    run = run_react(graph, "O que eu perguntei antes?", config=thread_config("sessao-c"))
    assert "Preço do BTC?" in contents(llm.seen[0])
    assert run.answer.startswith("Você perguntou sobre o BTC")
    close_checkpointer(saver)


def test_checkpoint_is_readable_from_a_new_process(db):
    saver = open_checkpointer(db)
    graph = build_react_graph(scripted("BTC US$ 81.545,05."), [market_tool()], checkpointer=saver)
    run_react(graph, "Preço do BTC?", config=thread_config("sessao-d"))
    close_checkpointer(saver)

    code = f"""
import json
from app.graph import build_react_graph, open_checkpointer
from app.graph.checkpointer import thread_config
from tests.unit.phase5_fakes import scripted
from tests.unit.test_graph_react import market_tool
graph = build_react_graph(scripted("x"), [market_tool()], checkpointer=open_checkpointer({str(db)!r}))
state = graph.get_state(thread_config("sessao-d"))
print(json.dumps({{"messages": [m.content for m in state.values["messages"]], "final": state.values["final_response"]}}))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data == {"messages": ["Preço do BTC?", "BTC US$ 81.545,05."], "final": "BTC US$ 81.545,05."}


# --------------------------------------------------------------------------- 11–12: isolamento


def test_threads_are_isolated(db):
    saver = open_checkpointer(db)
    llm = spy("Resposta sobre BTC.", "Resposta sobre SOL.", "Segunda sobre BTC.")
    graph = build_react_graph(llm, [market_tool()], checkpointer=saver)
    a, b = thread_config(thread_id_for("sessao-x")), thread_config(thread_id_for("sessao-y"))

    run_react(graph, "Fale do BTC", config=a)
    run_react(graph, "Fale da SOL", config=b)
    run_react(graph, "E agora?", config=a)

    assert contents(llm.seen[1]) == ["Fale da SOL"]                        # B não vê A
    assert contents(llm.seen[2]) == ["Fale do BTC", "Resposta sobre BTC.", "E agora?"]  # A não vê B
    assert len(graph.get_state(b).values["messages"]) == 2
    close_checkpointer(saver)


def test_architectures_use_distinct_threads_and_delete_removes_them(db):
    assert thread_id_for("abc") == "abc"
    assert thread_id_for("abc", "reflexion") == "abc:reflexion"
    with pytest.raises(ValueError):
        thread_id_for("")

    saver = open_checkpointer(db)
    graph = build_react_graph(scripted("a", "b"), [market_tool()], checkpointer=saver)
    run_react(graph, "oi", config=thread_config(thread_id_for("s1")))
    run_react(graph, "oi", config=thread_config(thread_id_for("s1", "reflexion")))
    delete_session_threads(saver, "s1", ("reflexion",))
    assert graph.get_state(thread_config("s1")).values == {}
    assert graph.get_state(thread_config("s1:reflexion")).values == {}
    close_checkpointer(saver)


# --------------------------------------------------------------------------- recuperação de turno interrompido


def test_interrupted_turn_resumes_without_repeating_the_tool(db):
    saver = open_checkpointer(db)
    calls: list = []
    llm = spy(tool_call("get_market_data", {"asset": "BTC"}, "c1"), "BTC US$ 81.545,05 (CoinPaprika).", fail_on=2)
    graph = build_react_graph(llm, [market_tool(calls)], checkpointer=saver)
    thread = thread_config(thread_id_for("sessao-z"))

    with pytest.raises(ConnectionError):
        run_react(graph, "Preço do BTC?", config=thread)
    assert pending_nodes(graph, "sessao-z") == ("agent",)          # parou depois da ferramenta, antes da resposta
    assert calls == ["BTC"]

    run = resume_react(graph, thread)
    assert calls == ["BTC"]                                         # a ferramenta não rodou de novo
    assert run.answer == "BTC US$ 81.545,05 (CoinPaprika)."
    assert [s.tool for s in run.steps] == ["get_market_data"]
    assert pending_nodes(graph, "sessao-z") == ()
    close_checkpointer(saver)
