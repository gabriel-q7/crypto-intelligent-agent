"""Roteamento condicional do grafo ReAct.

- Depois do ``agent``: ``tools_condition`` (LangGraph) lê a última mensagem; com ``tool_calls`` devolve
  ``"tools"``, sem devolve ``END``. O mapa troca ``END`` pelo nó seguinte (coleta de evidência).
- Depois da verificação: correção única (política do projeto-base) ou finalização.
"""

from collections.abc import Callable

from langgraph.graph import END
from langgraph.prebuilt import tools_condition

from app.graph.nodes import as_agent_run, verification_of
from app.graph.state import ReactState
from app.integrated.service import should_correct

route_after_agent = tools_condition


def agent_routes(after_loop: str) -> dict[str, str]:
    """Destinos de ``route_after_agent``: ferramentas, ou ``after_loop`` quando o modelo não pede ferramenta."""
    return {"tools": "tools", END: after_loop}


def make_route_after_verification(max_corrections: int) -> Callable[[ReactState], str]:
    """``revise_response`` se a verificação ou as regras de falha do agente pedirem e ainda houver tentativa;
    senão ``finalize``. O limite torna o laço de correção finito."""
    def route_after_verification(state: ReactState) -> str:
        if state.get("correction_attempts", 0) >= max_corrections:
            return "finalize"
        verification = verification_of(state)
        if verification and should_correct(state["user_query"], as_agent_run(state), verification):
            return "revise_response"
        return "finalize"

    return route_after_verification
