# Agente integrado — Fase 5

Um único agente que combina as capacidades das Fases 1–4 — dados ao vivo (CoinPaprika), snapshots (SQLite / Text-to-Query), documentos (Chroma / Retrieval Agent) e cálculo determinístico — com histórico persistente e verificação de resposta antes da entrega.

Código:
- `app/integrated/agent.py`: agente e trajetória;
- `app/integrated/tools.py`: ferramentas estruturadas;
- `app/integrated/history.py`: adaptador de histórico;
- `app/integrated/service.py`: o turno;
- `app/integrated/schemas.py`: modelos de resposta.

Endpoint: `POST /conversations/{id}/agent`.

## 1. Desvios do enunciado (decisões do usuário em 2026-11-05)

| Enunciado pedia | Implementado | Motivo |
|---|---|---|
| `create_openai_tools_agent` + `AgentExecutor` | `langchain.agents.create_agent` (LangChain 1.4.9, grafo LangGraph 1.2.20) | O usuário pediu para não usar código legado do `langchain-classic`. As duas APIs vivem nesse pacote. `create_agent` é o substituto oficial do LangChain 1.x |
| `RunnableWithMessageHistory` | `SessionHistoryAdapter` (leitura do SQLite + Mem0) | `RunnableWithMessageHistory` emite `LangChainDeprecationWarning: RunnableWithMessageHistory is deprecated. Use LangGraph's built-in persistence instead.` no `langchain-core` 1.7.6, e `BaseChatMessageHistory` também está depreciado (verificado em 2026-11-05) |
| NLI local com Transformers (`mDeBERTa-v3-base-mnli-xnli`) | NLI com `gemma4:e2b` via Ollama | O usuário preferiu não instalar torch e Transformers (cerca de 2 GB entre biblioteca e modelo, com 5 GB livres em disco) e manter um único motor de inferência. Ver [response-verification.md](response-verification.md) |

A verificação de depreciação foi feita com `warnings.simplefilter("always")` no ambiente da imagem, nos seguintes casos:
- `create_openai_tools_agent` e `AgentExecutor`: sem aviso, mas pertencem ao pacote legado;
- `RunnableWithMessageHistory`: aviso de depreciação;
- `InMemoryChatMessageHistory`: depreciado na 1.6.4, remoção na 2.0;
- `create_agent`: sem aviso.

O LangGraph entra como dependência do `langchain` 1.4.9. O checkpointer dele **não** é usado: a conversa continua tendo uma única fonte da verdade (o SQLite da Fase 3).

**Dependências:** `langchain==1.4.9` (novo) e `langchain-core` 1.6.12 → **1.7.6**, exigido pelo `langchain` 1.4.9. A suíte offline das Fases 1–4 passou sem mudança depois da atualização (312 testes).

## 2. Compatibilidade do modelo com tool calling

Spike em 2026-11-05, com `gemma4:e2b`, `langchain-ollama` 1.1.0 e Ollama da imagem `ollama/ollama:latest`:

| Verificação | Resultado |
|---|---|
| `ChatOllama.bind_tools([...]).invoke("Qual o preço do BTC?")` | `tool_calls=[{'name': 'get_price', 'args': {'symbol': 'BTC'}}]`, 10,4 s |
| `create_agent` executa a ferramenta e incorpora o resultado | Resposta com o valor devolvido pela ferramenta, 8,3 s |
| Pergunta com duas necessidades ("explique PoS e diga o preço do ETH") | Chamou `search_docs` **e** `get_price` |
| Histórico como mensagens ("meu ativo favorito é SOL" → "preço do meu favorito") | Chamou `get_price(SOL)` |

Teste de integração permanente: `test_model_tool_calling_bind_select_execute_incorporate` (`tests/integration/test_phase5_live.py`). Ele cobre as quatro etapas: bind, seleção, execução e incorporação.

O modelo configurado **não** foi trocado. `gemma4:e2b` suporta tool calling nativo pelo Ollama, então o modo JSON (`format="json"`) fica desligado no LLM do agente (`tool_llm`) e continua ligado no LLM da verificação.

## 3. Ferramentas

`structured_tools(build_agent_tools(...))` adapta as ferramentas de entrada única das Fases 2 e 4 para argumentos tipados (Pydantic). Cada ferramenta só converte argumentos e **delega** à implementação existente; não há regra de negócio duplicada.

