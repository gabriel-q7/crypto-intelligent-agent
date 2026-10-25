"""Execução do agente ReAct e montagem do ``CryptoReport`` validado.

O LLM decide as ferramentas e redige o "Final Answer". Todo o resto é determinístico:
- achados, fontes e horários saem das observações das ferramentas (``app.services.grounding``);
- valores de mercado da resposta que não existem nas observações são removidos;
- se os dados essenciais falharam, a resposta do LLM é substituída por uma explicação da falha:
  nunca sai uma análise com aparência de sucesso sem dados.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from langchain_core.agents import AgentAction
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseLanguageModel
from langchain_core.tools import BaseTool
from ollama import ResponseError

from app.config import AgentSettings, get_agent_settings, get_settings
from app.legacy.react_agent import build_react_executor
from app.memory.conversation import BoundedConversationMemory
from app.observability.callbacks import TraceCallbackHandler
from app.schemas.report import CryptoReport, ReportStatus, ToolCall
from app.services.grounding import findings_from, parse_observation, sources_from, verify_figures

logger = logging.getLogger(__name__)

MARKET_TOOLS = {"get_market_data", "get_market_details", "get_global_market"}
STOPPED_PREFIX = "Agent stopped"


def _tool_calls(steps: list[tuple[AgentAction, object]]) -> tuple[list[ToolCall], list[dict]]:
    calls, observations = [], []
    for action, observation in steps:
        if action.tool == "_Exception":  # erro de formato tratado pelo executor
            continue
        payload = parse_observation(observation) or {}
        error = payload.get("error") or {}
        calls.append(
            ToolCall(
                tool=action.tool,
                input=str(action.tool_input)[:200],
                ok=bool(payload.get("ok")),
                error_kind=error.get("kind") if not payload.get("ok") else None,
                error_message=error.get("message") if not payload.get("ok") else None,
            )
        )
        if payload:
            observations.append(payload)
    return calls, observations


def assemble_report(
    question: str,
    output: str,
    steps: list[tuple[AgentAction, object]],
    trace: TraceCallbackHandler | None = None,
    model: str | None = None,
) -> CryptoReport:
    calls, observations = _tool_calls(steps)
    uncertainties: list[str] = []
    market_calls = [c for c in calls if c.tool in MARKET_TOOLS]
    market_ok = [c for c in market_calls if c.ok]
    failed = [c for c in calls if not c.ok]
    stopped = output.strip().startswith(STOPPED_PREFIX)

    for call in failed:
        uncertainties.append(f"{call.tool}({call.input}) falhou: {call.error_kind} — {call.error_message}")

    if market_calls and not market_ok:
        status = ReportStatus.FAILED
        reasons = "; ".join(f"{c.error_kind}" for c in market_calls)
        answer = (
            "Não foi possível obter os dados de mercado necessários "
            f"({reasons}). Nenhum valor foi estimado; tente novamente mais tarde."
        )
    elif stopped:
        status = ReportStatus.PARTIAL if market_ok else ReportStatus.FAILED
        uncertainties.append("o agente atingiu o limite de iterações ou de tempo antes de concluir")
        answer = (
            "A análise não foi concluída dentro do limite de execução."
            + (" Os dados obtidos estão listados nos achados." if market_ok else " Nenhum dado de mercado foi obtido.")
        )
    else:
        answer, removed = verify_figures(output, observations)
        if removed:
            uncertainties.append(
                "valores sem correspondência nos dados das ferramentas foram removidos da resposta: "
                + ", ".join(removed)
            )
        status = ReportStatus.PARTIAL if failed else ReportStatus.COMPLETE
        if not answer:
            answer = "A resposta gerada continha apenas valores não confirmados e foi descartada."
            status = ReportStatus.FAILED if market_calls or removed else status

    if not calls:
        uncertainties.append("nenhuma ferramenta foi usada: resposta baseada apenas no conhecimento do modelo")

    decisions = [e.name + " " + e.detail for e in trace.events if e.kind == "decision"] if trace else [
        f"{c.tool} input={c.input}" for c in calls
    ]
    return CryptoReport(
        question=question,
        mode="react_agent",
        status=status,
        answer=answer,
        findings=findings_from(observations),
        data_sources=sources_from(observations),
        uncertainties=uncertainties,
        tool_calls=calls,
        decision_summary=decisions,
        model=model,
    )


def latest_market_timestamp(report: CryptoReport) -> datetime | None:
    stamps = [s.retrieved_at for s in report.data_sources if s.retrieved_at and s.tool in MARKET_TOOLS]
    return max(stamps) if stamps else None


class CryptoAgent:
    """Agente ReAct com memória limitada e rastreamento."""

    def __init__(
        self,
        llm: BaseLanguageModel,
        tools: list[BaseTool],
        settings: AgentSettings | None = None,
        memory: BoundedConversationMemory | None = None,
        executor_factory: Callable = build_react_executor,
        model_name: str | None = None,
    ) -> None:
        self.settings = settings or get_agent_settings()
        self.memory = memory or BoundedConversationMemory(
            self.settings.memory_max_turns, self.settings.memory_max_chars
        )
        self.executor = executor_factory(llm, tools, self.settings)
        self.model_name = model_name or get_settings().ollama_model
        self.last_trace: TraceCallbackHandler | None = None
        # Observações brutas da última execução (auditoria e testes de integração).
        self.last_observations: list[dict] = []

    def run(self, question: str) -> CryptoReport:
        trace = TraceCallbackHandler()
        self.last_trace = trace
        inputs = {
            "input": question,
            "history": self.memory.render(),
            "reference_time": datetime.now(UTC).isoformat(timespec="minutes"),
        }
        try:
            result = self.executor.invoke(inputs, config={"callbacks": [trace]})
        except (ConnectionError, httpx.HTTPError, ResponseError) as exc:
            logger.error("agent llm unavailable: %s", exc)
            return self._failure(question, f"modelo indisponível: {exc}")
        except OutputParserException as exc:
            logger.error("agent output parsing failed: %s", exc)
            return self._failure(question, f"resposta do modelo fora do formato ReAct: {exc}")

        steps = result.get("intermediate_steps", [])
        self.last_observations = _tool_calls(steps)[1]
        report = assemble_report(question, result.get("output", ""), steps, trace, self.model_name)
        logger.info(
            "agent finished status=%s tool_calls=%d removed_or_uncertain=%d",
            report.status, len(report.tool_calls), len(report.uncertainties),
        )
        self.memory.add(question, report.answer, latest_market_timestamp(report))
        return report

    def _failure(self, question: str, reason: str) -> CryptoReport:
        return CryptoReport(
            question=question,
            mode="react_agent",
            status=ReportStatus.FAILED,
            answer=f"Não foi possível concluir a análise: {reason}",
            uncertainties=[reason],
            model=self.model_name,
        )
