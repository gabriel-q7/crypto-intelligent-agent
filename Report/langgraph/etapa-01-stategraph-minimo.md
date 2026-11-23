# Etapa 1 — StateGraph mínimo (agent, ToolNode, tools_condition)

| | |
|---|---|
| **Período** | 2026-11-23 a 2026-11-23 |
| **Status** | Concluída |
| **Ambiente** | Docker Compose, CPU (12 núcleos, sem GPU); Ollama `gemma4:e2b`, `OLLAMA_REASONING=false`, `num_ctx` 8192 |
| **Versões** | langgraph 1.2.20, langgraph-prebuilt 1.1.1, langchain-core 1.7.6, langchain-ollama 1.1.0 |

## 1. Objetivo

Grafo ReAct explícito com estado tipado, nó do agente, `ToolNode`, `tools_condition` e arestas condicionais,
validado com ferramenta real, antes de persistência e Reflexion.

## 2. Escopo

- **Dentro:** estado, nós `agent`/`tools`/`finalize`, roteamento, limite de iterações, tratamento de erro de
  ferramenta, classificação da arquitetura anterior, teste real de tool calling no grafo.
- **Fora (e por quê):** demais ferramentas, evidência e verificação (Etapa 2); checkpointer (Etapa 3); histórico
  (Etapa 4); API (Etapa 7). O enunciado pede o grafo básico funcionando antes disso.

## 3. Solução implementada

Pacote novo [app/graph/](../../app/graph/), sem alterar `app/integrated` nem `app/legacy`:

| Arquivo | Conteúdo |
|---|---|
| [state.py](../../app/graph/state.py) | `ReactState` (`messages` com `add_messages`, `user_query`, `llm_calls`, `final_response`) |
| [nodes.py](../../app/graph/nodes.py) | `make_agent_node` (bind_tools uma vez; latência e tokens por chamada), `finalize` |
| [routing.py](../../app/graph/routing.py) | `tools_condition` + mapa de destinos |
| [builder.py](../../app/graph/builder.py) | `build_react_graph`, `tool_error_observation` |
| [run.py](../../app/graph/run.py) | `run_react`, `graph_config` (limite de iterações, `thread_id`, metadados), `GraphRun` |

Fluxo: `START → agent ─(tool_calls)→ tools → agent ─(sem tool_calls)→ finalize → END`. Diagrama e classificação
da arquitetura anterior em [docs/langgraph-architecture.md](../../docs/langgraph-architecture.md).

Reaproveitado do projeto-base: `SYSTEM_PROMPT`, `trajectory` e `_text` (`app/integrated/agent.py`),
`structured_tools` (`app/integrated/tools.py`), fábricas de ferramentas reais.

## 4. Decisões técnicas

| Decisão | Alternativas consideradas | Motivo |
|---|---|---|
| Pacote `app/graph/` | `app/agents/langgraph/` com `app/agents/legacy/` (sugestão do enunciado) | Mover `app/integrated` e `app/legacy` seria refatoração sem relação; `app/agents/` já é a fábrica da Fase 2 |
| Prompt do sistema fora do estado | Guardar `SystemMessage` em `messages` | Não se repete no histórico nem no checkpoint; o nó o injeta a cada chamada |
| Estado só com campos usados | Todos os campos sugeridos de uma vez | Campos de evidência/verificação entram com os nós que os produzem |
| `handle_tool_errors=tool_error_observation` | Padrão do `ToolNode` | Ver problema 5.1 |
| `recursion_limit = 2 × max_iterations + 3` | Limite padrão (25) | Mesmo limite de iterações do projeto-base (`AGENT_MAX_ITERATIONS=6` → 15); ver problema 5.2 |
| `langgraph==1.2.20` fixado em `requirements.txt` | Depender só do transitivo do `langchain` | Agora é importado diretamente; mesma versão já instalada |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 `ToolNode` relança exceções de ferramenta

- **Sintoma:** teste offline com ferramenta que lança `RuntimeError` derrubou o grafo.
- **Causa:** no langgraph-prebuilt 1.1.1, o `handle_tool_errors` padrão (`_default_handle_tool_errors`) só
  converte erro de argumento inválido; o resto é relançado.
