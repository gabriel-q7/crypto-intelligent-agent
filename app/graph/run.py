"""Execução de um turno no grafo ReAct e leitura do resultado.

A trajetória (ferramenta, argumentos, saída, erros) é lida com ``trajectory`` do agente integrado, para que as
duas arquiteturas sejam medidas pela mesma régua.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.graph.state import LLMCall
from app.integrated.agent import trajectory
from app.integrated.schemas import ToolStep

# Cada iteração ReAct passa por 2 nós (agent + tools); +3: última chamada ao agente, finalize e a folga exigida pelo LangGraph (passos < limite).
NODES_PER_ITERATION = 2
EXTRA_STEPS = 3


@dataclass
class GraphRun:
    answer: str
    steps: list[ToolStep] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    elapsed_s: float = 0.0


def graph_config(max_iterations: int, thread_id: str | None = None, **metadata) -> RunnableConfig:
    """Limite de iterações do laço ReAct via ``recursion_limit`` (excedido → ``GraphRecursionError``)."""
    config: RunnableConfig = {
        "recursion_limit": max_iterations * NODES_PER_ITERATION + EXTRA_STEPS,
        "metadata": {"architecture": "react_graph", **metadata},
    }
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    return config


def run_react(
    graph: CompiledStateGraph,
    question: str,
    history: Sequence[BaseMessage] = (),
    config: RunnableConfig | None = None,
) -> GraphRun:
    started = time.perf_counter()
    inputs = [*history, HumanMessage(content=question)]
    state = graph.invoke({"messages": inputs, "user_query": question}, config=config)
    steps, observations = trajectory(state["messages"], start=len(inputs))
    return GraphRun(
        answer=state.get("final_response", ""),
        steps=steps,
        observations=observations,
        messages=list(state["messages"]),
        llm_calls=list(state.get("llm_calls", [])),
        elapsed_s=round(time.perf_counter() - started, 3),
    )
