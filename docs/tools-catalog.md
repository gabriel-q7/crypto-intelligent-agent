# Catálogo de ferramentas — Fase 4

Todas as ferramentas que o agente principal (ReAct, `POST /agent`) pode chamar, mais os componentes internos que seguem a mesma lógica de contrato. O agente ReAct clássico (`ZERO_SHOT_REACT_DESCRIPTION`) só aceita ferramentas de **uma entrada em texto**. Por isso:
- toda ferramenta recebe uma string;
- a string é normalizada e validada (Pydantic) antes de qualquer efeito;
- toda ferramenta devolve **JSON com `ok`**.

Contrato comum de saída (`app/tools/market_tools.py`):

```json
{"ok": true,  "tool": "<nome>", "data": {...}, "source": {"provider": "...", "endpoint": "...", "source_updated_at": "...", "retrieved_at": "..."}}
{"ok": false, "tool": "<nome>", "error": {"kind": "<tipo>", "message": "..."}, "instruction": "Não invente valores: ..."}
```

Nenhuma ferramenta lança exceção para o agente. Falha vira `ok=false` com `error.kind`, e o `CryptoReport` registra a falha em `tool_calls` e `uncertainties`.

## Classificação

| Categoria | Critério | Ferramentas e componentes |
|---|---|---|
| **Hardcoded** | Lógica determinística, sem LLM | `calculate_percent_change`; validação de entrada (`AssetInput`); detecção de período (`find_period`); validador SQL (`validate_sql`); verificação de números (`verify_figures`); formatação (`format_usd`, `format_pct`) |
| **Semântica** | O LLM interpreta a intenção ou escolhe a operação | Seleção de ferramentas pelo agente ReAct; `QueryRewriter`; gerador SQL do Text-to-Query; classificador de intenção (Fase 3) |
| **API** | Chamada a serviço externo | `search_crypto_asset`, `get_market_data`, `get_market_details`, `get_global_market` (CoinPaprika) |
| **Text-to-Query** | Pergunta → SQL validado → banco | `query_market_database` |
| **Retrieval** | Busca semântica na base local (combina semântica + hardcoded) | `search_knowledge_base` (Retrieval Agent) |

Regra geral: o LLM **decide** (qual ferramenta, qual SQL, qual consulta reescrita), mas **não executa** nada diretamente. Cada decisão passa por validação explícita antes de ter efeito.

## Ferramentas do agente principal

Ordem de registro em `app/agents/factory.py::build_agent_tools`.

### 1. `search_crypto_asset` — API

| | |
|---|---|
| Descrição | Identifica criptoativos na CoinPaprika |
| Entrada | Nome ou símbolo (`"solana"`, `"BTC"`); prefixos como `query:` e aspas são removidos (`AssetInput`) |
| Saída | `data`: lista de `{id, name, symbol, rank, is_active}` |
| Lógica | `GET /search/` → até 5 candidatos |
| Erros | `invalid_argument`, `not_found`, `rate_limited`, `timeout`, `network`, `http_error`, `malformed` |

### 2. `get_market_data` — API

| | |
|---|---|
| Descrição | Dados de mercado **atuais** de um ativo |
| Entrada | Símbolo, nome ou id CoinPaprika |
| Saída | `MarketTicker`: preço, volume 24h, market cap, variações 1h/24h/7d/30d, ATH, oferta; `source` com `source_updated_at` (horário da fonte) e `retrieved_at` (horário da consulta) |
| Lógica | Resolve o id (id exato > símbolo > nome, desempate por rank) → `GET /tickers/{id}`. `percent_change_30d = 0` exato (preenchimento do plano gratuito até 2026-10-28) vira `null`; valores reais passam |
| Erros | Os mesmos da CoinPaprika (timeout e retries com backoff; 429 respeita `Retry-After` até 10 s) |

### 3. `get_market_details` — API