- **Correção:** `tool_error_observation` devolve a mesma observação JSON de erro das ferramentas
  (`{"ok": false, "error": {"kind": "tool_error", ...}}`); o `ToolMessage` sai com `status="error"` e o agente
  continua.
- **Evidência:** `test_tool_exception_becomes_error_observation_and_loop_continues`.

### 5.2 Limite de recursão uma unidade abaixo do necessário

- **Sintoma:** com `max_iterations=2`, um turno com exatamente 2 rodadas de ferramenta falhou com
  `GraphRecursionError: Recursion limit of 6 reached`.
- **Causa:** a conta inicial (2 × iterações + 2 = 6 passos) igualava o limite; o LangGraph exige passos < limite.
- **Correção:** margem +3. **Evidência:** `test_limit_allows_exactly_max_iterations` e `test_iterations_are_bounded`.

### 5.3 Falso negativo no teste real (parser de números)

- **Sintoma:** `test_single_tool_selection_execution_and_answer` falhou com "resposta não usa o preço observado".
- **Causa:** o modelo respondeu `$61,370.93` (formato en-US); o parser do teste tratava a vírgula como decimal
  (pt-BR). O modelo estava certo.
- **Correção:** parser aceita os dois formatos (último separador = decimal, salvo milhar com 3 dígitos).

## 6. Evidências e métricas

Testes offline: `docker compose run --rm --no-deps -v "$PWD":/app app pytest -q` → **412 passed**, 25 deselected
(401 do projeto-base + 11 novos em [tests/unit/test_graph_react.py](../../tests/unit/test_graph_react.py)).

Teste real (2026-11-23 23:43 UTC): `pytest -m "integration or network" tests/integration/test_graph_react_live.py`
→ **3 passed** em 40 s. Log: [etapa-01-react-live.log](../../docs/evidence/langgraph/etapa-01-react-live.log);
JSONL: [etapa-01-react-live.jsonl](../../docs/evidence/langgraph/etapa-01-react-live.jsonl).

| Pergunta | Ferramentas (args) | Chamadas ao modelo | Latência por chamada (s) | Total (s) | Tokens in/out |
|---|---|---|---|---|---|
| Qual é o preço atual do Bitcoin? | `get_market_data(BTC)` | ferramenta → resposta | 0,75 / 5,24 | 6,15 | 2525 / 80 |
| Compare o preço atual do Bitcoin e do Ethereum. | `get_market_data(BTC)`, `get_market_data(ethereum)` na **mesma** mensagem | ferramentas → resposta | 1,60 / 13,48 | 15,26 | 2875 / 217 |
| Calcule a variação percentual de 100 para 150. | `calculate_percent_change(100, 150)` | ferramenta → resposta | 1,32 / 1,55 | 2,88 | 2297 / 49 |
| Olá! O que você consegue fazer? | — | resposta | 15,16 | 15,16 | 1082 / 231 |

Ciclo observado nas mensagens: `HumanMessage → AIMessage(tool_calls) → ToolMessage(s) → AIMessage`. A primeira
execução (antes da correção do parser) teve as mesmas escolhas de ferramenta; a latência da primeira chamada foi
maior (16,6 s) por carga do modelo.

## 7. Limitações conhecidas

- Só duas ferramentas reais nesta etapa (`get_market_data`, `calculate_percent_change`).
- Sem evidência consolidada nem verificação no grafo; a resposta sai do `finalize` sem checagem.
- Uma execução por pergunta; latências não são média.
- `llm_calls` acumula por soma: com checkpointer (Etapa 3), precisa ser zerado ou separado por turno.

## 8. Aprendizados

- O `gemma4:e2b` emite várias chamadas paralelas na mesma `AIMessage` com tool calling nativo, e o `ToolNode`
  as executa antes de voltar ao agente.
- Comportamentos padrão do `ToolNode` mudaram entre versões; testar falha de ferramenta explicitamente.

## 9. Pendências e insumos para a próxima etapa

- Etapa 2: todas as 9 ferramentas (API, SQL, RAG com Query Rewriting, cálculo), nós `collect_evidence`,
  `verify_response`, `revise_response` reaproveitando `VerificationService` e a política de correção única,
  cenários API+SQL e API+RAG.