| Ferramenta | Categoria | Argumentos | Delegação |
|---|---|---|---|
| `get_market_data` | api | `asset` | `market_tools.get_market_data` (CoinPaprika `/tickers`) |
| `get_market_details` | api | `asset` | OHLCV do dia + perfil |
| `get_global_market` | api | — | `/global` |
| `search_crypto_asset` | api | `query` | `/search/` |
| `query_market_database` | sql | `question` | `TextToQueryService` (SQL validado, somente leitura) |
| `search_knowledge_base` | rag | `question` | `RetrievalAgent` (Query Rewriting sobre `crypto_knowledge`) |
| `calculate_percent_change` | deterministic | `old_value`, `new_value` | `calculation_tools` |

As observações mantêm o contrato da Fase 2 (`ok`, `data`, `source` com `source_updated_at`/`retrieved_at` ou `error.kind`). As exigências de grounding (não inventar valores, separar ao vivo de snapshot e de documento, declarar falta de cobertura) estão no prompt de sistema e são **checadas depois** pela verificação.

## 4. Fluxo de um turno

```
POST /conversations/{id}/agent
 └─ IntegratedAgentService.handle            (trace "integrated.turn", metadados: fase, sessão em hash, modelo)
     ├─ trava por sessão (a mesma da Fase 3)
     ├─ SQLite: grava a mensagem do usuário  (única escrita do lado do usuário)
     ├─ SessionHistoryAdapter.load           (resumo + memórias Mem0 + janela; só leitura)
     ├─ create_agent: LLM ⇄ ferramentas      (tool calling nativo; várias categorias se preciso)
     ├─ trajetória ← AIMessage.tool_calls + ToolMessage   (ordem, args, saída, ok/erro)
     ├─ VerificationService.verify           (afirmações → evidência → NLI → coerência → status)
     ├─ [se a política pedir] 1 correção     (reaproveita as observações; nova verificação)
     │    └─ falhou de novo → resposta cautelosa (contraditas removidas, aviso)
     ├─ SQLite: grava a resposta (+ análise e episódio no Mem0 se houve dado de mercado)
     └─ manutenção da Fase 3 (resumo incremental, extração semântica) em segundo plano
```

## 5. Histórico persistente (substituto do `RunnableWithMessageHistory`)

| Componente | Responsabilidade |
|---|---|
| `ConversationStore` (SQLite, Fase 3) | Persiste as mensagens. Fonte da verdade |
| `MemoryBackend` (Mem0, Fase 3) | Memórias episódicas e semânticas por sessão |
| `SessionHistoryAdapter` | **Só lê**: monta `[SystemMessage(resumo + memórias), Human/AI da janela]` sem a mensagem atual |
| `IntegratedAgentService` | Grava pergunta e resposta, uma vez cada, por turno |

Por construção não há duplicação: o adaptador nunca escreve e o agente não usa checkpointer. O teste `test_follow_up_uses_persisted_history_and_survives_restart` confere 2 turnos = 4 mensagens, mesmo depois de um "reinício" (novo serviço e novo store sobre o mesmo arquivo).

O agente integrado e o fluxo da Fase 3 (`POST /conversations/{id}/messages`) compartilham sessão, histórico, resumo e Mem0. As duas engines podem ser usadas na mesma conversa, e cada mensagem registra `engine` nos metadados.

O histórico entra no agente como mensagens, então os follow-ups funcionam ("E o volume de 24 horas dele?" → `get_market_data(BTC)`, evidência em [integration-run1.log](evidence/phase-5/integration-run1.log), teste `test_multi_turn_isolation_restart_and_mem0`).

## 6. Trajetória e observabilidade

`AgentReply.trajectory` lista os passos em ordem: `order`, `tool`, `category`, `args`, `ok`, `error_kind`, `error_message` e `output`. Por padrão, a resposta HTTP omite `output`; ele volta com `include_trace=true`.

A mesma trajetória alimenta:
- a verificação: as observações são a evidência;
- os avaliadores de trajetória do LangSmith.

No LangSmith, o trace `integrated.turn` contém:
- `integrated.agent_run`, com o grafo do `create_agent`, as chamadas ao LLM e as ferramentas;
- `verification.verify` (`verification.claims_nli` e `verification.logic`);
- `integrated.correction`, quando há correção.

Ver [langsmith-evaluation.md](langsmith-evaluation.md).

## 7. Status da resposta

| `status` | Quando |
|---|---|
| `complete` | Ferramentas sem erro e verificação `verified` |
| `partial` | Alguma ferramenta falhou ou a verificação não foi `verified` |
| `failed` | Todas as chamadas de dados de mercado falharam |
