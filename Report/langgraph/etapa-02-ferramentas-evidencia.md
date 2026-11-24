# Etapa 2 — Integração completa das ferramentas, evidência e verificação no grafo

| | |
|---|---|
| **Período** | 2026-11-23 a 2026-11-24 |
| **Status** | Concluída |
| **Ambiente** | Docker Compose, CPU (12 núcleos, sem GPU); Ollama `gemma4:e2b` (chat e verificação), `nomic-embed-text`; snapshots locais: 29 registros de 10 ativos (2026-11-05) |
| **Versões** | langgraph 1.2.20, langgraph-prebuilt 1.1.1, langchain-core 1.7.6, langchain-ollama 1.1.0 |

## 1. Objetivo

Levar ao grafo as 9 ferramentas do projeto-base (API, SQL, RAG com Query Rewriting, cálculo), consolidar a
evidência com proveniência, validar tratamento de erro e cenários combinados (API + SQL, API + RAG), e trazer a
verificação e a correção única do agente integrado como nós.

## 2. Escopo

- **Dentro:** nós `prefetch`, `collect_evidence`, `verify_response`, `revise_response`, `finalize`; latência
  por ferramenta; reuso das regras de correção da Fase 5; testes offline e reais.
- **Fora (e por quê):** checkpointer (Etapa 3), gestão de histórico (Etapa 4), API (Etapa 7). Seleção semântica de
  ferramentas: não justificada (9 ferramentas; ver seção 6.3).

## 3. Solução implementada

Grafo e tabelas de estado, nós e rotas em [docs/langgraph-architecture.md](../../docs/langgraph-architecture.md).
Resumo do fluxo:

`START → prefetch → agent ⇄ tools → collect_evidence → verify_response → (revise_response → agent …) → finalize → END`

| Arquivo | Mudança |
|---|---|
| [app/graph/state.py](../../app/graph/state.py) | `turn_id`, `tool_results`, `evidence`, `draft_answer`, `verification_result`, `correction_attempts`, `original_*`, `execution_metadata` |
| [app/graph/nodes.py](../../app/graph/nodes.py) | Nós novos, `timed_tool_call` (latência via `wrap_tool_call`), leitura do turno por `turn_id` |
| [app/graph/routing.py](../../app/graph/routing.py) | `route_after_verification` (correção única) |
| [app/graph/builder.py](../../app/graph/builder.py) | `verifier`, `max_corrections`, `prefetch` opcionais |
| [app/graph/run.py](../../app/graph/run.py) | `turn_input` (zera campos do turno, `Overwrite` em `llm_calls`), `recursion_limit` com a passada de correção, `GraphRun` com verificação e status |
| [app/integrated/service.py](../../app/integrated/service.py) | Extração de `agent_failed`, `should_correct`, `correction_feedback`, `settle_correction`, `deliverable_answer`, `reply_status` (sem mudança de comportamento) |

## 4. Decisões técnicas

| Decisão | Alternativas consideradas | Motivo |
|---|---|---|
| Extrair as regras de política do `IntegratedAgentService` para funções de módulo | Copiar a lógica para os nós | Evita duplicar regra de negócio; os dois caminhos usam as mesmas funções. Suíte da Fase 5 continua verde |
| Verificação e correção como nós (`verify_response` → `revise_response` → `agent`) | Manter a correção como segundo `invoke` (como na Fase 5) | A correção vira transição explícita do grafo e reaproveita as mensagens já no estado (sem repetir chamadas) |
| Turno identificado por `turn_id` (id da `HumanMessage`) | Índice da mensagem; última `HumanMessage` | O feedback da correção também é `HumanMessage`; com histórico/checkpoint o índice muda. O id é estável |
| `llm_calls` zerada por turno com `Overwrite` | `turn_id` em cada entrada e filtro na leitura | Não acumula no checkpoint (Etapa 3) |
| Estado só com tipos JSON | Modelos Pydantic no estado | Serialização previsível no checkpoint |
| Manter a pré-consulta fixa das comparações entre redes como nó opcional | Remover (deixar tudo com o modelo) | Regra documentada do projeto-base (Fase 5, seção 5.11); único ponto de ordem fixa |
| Latência de ferramenta via `wrap_tool_call` do `ToolNode` | Envolver cada ferramenta | Um ponto só, sem tocar nas ferramentas |
| Sem seleção semântica de ferramentas | Pré-seleção por embeddings | 9 ferramentas; a decisão do modelo levou 6,4 a 8,7 s com o modelo carregado e acertou todas as ferramentas (seção 6.3) |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Teste "SQL falha → API" disparou correção

- **Sintoma:** teste offline com a pergunta "Preço do BTC nos últimos 90 dias no banco?" esgotou o roteiro do
  modelo falso (`StopIteration`).
- **Causa:** a regra `skipped_history` do projeto-base (caso real de 2026-11-13) viu "últimos 90 dias" sem
  `get_price_history` e pediu a correção. O grafo estava certo; o teste não previa a regra.
- **Correção:** pergunta do teste trocada para isolar SQL → API; novo teste
  `test_history_question_without_price_history_uses_baseline_rule` cobre a regra no grafo.

### 5.2 Correção e reescrita não apareceram nos 6 cenários reais

- **Sintoma:** os 6 casos reais foram verificados na primeira passada; nenhum usou a correção, e o Retrieval
  Agent decidiu "pergunta original adequada" em todos os casos RAG.
