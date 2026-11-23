"""Nós do grafo ReAct. Cada nó é uma função ``estado -> atualização parcial``, testável isoladamente.

O nó ``tools`` é o ``ToolNode`` do LangGraph (montado em ``builder``); as ferramentas são as mesmas do agente
integrado (``app/integrated/tools.py``), sem regra de negócio nova.
"""

import logging
import time
from collections.abc import Callable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.graph.state import LLMCall, ReactState
from app.integrated.agent import SYSTEM_PROMPT, _text

logger = logging.getLogger(__name__)


def make_agent_node(
    llm: BaseChatModel, tools: Sequence[BaseTool], system_prompt: str = SYSTEM_PROMPT
) -> Callable[[ReactState], dict]:
    """Nó ``agent``: chama o modelo com as ferramentas vinculadas e devolve a ``AIMessage``.

    Se a mensagem tiver ``tool_calls``, ``tools_condition`` leva ao ``ToolNode``; senão, ao fim do laço.
    O modelo com ferramentas é vinculado uma vez, na montagem do grafo, e reaproveitado entre requisições.
    """
    model = llm.bind_tools(list(tools))

    def agent(state: ReactState) -> dict:
        started = time.perf_counter()
        response = model.invoke([SystemMessage(content=system_prompt), *state["messages"]])
        usage = getattr(response, "usage_metadata", None) or {}
        call: LLMCall = {
            "node": "agent",
            "latency_s": round(time.perf_counter() - started, 3),
            "tool_calls": [c["name"] for c in getattr(response, "tool_calls", None) or []],
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }
        logger.info("graph agent tool_calls=%s latency_s=%.2f", call["tool_calls"], call["latency_s"])
        return {"messages": [response], "llm_calls": [call]}

    return agent


def finalize(state: ReactState) -> dict:
    """Nó ``finalize``: a última ``AIMessage`` sem ``tool_calls`` é a resposta do turno."""
    final = next((m for m in reversed(state.get("messages", []))
                  if isinstance(m, AIMessage) and not m.tool_calls), None)
    return {"final_response": _text(final.content).strip() if final else ""}
