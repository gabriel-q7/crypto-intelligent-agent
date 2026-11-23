"""Estado do grafo ReAct.

Só o que a execução usa de fato. Campos de evidência, verificação e correção entram quando os nós que os
produzem existirem (etapas seguintes), para não carregar campos mortos no estado nem no checkpoint.

- ``messages``: conversa do turno (Human, AI com ``tool_calls``, Tool). Redutor ``add_messages``: cada nó
  devolve só as mensagens novas. O prompt do sistema NÃO fica aqui: o nó do agente o acrescenta a cada chamada,
  então ele não se repete no estado nem no checkpoint.
- ``user_query``: a pergunta original do turno, preservada mesmo que o histórico seja recortado.
- ``llm_calls``: uma entrada por chamada ao modelo (latência, ferramentas pedidas, tokens). Redutor de soma.
- ``final_response``: texto entregue ao usuário, preenchido pelo nó ``finalize``.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class LLMCall(TypedDict):
    node: str
    latency_s: float
    tool_calls: list[str]
    input_tokens: int | None
    output_tokens: int | None


class ReactState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_query: str
    llm_calls: Annotated[list[LLMCall], operator.add]
    final_response: str
