"""Roteamento condicional do grafo ReAct.

``tools_condition`` (LangGraph) lê a última mensagem: com ``tool_calls`` devolve ``"tools"``; sem, ``END``.
O mapa troca ``END`` pelo próximo nó de processamento da resposta, em vez de encerrar o grafo ali.
"""

from langgraph.graph import END
from langgraph.prebuilt import tools_condition

route_after_agent = tools_condition


def agent_routes(after_loop: str) -> dict[str, str]:
    """Destinos de ``route_after_agent``: ferramentas, ou ``after_loop`` quando o modelo não pede ferramenta."""
    return {"tools": "tools", END: after_loop}