| | |
|---|---|
| Descrição | OHLCV do dia e perfil do ativo (tipo, consenso, tags) |
| Entrada | Símbolo, nome ou id |
| Saída | `{profile, ohlcv_today_usd}` |
| Lógica | `GET /coins/{id}` + `GET /coins/{id}/ohlcv/today`. O OHLCV histórico é bloqueado no plano gratuito (402) e não é usado |

### 3a. `get_asset_profile` — API (só no agente integrado)

| | |
|---|---|
| Descrição | Perfil técnico de uma rede/ativo, sem preços |
| Entrada | Símbolo, nome ou id |
| Saída | `CoinProfile`: tipo, consenso (`proof_type`), status de desenvolvimento, código aberto, data de início, descrição (até 800 caracteres, sem o crédito "Source: CoinPaprika.com"), whitepaper, site, repositório, tags |
| Lógica | `GET /coins/{id}`. `hash_algorithm` e `org_structure` ficam de fora: o ETH ainda vem com `Ethash` (anterior ao The Merge) e a estrutura é opinião da fonte |
| Uso | Chamada pelo código em toda comparação entre redes (`IntegratedAgentService._prefetch`); cobre redes que a base de conhecimento não documenta |

### 3b. `get_price_history` — API (só no agente integrado)

| | |
|---|---|
| Descrição | Preço em USD de UM ativo em cada dia (00:00 UTC) dos últimos N dias |
| Entrada | `asset` e `days` (1 a 364, padrão 7); na ferramenta de texto, `"ativo, dias"` (ex.: `"SOL, 7"`) |
| Saída | `{coin_id, symbol, days, interval, points: [{date, price_usd}]}` |
| Lógica | `GET /tickers/{id}/historical?start=...&interval=1d` (acima de 31 dias, `7d`). Liberado no plano gratuito para o último ano; antes disso, 402 → `plan_restricted`. Volume e market cap ficam de fora da saída (o modelo de 2B embaralhava os dígitos) |
| Uso | Preço em dias passados ("última semana", "últimos N dias", "ontem") e quando `query_market_database` não tem registros do período. Se o agente não a chamar nessas perguntas, `skipped_history` aciona a correção única |

### 4. `get_global_market` — API

| | |
|---|---|
| Descrição | Market cap total, volume 24h e dominância do BTC |
| Entrada | Ignorada (aceita qualquer texto) |
| Lógica | `GET /global` |

### 5. `search_knowledge_base` — Retrieval (Fase 4: Retrieval Agent)

| | |
|---|---|
| Descrição | Conceitos na base local (coleção Chroma `crypto_knowledge`); reescreve a consulta quando o vocabulário não casa |
| Entrada | Pergunta ou termo (até 300 caracteres) |
| Saída | `data`: lista de trechos `{source, title, chunk_id, score, content}`. `retrieval`: `{original_query, rewritten_query, rewrite_applied, decision}` |
| Lógica | `RetrievalAgent.retrieve` (detalhes em [retrieval-agent.md](retrieval-agent.md)): busca original → reescrita pelo LLM se a busca for fraca → filtro de relevância (distância + palavra âncora) |
| Erros | `invalid_argument`, `index_unavailable` (coleção ausente), `no_relevant_documents` (a base não cobre o assunto; o agente deve dizer isso) |

Mesmo nome da Fase 2, de propósito: o prompt PTCF, as regras e a ancoragem (`findings_from`) já o conhecem. O formato de `data` é compatível (só ganhou `score` e `chunk_id`).

### 6. `calculate_percent_change` — Hardcoded

| | |
|---|---|
| Descrição | Variação percentual entre dois valores já obtidos de outras ferramentas |
| Entrada | Dois números em qualquer formato aceito por `parse_number` (`"100, 112.5"`, `"de 81.545,05 para 82.000"`) |
| Saída | `PercentChangeOutput {old, new, absolute_change, percent_change}` |
| Lógica | `(new - old) / |old| × 100`, sem LLM. O resultado entra nas observações, então o número calculado passa em `verify_figures` |
| Erros | `invalid_argument` (diferente de dois números; valor inicial zero) |

### 7. `query_market_database` — Text-to-Query

