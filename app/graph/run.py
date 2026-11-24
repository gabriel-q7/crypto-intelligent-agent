"""Execução de um turno no grafo ReAct e leitura do resultado.

A trajetória (ferramenta, argumentos, saída, erros) é a mesma ``ToolStep`` do agente integrado, para que as
arquiteturas sejam medidas pela mesma régua.
"""

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Overwrite

from app.graph.nodes import tool_steps, verification_of
from app.graph.state import LLMCall
from app.integrated.schemas import ToolStep
from app.integrated.service import MAX_CORRECTIONS
from app.verification.schemas import VerificationResult

# Passos (super-steps) por passada do laço: 2 por iteração ReAct (agent + tools) + a última chamada ao agente
# + collect_evidence + verify_response. Fora das passadas: prefetch, revise_response (1 por correção), finalize
# e a folga que o LangGraph exige (passos < limite).
NODES_PER_ITERATION = 2
NODES_PER_PASS = 3


@dataclass
class GraphRun:
    answer: str
    steps: list[ToolStep] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    verification: VerificationResult | None = None
    original_answer: str | None = None
    tool_latencies: list[float | None] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def status(self) -> str | None:
        return self.metadata.get("status")


def recursion_limit(max_iterations: int, max_corrections: int = MAX_CORRECTIONS) -> int:
    passes = max_corrections + 1
    return 1 + passes * (max_iterations * NODES_PER_ITERATION + NODES_PER_PASS) + max_corrections + 1 + 1


def graph_config(
    max_iterations: int,
    thread_id: str | None = None,
    *,
    max_corrections: int = MAX_CORRECTIONS,
    architecture: str = "react_graph",
    **metadata,
) -> RunnableConfig:
    """Limite de passos via ``recursion_limit`` (excedido → ``GraphRecursionError``), ``thread_id`` e metadados
    para o LangSmith."""
    config: RunnableConfig = {
        "recursion_limit": recursion_limit(max_iterations, max_corrections),
        "metadata": {"architecture": architecture, **metadata},
    }
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    return config


def turn_input(question: str, history: Sequence[BaseMessage] = ()) -> dict:
    """Entrada de um turno: a pergunta ganha um ``id`` (início do turno) e os campos do turno anterior são zerados."""
    turn_id = f"turn-{uuid.uuid4().hex}"
    return {
        "messages": [*history, HumanMessage(content=question, id=turn_id)],
        "user_query": question,
        "turn_id": turn_id,
        "tool_results": [],
        "evidence": [],
        "draft_answer": "",
        "verification_result": None,
        "correction_attempts": 0,
        "original_answer": None,
        "original_status": None,
        "llm_calls": Overwrite([]),
        "final_response": "",
        "execution_metadata": {},
    }


def run_react(
    graph: CompiledStateGraph,
    question: str,
    history: Sequence[BaseMessage] = (),
    config: RunnableConfig | None = None,
) -> GraphRun:
    started = time.perf_counter()
    state = graph.invoke(turn_input(question, history), config=config)
    corrected = bool(state.get("correction_attempts"))
    return GraphRun(
        answer=state.get("final_response", ""),
        steps=tool_steps(state),
        observations=list(state.get("evidence", [])),
        messages=list(state["messages"]),
        llm_calls=list(state.get("llm_calls", [])),
        verification=verification_of(state),
        original_answer=state.get("original_answer") if corrected else None,
        tool_latencies=[r.get("latency_s") for r in state.get("tool_results", [])],
        metadata=dict(state.get("execution_metadata", {})),
        elapsed_s=round(time.perf_counter() - started, 3),
    )
