"""Projeto LangGraph, Etapa 1: compatibilidade real do tool calling do Ollama com o StateGraph ReAct.

Não presume compatibilidade pelo nome do modelo: verifica, no grafo novo (``ToolNode`` + ``tools_condition``),
vínculo das ferramentas, escolha, execução, uso do resultado, chamadas consecutivas e resposta final.
Ferramentas reais: ``get_market_data`` (CoinPaprika) e ``calculate_percent_change`` (determinística).

Execução: pytest -m "integration or network" tests/integration/test_graph_react_live.py -v -s
"""

import json
import re

import httpx
import pytest

from app.config import get_agent_settings, get_settings
from app.graph import build_react_graph, graph_config, run_react
from app.integrated.tools import structured_tools
from app.services.llm import build_llm
from app.tools.calculation_tools import build_calculation_tools
from app.tools.coinpaprika import CoinPaprikaClient
from app.tools.market_tools import build_market_tools
from tests.integration.test_phase2_live import _ollama_has

pytestmark = [pytest.mark.integration, pytest.mark.network]

STEP1_TOOLS = {"get_market_data", "calculate_percent_change"}


@pytest.fixture(scope="module")
def graph():
    settings = get_settings()
    if not _ollama_has(settings.ollama_model):
        pytest.skip("Ollama ou modelo indisponível")
    try:
        httpx.get("https://api.coinpaprika.com/v1/global", timeout=5)
    except httpx.HTTPError:
        pytest.skip("sem acesso à CoinPaprika")
    client = CoinPaprikaClient()
    tools = [t for t in structured_tools([*build_market_tools(client, profile=True), *build_calculation_tools()])
             if t.name in STEP1_TOOLS]
    assert {t.name for t in tools} == STEP1_TOOLS
    yield build_react_graph(build_llm(settings, json_mode=False), tools)
    client.close()


def ask(graph, question: str):
    run = run_react(graph, question, config=graph_config(get_agent_settings().max_iterations))
    print(json.dumps({
        "q": question, "model": get_settings().ollama_model,
        "tools": [{"tool": s.tool, "args": s.args, "ok": s.ok} for s in run.steps],
        "llm_calls": run.llm_calls, "answer": run.answer[:400], "elapsed_s": run.elapsed_s,
        "messages": [type(m).__name__ for m in run.messages],
    }, ensure_ascii=False))
    return run


def parse_number(raw: str) -> float:
    """'61,370.93' (en-US) e '61.370,93' (pt-BR): o último separador é o decimal, salvo quando é o único
    separador e vem seguido de 3 dígitos ('61,370' = milhar)."""
    last = max(raw.rfind(","), raw.rfind("."))
    if last == -1:
        return float(raw)
    integer, decimal = raw[:last], raw[last + 1:]
    if len(decimal) == 3 and "," not in integer and "." not in integer:
        return float(integer + decimal)
    return float(integer.replace(",", "").replace(".", "") + "." + decimal)


def numbers(text: str) -> list[float]:
    return [parse_number(n) for n in re.findall(r"\d[\d.,]*\d|\d", text)]


def test_single_tool_selection_execution_and_answer(graph):
    run = ask(graph, "Qual é o preço atual do Bitcoin?")
    assert [s.tool for s in run.steps] == ["get_market_data"] and run.steps[0].ok
    assert run.messages[1].tool_calls and type(run.messages[2]).__name__ == "ToolMessage"
    assert len(run.llm_calls) == 2  # agent → tools → agent
    price = run.observations[0]["data"]["price_usd"]
    assert "BTC" in run.answer.upper() or "BITCOIN" in run.answer.upper()
    assert any(abs(n - price) / price < 0.01 for n in numbers(run.answer)), "resposta não usa o preço observado"


def test_multiple_tool_calls_for_two_assets(graph):
    run = ask(graph, "Compare o preço atual do Bitcoin e do Ethereum.")
    symbols = {o["data"]["symbol"] for o in run.observations if o.get("ok") and o.get("tool") == "get_market_data"}
    assert {"BTC", "ETH"} <= symbols
    assert run.answer


def test_deterministic_tool_and_no_tool_path(graph):
    run = ask(graph, "Calcule a variação percentual de 100 para 150.")
    assert [s.tool for s in run.steps] == ["calculate_percent_change"] and run.steps[0].ok
    assert "50" in run.answer

    advice = ask(graph, "Olá! O que você consegue fazer?")
    assert advice.answer and len(advice.llm_calls) >= 1
