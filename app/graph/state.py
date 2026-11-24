"""Estado do grafo ReAct.

Só o que a execução usa de fato. Campos ficam serializáveis em JSON (dicts, não modelos Pydantic), porque o
estado vai para o checkpoint (Etapa 3).

- ``messages``: conversa (Human, AI com ``tool_calls``, Tool). Redutor ``add_messages``: cada nó devolve só as
  mensagens novas. O prompt do sistema NÃO fica aqui: o nó do agente o acrescenta a cada chamada.
- ``user_query`` / ``turn_id``: pergunta original do turno e o ``id`` da ``HumanMessage`` que a carrega. O turno
  começa nessa mensagem: evidência e trajetória são lidas a partir dela, mesmo com histórico anterior no estado.
- ``tool_results``: passos de ferramenta do turno (``ToolStep`` + latência), na ordem de execução.
- ``evidence``: observações JSON das ferramentas (``ok``, ``data``, ``source`` com provedor e horários): a
  proveniência vem intacta da ferramenta.
- ``draft_answer``: resposta atual do agente (antes dos ajustes finais).
- ``verification_result``: ``VerificationResult`` da última verificação.
- ``correction_attempts`` / ``original_answer`` / ``original_status``: correção única (política do projeto-base).
- ``llm_calls``: uma entrada por chamada ao modelo (latência, ferramentas pedidas, tokens). Redutor de soma; a
  entrada de cada turno zera a lista (``Overwrite``), então ela mede só o turno.
- ``final_response`` / ``execution_metadata``: resposta entregue e status, limitações e contadores do turno.
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
    turn_id: str
    tool_results: list[dict]
    evidence: list[dict]
    draft_answer: str
    verification_result: dict | None
    correction_attempts: int
    original_answer: str | None
    original_status: str | None
    llm_calls: Annotated[list[LLMCall], operator.add]
    final_response: str
    execution_metadata: dict
