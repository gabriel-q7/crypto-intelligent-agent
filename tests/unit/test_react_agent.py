"""Agente ReAct clássico com LLM roteirizado (FakeListLLM) e ferramentas falsas."""

import json

from langchain_core.language_models.fake import FakeListLLM
from langchain_core.tools import Tool

from app.agents.runner import CryptoAgent
from app.config import AgentSettings
from app.legacy.react_agent import build_react_executor
from app.prompts.ptcf import FEW_SHOT_EXAMPLES, REACT_INPUT_VARIABLES, react_prefix
from app.schemas.report import ReportStatus
from tests.unit.test_grounding import OBS_BTC

SETTINGS = AgentSettings(max_iterations=3, max_execution_time_s=30)


def market_tool(observation: dict, calls: list | None = None) -> Tool:
    def run(value: str) -> str:
        if calls is not None:
            calls.append(value)
        return json.dumps(observation)

    return Tool(name="get_market_data", func=run, description="dados de mercado")


def agent_with(responses: list[str], observation: dict = OBS_BTC, calls: list | None = None) -> CryptoAgent:
    return CryptoAgent(FakeListLLM(responses=responses), [market_tool(observation, calls)], SETTINGS, model_name="fake")


ACTION_BTC = "Thought: preciso de dados atuais.\nAction: get_market_data\nAction Input: BTC"


def test_executor_uses_legacy_zero_shot_react_with_ptcf_prompt():
    executor = build_react_executor(FakeListLLM(responses=["x"]), [market_tool(OBS_BTC)], SETTINGS)
    template = executor.agent.llm_chain.prompt.template
    assert "# Persona" in template and "# Tarefa" in template and "# Contexto" in template and "# Formato" in template
    assert "get_market_data" in template
    assert set(executor.agent.llm_chain.prompt.input_variables) == set(REACT_INPUT_VARIABLES)
    assert executor.max_iterations == 3
    assert executor.return_intermediate_steps is True


def test_few_shots_cover_required_scenarios():
    titles = {e.title for e in FEW_SHOT_EXAMPLES}
    assert titles == {"consulta de mercado", "comparação de ativos", "informação ausente", "falha da API"}
    assert "{{" in react_prefix()  # JSON dos exemplos escapado para o PromptTemplate


def test_agent_calls_tool_and_grounds_answer():
    calls: list = []
    agent = agent_with([ACTION_BTC, "Thought: ok.\nFinal Answer: O BTC está em US$ 81.528,29 (CoinPaprika)."], calls=calls)
    report = agent.run("Qual o preço do BTC?")
    assert calls == ["BTC"]
    assert report.status is ReportStatus.COMPLETE
    assert "81.528,29" in report.answer
    assert report.data_sources[0].endpoint == "/tickers/btc-bitcoin"
    assert report.findings and report.decision_summary == ["get_market_data input=BTC"]
    assert agent.last_trace and any(e.kind == "tool" and e.ok for e in agent.last_trace.events)


def test_invented_values_are_removed():
    agent = agent_with([ACTION_BTC, "Thought: ok.\nFinal Answer: O BTC está em US$ 81.528,29. Vai a US$ 150.000 em dezembro."])
    report = agent.run("BTC?")
    assert "150.000" not in report.answer
    assert any("removidos" in u for u in report.uncertainties)


def test_api_failure_never_returns_successful_analysis():
    failure = {"ok": False, "tool": "get_market_data", "error": {"kind": "rate_limited", "message": "limite"}}
    # Mesmo que o modelo "invente" um preço, a falha substitui a resposta.
    agent = agent_with([ACTION_BTC, "Thought: ok.\nFinal Answer: O BTC está em US$ 80.000."], observation=failure)
    report = agent.run("Preço do BTC?")
    assert report.status is ReportStatus.FAILED
    assert "80.000" not in report.answer and "rate_limited" in report.answer
    assert report.findings == [] and report.data_sources == []


def test_iteration_limit_is_explicit_failure():
    agent = agent_with([ACTION_BTC] * 5, observation={"ok": False, "tool": "get_market_data", "error": {"kind": "timeout", "message": "t"}})
    report = agent.run("BTC?")
    assert report.status is ReportStatus.FAILED
    assert len(report.tool_calls) == SETTINGS.max_iterations


def test_parsing_errors_are_fed_back_and_bounded():
    agent = agent_with(["resposta sem formato", "Thought: ok.\nFinal Answer: Preciso saber qual ativo."])
    report = agent.run("está caro?")
    assert report.status is ReportStatus.COMPLETE
    assert report.tool_calls == []
    assert any("nenhuma ferramenta" in u for u in report.uncertainties)


def test_memory_feeds_history_into_prompt_and_is_bounded():
    prompts: list[str] = []

    class RecordingLLM(FakeListLLM):
        def _call(self, prompt, stop=None, run_manager=None, **kwargs):
            prompts.append(prompt)
            return super()._call(prompt, stop, run_manager, **kwargs)

    agent = CryptoAgent(
        RecordingLLM(responses=[ACTION_BTC, "Final Answer: US$ 81.528,29.", "Final Answer: Conforme antes."]),
        [market_tool(OBS_BTC)], AgentSettings(max_iterations=3, memory_max_turns=1), model_name="fake",
    )
    agent.run("preço do BTC?")
    agent.run("e agora?")
    assert "preço do BTC?" in prompts[-1] and "históricos" in prompts[-1]
    assert len(agent.memory.turns) == 1
