"""Agente integrado (Fase 5): ``langchain.agents.create_agent`` com tool calling nativo do Ollama.

Por decisão do usuário (2026-11-05), o agente não usa ``create_openai_tools_agent`` +
``AgentExecutor`` (pacote legado ``langchain-classic``) nem ``RunnableWithMessageHistory``
(depreciado no langchain-core 1.7: "Use LangGraph's built-in persistence instead"). O substituto
oficial é ``create_agent`` (LangChain 1.x, grafo LangGraph). Ver docs/agent-integration.md.

O histórico chega como mensagens (``SessionHistoryAdapter``), sem checkpointer: o SQLite da
Fase 3 continua sendo a única fonte da verdade da conversa.

A trajetória (ferramenta, argumentos, saída, ordem, erros) é lida das mensagens do grafo:
``AIMessage.tool_calls`` + ``ToolMessage`` correspondente.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool

from app.integrated.schemas import ToolStep
from app.integrated.tools import TOOL_CATEGORY
from app.prompts.ptcf import PERSONA
from app.services.grounding import parse_observation

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""{PERSONA}

Ferramentas e quando usar (chame SÓ as necessárias; várias numa mesma resposta quando a pergunta pedir):
- Preço/métricas ATUAIS de um ativo → get_market_data (uma chamada por ativo). Visão do mercado → get_global_market.
- Preço de UM ativo em dias passados (última semana, último mês, ontem, "nos dias de...") → get_price_history
  (asset e days; última semana = 7). Para histórico de preço, prefira esta ferramenta.
- Filtros, rankings, médias ou comparação com coletas anteriores do BANCO LOCAL de snapshots → query_market_database
  (passe a pergunta completa). São dados armazenados, com horário de coleta; não são cotação ao vivo.
  Se o banco não tiver registros do período pedido, chame get_price_history em seguida; não pare na falta de dados.
- Conceitos (o que é, como funciona, diferença entre...) → search_knowledge_base com a pergunta conceitual completa,
  MESMO que a pergunta também peça dados de mercado.
- Comparação entre redes/blockchains ("X vs Y", "compare X e Y"): as consultas (base de conhecimento, perfil técnico e
  mercado de cada rede) já chegam feitas na conversa; use essas observações e não repita as chamadas.
- Perfil técnico de uma rede (consenso, código aberto, descrição, whitepaper) → get_asset_profile,
  sobretudo quando a base de conhecimento não cobre a rede.
- Variação entre dois números já obtidos → calculate_percent_change.

Regras:
1. Nunca invente valores de mercado: todo número vem de uma ferramenta desta conversa. Cite a fonte e o horário.
2. Diferencie dado ao vivo (CoinPaprika) de snapshot armazenado (SQLite) e de documento (base local).
3. Se uma ferramenta falhar ou não tiver dados (ok=false), tente a alternativa (banco local sem registros → get_price_history); só então diga o que faltou. Não estime nem diga que há histórico que não existe.
4. Afirmações conceituais só a partir dos trechos de search_knowledge_base ou do perfil de get_asset_profile (cite
   a CoinPaprika); o que nenhum dos dois trouxer (ex.: finalidade de uma rede fora da base), diga que não foi encontrado.
5. Valores citados em mensagens anteriores da conversa são antigos: consulte de novo se o usuário pedir dado atual.
6. Sem previsões de preço nem recomendações de investimento ("compre", "venda", "qual é melhor para investir").
   Um pedido de recomendação ou de previsão sobre ativos citados NÃO dispensa a consulta: chame get_market_data
   para cada ativo citado, apresente a comparação objetiva dos dados (com fonte e horário) e diga que não faz
   recomendação de investimento. Nunca diga que não tem dados sem antes chamar a ferramenta.
7. Se a pergunta citar um nome ou símbolo, consulte as ferramentas mesmo que ele pareça desconhecido (a ferramenta
   dirá se não existe); só peça esclarecimento quando nenhum ativo for citado nem estiver no histórico.
8. Em comparação entre redes, organize em (a) tecnologia — consenso, contratos inteligentes, finalidade e
   escalabilidade de cada rede, a partir dos trechos — e (b) mercado — dados com fonte e horário.
9. Responda em português, em 2 a 6 frases objetivas; em comparação entre redes, até 12 frases ou tópicos curtos."""