- **Ação:** sonda com vocabulário coloquial ([etapa-02-rewrite-probe.jsonl](../../docs/evidence/langgraph/etapa-02-rewrite-probe.jsonl)):
  - "Moeda que tenta valer sempre um dólar pode quebrar?" → distância 0,415 > 0,33 → consulta reescrita
    ("riscos de stablecoins falha estabilidade moeda dólar"), 7 afirmações sustentadas.
  - "Por que tanta gente fala da grana parada dentro dos protocolos de empréstimo cripto?" → verificação
    `partially_verified` (1 sustentada, 1 neutra) → `revise_response` → resposta corrigida. Caminho real da
    correção no grafo.
- **Achado:** na primeira sonda, o trecho recuperado (distância 0,220, aceito) não tratava de TVL e a resposta
  disse que não encontrou o tema. Limitação de recuperação herdada do projeto-base, não do grafo.

## 6. Evidências e métricas

### 6.1 Testes

- Offline: `docker compose run --rm --no-deps -v "$PWD":/app app pytest -q` → **427 passed**, 28 deselected
  (412 da Etapa 1 + 15 em [tests/unit/test_graph_tools_evidence.py](../../tests/unit/test_graph_tools_evidence.py)).
  Inclui os testes da Fase 5, inalterados, após a extração das regras.
- Real (2026-11-24 00:03–00:10 UTC): `pytest -m "integration or network" tests/integration/test_graph_tools_live.py`
  → **6 passed** em 413,65 s. Log: [etapa-02-tools-live.log](../../docs/evidence/langgraph/etapa-02-tools-live.log);
  JSONL: [etapa-02-tools-live.jsonl](../../docs/evidence/langgraph/etapa-02-tools-live.jsonl).

### 6.2 Cenários reais

| Caso | Ferramentas (latência, s) | Verificação | Status | Total (s) |
|---|---|---|---|---|
| API + SQL: preço atual do BTC × último snapshot local | `get_market_data` (0,60), `query_market_database` (10,36) | verified (2/2) | complete | 49,1 |
| API + RAG: o que é PoS + preço do ETH | `search_knowledge_base` (0,79), `get_market_data` (0,42) | verified (4/4) | complete | 52,8 |
| SQL: 3 maiores market caps no banco local | `query_market_database` (12,20) | verified (3/3) | complete | 76,5 |
| RAG: staking no Ethereum | `search_knowledge_base` (0,07) | verified (5/5) | complete | 54,0 |
| Erro: preço da "XQZWV" | `get_market_data` ✗ `not_found` (0,46) | verified (0 afirmações) | failed | 11,3 |
| Comparação Solana × Ethereum (pré-consulta) | 3× `search_knowledge_base`, 2× `get_asset_profile`, 2× `get_market_data` (pré-consulta) | verified (8/8) | complete | 167,4 |

Exemplo API + SQL (resposta real): "O preço atual do Bitcoin (BTC) é de **$61,155.93 USD**, conforme dados da
CoinPaprika atualizados em 24/11/2026 às 00:04:09 […]. O último snapshot do Bitcoin armazenado no banco local tem
um preço de **$75,897.16 USD**, coletado em 05/11/2026 às 23:54:09Z." Fonte e horário de cada dado preservados;
ao vivo e armazenado diferenciados.

No caso de erro, a resposta foi "Não foi possível encontrar dados de mercado para a criptomoeda XQZWV." e a
limitação foi registrada (`get_market_data: not_found`).

### 6.3 Seleção de ferramentas e latência

| Caso | Decisão do modelo (s) | Ferramentas pedidas na 1ª chamada | Necessárias? | Resposta (s) | Ferramentas (s) | Restante: verificação (s) |
|---|---|---|---|---|---|---|
| API + SQL | 21,56 (inclui carga do modelo) | `get_market_data`, `query_market_database` | sim, ambas | 12,28 | 10,96 | 4,3 |
| API + RAG | 7,41 | `search_knowledge_base`, `get_market_data` | sim, ambas | 16,85 | 1,21 | 27,4 |
| SQL | 8,73 | `query_market_database` | sim | 20,57 | 12,20 | 35,0 |
| RAG | 6,39 | `search_knowledge_base` | sim | 13,09 | 0,07 | 34,4 |
| Erro | 6,60 | `get_market_data` | sim | 2,83 | 0,46 | 1,5 |

- Acerto de seleção nos 5 casos com escolha do modelo: 5/5 com todas as ferramentas necessárias, 0 chamadas
  desnecessárias ou repetidas. Nos casos combinados, o modelo pediu as duas ferramentas **na mesma mensagem**
  (uma única rodada de ferramentas).
- O custo dominante é o modelo (decisão + resposta) e a verificação (NLI e coerência, também no Ollama). As
  ferramentas só pesam no Text-to-Query (10–12 s, porque gera SQL com o LLM).
- Uma execução por caso: valores indicativos, não médias. Medição sistemática na Etapa 6.

## 7. Limitações conhecidas

- A pré-consulta roda fora do `ToolNode`: suas ferramentas ficam sem latência medida (`None`).
- Verificação sem tempo próprio no estado além do `elapsed_s` do `VerificationResult`; a tabela 6.3 deduz o
  restante por diferença.
- `GraphRecursionError` ainda não é tratado (o serviço da Etapa 7 deve convertê-lo em resposta de limitação).
- Uma execução por cenário; correção real observada em 1 sonda.

## 8. Aprendizados

- O caminho de correção, antes um segundo `invoke` escondido no serviço, ficou visível como `verify_response →
  revise_response → agent`, reaproveitando as mensagens já no estado.
- Extrair a política para funções puras foi o que permitiu reuso real entre o serviço da Fase 5 e o grafo.

## 9. Pendências e insumos para a próxima etapa

- Etapa 3: `SqliteSaver` (`langgraph-checkpoint-sqlite==3.1.3`), `thread_id = session_id`, continuação entre
  requisições e após reinício, isolamento entre threads, apagar a thread junto com a sessão.
