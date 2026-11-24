"""Projeto LangGraph, Etapa 3: persistência real do grafo (Ollama, ferramentas e verificador reais).

Roteiro do enunciado (seção 8.4): conversa → execução → checkpoint → fim da requisição → nova requisição com o
mesmo thread_id → estado recuperado → pergunta de seguimento → continuação → reinício (processo novo) →
checkpoint disponível → outra thread → isolamento.

Execução: pytest -m "integration or network" tests/integration/test_graph_persistence_live.py -v -s
"""

import json
import os
import subprocess
import sys
import uuid

import httpx
import pytest

from app.api.container import AppContainer
from app.config import ConversationSettings, get_agent_settings, get_settings
from app.graph import build_react_graph, graph_config, open_checkpointer, run_react, thread_id_for
from app.graph.checkpointer import close_checkpointer, thread_config
from tests.integration.test_phase2_live import _ollama_has

pytestmark = [pytest.mark.integration, pytest.mark.network]

SESSION_A = uuid.uuid4().hex
SESSION_B = uuid.uuid4().hex


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    if not _ollama_has(get_settings().ollama_model):
        pytest.skip("Ollama ou modelo indisponível")
    try:
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        pytest.skip("sem acesso à CoinPaprika")
    root = tmp_path_factory.mktemp("persist")
    c = AppContainer(conversation_settings=ConversationSettings(db_path=str(root / "conv.sqlite3")),
                     background_maintenance=False)
    saver = open_checkpointer(root / "checkpoints.sqlite3")
    graph = build_react_graph(c.tool_llm, list(c.integrated.tools.values()), verifier=c.integrated.verifier,
                              prefetch=True, checkpointer=saver)
    yield {"graph": graph, "saver": saver, "db": root / "checkpoints.sqlite3"}
    close_checkpointer(saver)
    c.close()


def ask(env, case: str, session: str, question: str):
    thread = thread_id_for(session)
    run = run_react(env["graph"], question,
                    config=graph_config(get_agent_settings().max_iterations, thread_id=thread, case=case))
    state = env["graph"].get_state(thread_config(thread))
    print(json.dumps({
        "case": case, "thread": thread[:8], "q": question,
        "tools": [{"tool": s.tool, "args": s.args, "ok": s.ok} for s in run.steps],
        "llm_calls": len(run.llm_calls), "input_tokens": sum(c["input_tokens"] or 0 for c in run.llm_calls),
        "thread_messages": len(state.values["messages"]),
        "checkpoints": sum(1 for _ in env["graph"].get_state_history(thread_config(thread))),
        "verification": run.verification.summary() if run.verification else None,
        "answer": run.answer[:400], "elapsed_s": run.elapsed_s,
    }, ensure_ascii=False))
    return run, state


def test_1_first_turn_is_checkpointed(env):
    run, state = ask(env, "turn-1", SESSION_A, "Qual é o preço atual do Bitcoin?")
    assert "get_market_data" in {s.tool for s in run.steps}
    assert state.next == () and state.values["final_response"] == run.answer


def test_2_contextual_follow_up_uses_previous_turn(env):
    run, state = ask(env, "follow-up", SESSION_A, "E o Ethereum? Qual dos dois teve a maior variação em 24h?")
    symbols = {o["data"]["symbol"] for o in run.observations if o.get("ok") and o.get("tool") == "get_market_data"}
    assert "ETH" in symbols
    text = run.answer.upper()
    assert ("BTC" in text or "BITCOIN" in text) and ("ETH" in text or "ETHEREUM" in text)


def test_3_other_thread_is_isolated(env):
    question = "Sobre qual criptomoeda eu perguntei antes nesta conversa?"
    run, state = ask(env, "isolation", SESSION_B, question)
    b = state.values["messages"]
    a = env["graph"].get_state(thread_config(thread_id_for(SESSION_A))).values["messages"]
    assert b[0].content == question                      # a thread B começa na própria pergunta
    assert not {m.id for m in a} & {m.id for m in b}     # nenhuma mensagem da thread A
    assert not any("ethereum" in str(m.content).lower() for m in b[:1])


def test_4_restart_new_process_continues_the_thread(env):
    close_checkpointer(env["saver"])  # libera o arquivo, como no desligamento da aplicação
    code = f"""
import json
from app.api.container import AppContainer
from app.config import ConversationSettings
from app.graph import build_react_graph, graph_config, open_checkpointer, run_react, thread_id_for
from app.graph.checkpointer import thread_config
c = AppContainer(conversation_settings=ConversationSettings(db_path={str(env['db'].parent / 'conv2.sqlite3')!r}),
                 background_maintenance=False)
graph = build_react_graph(c.tool_llm, list(c.integrated.tools.values()), verifier=c.integrated.verifier,
                          prefetch=True, checkpointer=open_checkpointer({str(env['db'])!r}))
thread = thread_id_for({SESSION_A!r})
before = len(graph.get_state(thread_config(thread)).values["messages"])
run = run_react(graph, "Quais foram os dois ativos que eu perguntei nesta conversa?",
                config=graph_config(6, thread_id=thread, case="restart"))
print(json.dumps({{"case": "restart", "pid_new_process": True, "messages_before": before,
                  "tools": [s.tool for s in run.steps], "answer": run.answer[:400], "elapsed_s": run.elapsed_s}},
                 ensure_ascii=False))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=900,
                         env={**os.environ, "PYTHONPATH": os.getcwd()})
    assert out.returncode == 0, out.stderr[-2000:]
    line = next(l for l in out.stdout.splitlines() if l.startswith('{"case": "restart"'))
    print(line)
    data = json.loads(line)
    assert data["messages_before"] >= 8
    text = data["answer"].upper()
    assert ("BTC" in text or "BITCOIN" in text) and ("ETH" in text or "ETHEREUM" in text)