@dataclass
class AgentRun:
    answer: str
    steps: list[ToolStep] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)


def build_integrated_agent(llm: BaseChatModel, tools: Sequence[BaseTool]) -> Runnable:
    return create_agent(llm, list(tools), system_prompt=SYSTEM_PROMPT, name="crypto_integrated_agent")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return str(content)


def trajectory(messages: Sequence[BaseMessage], start: int = 0) -> tuple[list[ToolStep], list[dict]]:
    """Passos de ferramenta a partir de ``start`` (índice da 1ª mensagem nova), na ordem de execução."""
    calls: dict[str, dict] = {}
    steps: list[ToolStep] = []
    observations: list[dict] = []
    for message in messages[start:]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                calls[call["id"]] = call
        elif isinstance(message, ToolMessage):
            call = calls.get(message.tool_call_id, {})
            output = _text(message.content)
            payload = parse_observation(output) or {}
            error = payload.get("error") or {}
            name = call.get("name") or message.name or "?"
            steps.append(ToolStep(
                order=len(steps) + 1,
                tool=name,
                category=TOOL_CATEGORY.get(name, "unknown"),
                args=call.get("args", {}),
                ok=bool(payload.get("ok")) and message.status != "error",
                error_kind=error.get("kind") if not payload.get("ok") else None,
                error_message=error.get("message") if not payload.get("ok") else None,
                output=output[:4000],
            ))
            if payload:
                observations.append(payload)
    return steps, observations


def run_agent(
    agent: Runnable,
    history: Sequence[BaseMessage],
    message: str,
    config: RunnableConfig | None = None,
    extra: Sequence[BaseMessage] = (),
) -> AgentRun:
    """Executa um turno. ``extra`` são mensagens já trocadas neste turno (usado na correção,
    para reaproveitar as observações sem repetir as chamadas)."""
    inputs = [*history, HumanMessage(content=message), *extra]
    result = agent.invoke({"messages": inputs}, config=config)
    messages = result["messages"]
    final = next((m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None)
    # Só as mensagens novas deste invoke (na correção, as observações anteriores vêm em ``extra``).
    steps, observations = trajectory(messages, start=len(inputs))
    answer = _text(final.content).strip() if final else ""
    logger.info("integrated agent tools=%s answer_chars=%d", [s.tool for s in steps], len(answer))
    return AgentRun(answer=answer, steps=steps, observations=observations, messages=list(messages))


def prefetch_messages(tools: Mapping[str, BaseTool], calls: Sequence[tuple[str, dict]]) -> list[BaseMessage]:
    """Executa ``calls`` no código e devolve-as como se o modelo as tivesse pedido (``AIMessage.tool_calls`` +
    ``ToolMessage``). O grafo segue a partir daí e ``trajectory`` lê esses passos como os demais."""
    requested, results = [], []
    for i, (name, args) in enumerate(calls, start=1):
        if name not in tools:
            continue
        call_id = f"prefetch-{i}"
        requested.append({"name": name, "args": args, "id": call_id, "type": "tool_call"})
        try:
            output = tools[name].invoke(args)
        except Exception as exc:  # a ferramenta já devolve JSON de erro; isto cobre falha inesperada
            output = json.dumps({"ok": False, "tool": name, "error": {"kind": "tool_error", "message": str(exc)}})
        results.append(ToolMessage(content=_text(output), tool_call_id=call_id, name=name))
    if not requested:
        return []
    return [AIMessage(content="", tool_calls=requested), *results]


def observations_json(observations: list[dict]) -> str:
    return json.dumps(observations, ensure_ascii=False, default=str)
