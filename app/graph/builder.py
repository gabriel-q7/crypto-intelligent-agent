"""Montagem do grafo ReAct (separada da inicialização da aplicação).

    START → [prefetch] → agent ─┬─(tool_calls)→ tools → agent              (laço ReAct)
                                └─(sem tool_calls)→ collect_evidence
    collect_evidence → [verify_response ─┬─(corrigir)→ revise_response → agent
                                         └─(ok/limite)→ finalize] → END

Nós entre colchetes são opcionais: ``prefetch`` (comparações entre redes) e a verificação (sem verificador,
``collect_evidence`` vai direto ao ``finalize``). O grafo é compilado uma vez e reaproveitado entre requisições:
os nós não guardam estado próprio (tudo vive em ``ReactState``).
"""

import json
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.graph.nodes import (
    collect_evidence,
    finalize,
    make_agent_node,
    make_prefetch_node,
    make_verify_node,
    revise_response,
    timed_tool_call,
)
from app.graph.routing import agent_routes, make_route_after_verification, route_after_agent
from app.graph.state import ReactState
from app.integrated.agent import SYSTEM_PROMPT
from app.integrated.service import MAX_CORRECTIONS
from app.verification.service import VerificationService


def tool_error_observation(exc: Exception) -> str:
    """Exceção inesperada de ferramenta → a mesma observação JSON de erro das ferramentas (``ok=false``).

    As ferramentas já devolvem JSON de erro nos casos previstos. O tratamento padrão do ``ToolNode``
    (langgraph-prebuilt 1.1.1) só converte argumentos inválidos e relança o resto, o que derrubaria o turno;
    aqui a falha vira ``ToolMessage`` com ``status="error"`` e o agente decide o que fazer com ela.
    """
    return json.dumps({"ok": False, "error": {"kind": "tool_error", "message": f"{type(exc).__name__}: {exc}"}},
                      ensure_ascii=False)


def build_react_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    *,
    verifier: VerificationService | None = None,
    max_corrections: int = MAX_CORRECTIONS,
    prefetch: bool = False,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    name: str = "react_graph",
) -> CompiledStateGraph:
    graph = StateGraph(ReactState)
    graph.add_node("agent", make_agent_node(llm, tools, system_prompt))
    graph.add_node("tools", ToolNode(list(tools), handle_tool_errors=tool_error_observation,
                                     wrap_tool_call=timed_tool_call))
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("finalize", finalize)

    if prefetch:
        graph.add_node("prefetch", make_prefetch_node(tools))
        graph.add_edge(START, "prefetch")
        graph.add_edge("prefetch", "agent")
    else:
        graph.add_edge(START, "agent")

    graph.add_conditional_edges("agent", route_after_agent, agent_routes("collect_evidence"))
    graph.add_edge("tools", "agent")

    if verifier is not None:
        graph.add_node("verify_response", make_verify_node(verifier))
        graph.add_node("revise_response", revise_response)
        graph.add_edge("collect_evidence", "verify_response")
        graph.add_conditional_edges("verify_response", make_route_after_verification(max_corrections),
                                    ["revise_response", "finalize"])
        graph.add_edge("revise_response", "agent")
    else:
        graph.add_edge("collect_evidence", "finalize")

    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name=name)
