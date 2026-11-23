"""Montagem do grafo ReAct (separada da inicialização da aplicação).

    START → agent ─┬─(tool_calls)→ tools → agent   (laço ReAct)
                   └─(sem tool_calls)→ finalize → END

O grafo é compilado uma vez e reaproveitado entre requisições: os nós não guardam estado próprio (tudo vive
em ``ReactState``), então o mesmo objeto atende chamadas concorrentes.
"""

import json
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.graph.nodes import finalize, make_agent_node
from app.graph.routing import agent_routes, route_after_agent
from app.graph.state import ReactState
from app.integrated.agent import SYSTEM_PROMPT


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
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    name: str = "react_graph",
) -> CompiledStateGraph:
    graph = StateGraph(ReactState)
    graph.add_node("agent", make_agent_node(llm, tools, system_prompt))
    graph.add_node("tools", ToolNode(list(tools), handle_tool_errors=tool_error_observation))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, agent_routes("finalize"))
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name=name)
