"""Nós do grafo ReAct. Cada nó é uma função ``estado -> atualização parcial``, testável isoladamente.

Nenhuma regra de negócio nova: ferramentas, prompt, pré-consulta de comparações, verificação, feedback de
correção, resposta cautelosa e ajustes finais são os do projeto-base (``app/integrated``, ``app/verification``).
O que muda é que cada etapa virou um nó e cada decisão, uma aresta.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.graph.state import LLMCall, ReactState
from app.integrated.agent import SYSTEM_PROMPT, AgentRun, _text, prefetch_messages, trajectory
from app.integrated.schemas import ToolStep
from app.integrated.service import (
    comparison_calls,
    comparison_instruction,
    correction_feedback,
    deliverable_answer,
    network_comparison,
    reply_status,
    settle_correction,
)
from app.verification.schemas import VerificationResult
from app.verification.service import VerificationService

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- leitura do estado


def turn_messages(state: ReactState) -> list[BaseMessage]:
    """Mensagens do turno atual: da ``HumanMessage`` da pergunta (``turn_id``) em diante."""
    messages = state.get("messages", [])
    turn_id = state.get("turn_id")
    start = next((i for i, m in enumerate(messages) if turn_id and m.id == turn_id), 0)
    return messages[start:]


def tool_steps(state: ReactState) -> list[ToolStep]:
    return [ToolStep.model_validate({k: v for k, v in r.items() if k in ToolStep.model_fields})
            for r in state.get("tool_results", [])]


def verification_of(state: ReactState) -> VerificationResult | None:
    data = state.get("verification_result")
    return VerificationResult.model_validate(data) if data else None


def as_agent_run(state: ReactState) -> AgentRun:
    """Visão do estado no formato das heurísticas do projeto-base (``skipped_*``, ``correction_feedback``)."""
    return AgentRun(answer=state.get("draft_answer", ""), steps=tool_steps(state),
                    observations=list(state.get("evidence", [])))


# --------------------------------------------------------------------------- nós


def make_prefetch_node(tools: Sequence[BaseTool]) -> Callable[[ReactState], dict]:
    """Nó ``prefetch``: em comparação entre redes, o código consulta base, perfil e mercado antes do modelo.

    Mesma regra do projeto-base (Fase 5, seção 5.11: o modelo de 2B ignorava o roteamento nesse caso). Para as
    demais perguntas não faz nada: a ordem das ferramentas continua com o modelo.
    """
    by_name: Mapping[str, BaseTool] = {t.name: t for t in tools}

    def prefetch(state: ReactState) -> dict:
        message = state["user_query"]
        assets = network_comparison(message)
        if not assets:
            return {}
        messages = prefetch_messages(by_name, comparison_calls(message, assets))
        if not messages:
            return {}
        logger.info("graph prefetch assets=%s tools=%d", assets, len(messages) - 1)
        return {"messages": [*messages, HumanMessage(content=comparison_instruction(assets))]}

    return prefetch


def make_agent_node(
    llm: BaseChatModel, tools: Sequence[BaseTool], system_prompt: str = SYSTEM_PROMPT
) -> Callable[[ReactState], dict]:
    """Nó ``agent``: chama o modelo com as ferramentas vinculadas e devolve a ``AIMessage``.

    Se a mensagem tiver ``tool_calls``, ``tools_condition`` leva ao ``ToolNode``; senão, à coleta de evidência.
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


def timed_tool_call(request, execute):
    """``wrap_tool_call`` do ``ToolNode``: mede a latência de cada ferramenta (``response_metadata.latency_s``)."""
    started = time.perf_counter()
    result = execute(request)
    if isinstance(result, ToolMessage):
        result.response_metadata = {**result.response_metadata, "latency_s": round(time.perf_counter() - started, 3)}
    return result


def collect_evidence(state: ReactState) -> dict:
    """Nó ``collect_evidence``: consolida trajetória, observações e resposta atual do turno inteiro
    (pré-consulta, laço ReAct e, se houve, a correção)."""
    messages = turn_messages(state)
    steps, observations = trajectory(messages)
    latencies = [m.response_metadata.get("latency_s") for m in messages if isinstance(m, ToolMessage)]
    results = [{**s.model_dump(mode="json"), "latency_s": lat} for s, lat in zip(steps, latencies, strict=True)]
    final = next((m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None)
    return {"tool_results": results, "evidence": observations,
            "draft_answer": _text(final.content).strip() if final else ""}


def make_verify_node(verifier: VerificationService) -> Callable[[ReactState], dict]:
    """Nó ``verify_response``: NLI por afirmação + coerência lógica contra as observações reais (projeto-base)."""

    def verify_response(state: ReactState) -> dict:
        result = verifier.verify(state["user_query"], state.get("draft_answer", ""), list(state.get("evidence", [])))
        return {"verification_result": result.model_dump(mode="json")}

    return verify_response


def revise_response(state: ReactState) -> dict:
    """Nó ``revise_response``: feedback estruturado (verificação + falhas do agente) como nova mensagem; o agente
    reescreve a partir das próprias observações, sem repetir as chamadas."""
    verification = verification_of(state)
    feedback = correction_feedback(state["user_query"], as_agent_run(state), verification)
    return {
        "messages": [HumanMessage(content=feedback)],
        "correction_attempts": state.get("correction_attempts", 0) + 1,
        "original_answer": state.get("draft_answer", ""),
        "original_status": verification.status,
    }


def finalize(state: ReactState) -> dict:
    """Nó ``finalize``: resposta cautelosa se a correção não passou, ajustes garantidos por código, status."""
    answer = state.get("draft_answer", "")
    verification = verification_of(state)
    steps = tool_steps(state)
    if verification is None:  # grafo sem verificação
        return {"final_response": answer, "execution_metadata": {"status": None, "limitations": []}}
    if state.get("correction_attempts"):
        answer, verification = settle_correction(answer, verification, state.get("original_status") or "")
    answer = deliverable_answer(state["user_query"], answer, verification)
    limitations = [f"{s.tool}: {s.error_kind} — {s.error_message}" for s in steps if not s.ok]
    limitations += verification.verifier_errors
    return {
        "final_response": answer,
        "verification_result": verification.model_dump(mode="json"),
        "execution_metadata": {"status": reply_status(steps, verification),
                               "limitations": list(dict.fromkeys(limitations))},
    }