| | |
|---|---|
| Descrição | Pergunta em linguagem natural sobre o **banco local de snapshots** (SQLite), para filtros e rankings entre ativos |
| Entrada | A pergunta completa (até 500 caracteres) |
| Saída `ok=true` | `data {status: ok\|no_matching_records, message, rows, row_count, sql, data_origin, coverage}`; `source.provider = "SQLite local (snapshots coletados da CoinPaprika)"` e `retrieved_at` = coleta mais recente |
| Saída `ok=false` | `error.kind ∈ {missing_data, insufficient_coverage, invalid_query, unsupported, execution_error}` + `coverage` |
| Lógica | `TextToQueryService.run` (detalhes em [text-to-query.md](text-to-query.md)) |

A fonte declarada é diferente da CoinPaprika ao vivo. Nos achados, as linhas aparecem como "Banco de snapshots (coleta mais recente …)". O agente não apresenta snapshot como cotação atual.

## Componentes internos

| Componente | Categoria | Entrada → saída | Lógica central |
|---|---|---|---|
| `AssetInput` (`market_tools.py`) | Hardcoded | texto livre → nome/símbolo limpo | Remove prefixos (`asset=`), aspas e linhas extras; 1–60 caracteres |
| `find_period` (`intent/classifier.py`) | Hardcoded | texto → `(rótulo, dias)` | Regras pt/en ("últimos 30 dias", "7d", "24h") |
| `validate_sql` (`market_data/sql_guard.py`) | Hardcoded | SQL → `ValidatedQuery` ou `UnsafeQueryError` | Parser sqlglot + listas permitidas (tabelas, colunas, funções) + `LIMIT` + literais parametrizados |
| `verify_figures` (`services/grounding.py`) | Hardcoded | resposta + observações → resposta filtrada | Remove frases com valores que não estão nas observações (tolerância de 1%) |
| `MarketDataIngestor` (`market_data/ingestion.py`) | API (reuso) | ids/símbolos → `IngestionReport` | `CoinPaprikaClient.get_ticker` → `MarketDataStore.save_ticker`; falha por ativo, sem valor substituto |
| `QueryRewriter` (`retrieval/rewriter.py`) | Semântica | pergunta → `QueryRewrite {main_concept, intent, rewritten_query, added_terms}` | LLM em modo JSON, validado por Pydantic |
| Gerador SQL (`text_to_query.py`) | Semântica | pergunta + cobertura → `SqlGeneration {metrics, filters, sql, unsupported_reason}` | LLM em modo JSON, com o schema descrito no prompt |
| Seleção de ferramenta (`legacy/react_agent.py` + `prompts/ptcf.py`) | Semântica | pergunta → `Action`/`Action Input` | ReAct com PTCF, regra 6 (mapa pergunta → ferramenta) e 5 exemplos few-shot |

## Seleção de ferramentas pelo agente

Regra 6 do PTCF (`app/prompts/ptcf.py`):

| Pergunta | Ferramenta |
|---|---|
| Preço ou métricas atuais de um ativo | `get_market_data` (CoinPaprika ao vivo) |
| Filtro ou ranking no banco ("quais ativos…", "nossa base") | `query_market_database` |
| Conceito | `search_knowledge_base` (com a pergunta conceitual completa, sem resumir) |
| Mista | Combina só as necessárias |

O exemplo few-shot "banco de snapshots" mostra o uso de `query_market_database`, com valores fictícios. As regras 7 e 8 exigem que afirmações conceituais venham dos trechos recuperados e que respostas do banco digam que são snapshots, com o horário da coleta.

Guarda determinística (`app/agents/runner.py::assemble_report`): se a pergunta pede explicação conceitual ("explique", "diferença", "o que é", "como funciona"...) e nenhuma chamada de `search_knowledge_base` trouxe trechos, o relatório recebe a incerteza "a parte conceitual … não foi verificada na base de conhecimento local", e o status `complete` cai para `partial`. Motivo: na execução real, o `gemma4:e2b` respondeu a parte conceitual de uma pergunta mista sem buscar documentos (`docs/phase-4-evidence.md`, seção 4.2).
