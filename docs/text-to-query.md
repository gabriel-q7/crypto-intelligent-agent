# Text-to-Query — banco de snapshots de mercado (Fase 4)

Converte perguntas em linguagem natural em SQL **somente leitura**, validado, sobre um banco SQLite próprio com snapshots reais da CoinPaprika.

Código:
- `app/market_data/store.py`: schema e repositório;
- `app/market_data/ingestion.py`: coleta;
- `app/market_data/sql_guard.py`: validação e execução;
- `app/market_data/text_to_query.py`: serviço;
- `app/tools/market_db_tool.py`: ferramenta do agente.

## 1. Banco de dados

Arquivo próprio, `MARKET_DB_PATH` (padrão `data/market.sqlite3`), **separado** de `data/conversations.sqlite3` (Fase 3). É criado na primeira operação, e o schema é versionado por `PRAGMA user_version`.

```
assets                         market_snapshots                            latest_snapshots (VIEW)
- id PK                        - id PK                                     snapshot mais recente
- coinpaprika_id UNIQUE        - asset_id FK → assets(id) ON DELETE CASCADE  de cada ativo + symbol,
- symbol (índice)              - price_usd NOT NULL CHECK >= 0               name e coinpaprika_id
- name                         - market_cap_usd, volume_24h_usd
- rank                         - percent_change_1h/24h/7d/30d
                               - ath_price_usd, percent_from_ath
                               - circulating_supply, max_supply, rank
                               - provider, source_endpoint
                               - source_updated_at (horário da fonte)
                               - retrieved_at NOT NULL (horário local da consulta)
```

As colunas são exatamente as de `MarketTicker` (campos reais de `GET /tickers/{id}`). Nenhuma métrica foi inventada, e `NULL` significa "o provedor não informou". `percent_change_30d` chegava como `0` no plano gratuito até 2026-10-28, e o cliente converte esse 0 exato para `NULL`. Em 2026-11-05 a API passou a devolver valores reais (ex.: BTC -6,0%), gravados normalmente.

Índices e restrições:
- **Índice único parcial `(asset_id, source_updated_at) WHERE source_updated_at IS NOT NULL`:** o mesmo dado da fonte não é gravado duas vezes. A CoinPaprika atualiza o ticker a cada poucos minutos, então coletas repetidas dentro desse intervalo viram `duplicate`.
- **Índice `(asset_id, retrieved_at)`:** consultas temporais.
- Horários em texto ISO-8601 UTC (`2026-11-04T12:00:00Z`), ordenáveis e aceitos por `datetime()`/`julianday()` do SQLite.

## 2. Coleta

`POST /market-data/ingest` (ou `make market-ingest`) com `{"assets": [...]}`, que aceita ids, símbolos ou nomes. Corpo vazio usa `MARKET_DATA_ASSETS` (10 ativos).

1. `MarketDataIngestor` reusa o `CoinPaprikaClient` da Fase 2, com timeout, retries, rate limit, erros tipados e horários.
2. Cada ativo é independente. Falha (`not_found`, `rate_limited`, …) vira item `failed` com o tipo do erro, e **nada é gravado** para ele.
3. Sucesso grava o ativo (upsert) e o snapshot em transação (`BEGIN IMMEDIATE`). O resultado é `inserted` ou `duplicate`.
4. A resposta traz a cobertura do banco depois da coleta.

Não há agendador. Histórico se forma chamando a coleta ao longo do tempo. Nada é preenchido por interpolação.

## 3. Fluxo da consulta

```
pergunta
 ├─ cobertura do banco ── 0 snapshots ──────────────────────────────→ missing_data (sem LLM)
 ├─ find_period (regra, Fase 3) ── período ∉ {1, 7, 30} dias e
 │                                  maior que a janela coberta ────→ insufficient_coverage (sem LLM, sem SQL)
 ├─ LLM (modo JSON) → SqlGeneration {metrics, filters, sql, unsupported_reason}
 │        └─ sql nulo / unsupported_reason ─────────────────────────→ unsupported
 ├─ validate_sql (parser) ── recusado ──────────────────────────────→ invalid_query
 ├─ execute_query (somente leitura + authorizer + timeout)
 │        ├─ erro de schema/autorização ────────────────────────────→ invalid_query
 │        └─ timeout / falha do banco ──────────────────────────────→ execution_error
 └─ linhas?
          ├─ sim ───────────────────────────────────────────────────→ ok
          ├─ não, e a métrica filtrada está NULL em todos os ativos ─→ missing_data
          └─ não ───────────────────────────────────────────────────→ no_matching_records
```

Janelas de 1, 7 e 30 dias não exigem histórico no banco: o provedor já calcula `percent_change_24h`, `_7d` e `_30d` em cada snapshot (se não informar, o resultado é `missing_data`). Outros períodos (90 dias, 1 ano, …) exigem observações reais cobrindo o período.

`TextToQueryResult` guarda pergunta, interpretação do LLM, SQL executado (com `?`), parâmetros, colunas, linhas, cobertura e `data_origin` ("snapshots … armazenados no SQLite local (não é cotação ao vivo)").

## 4. Segurança

O SQL do modelo nunca é executado sem passar por **duas camadas independentes**.

**Camada 1 — parser (`sqlglot` 30.27, dialeto SQLite), antes da execução:**

| Regra | Como |
|---|---|
| Um único comando | `sqlglot.parse` → exatamente 1 statement |
| Somente leitura | Raiz `exp.Select` (CTE `WITH` permitida). Qualquer nó `Insert/Update/Delete/Drop/Create/Alter/Pragma/Attach/Detach/Transaction/Command/...` em qualquer ponto da árvore é recusado |
| Tabelas permitidas | `assets`, `market_snapshots`, `latest_snapshots` (+ CTEs da própria consulta). `sqlite_master`, `main.x` e outro banco são recusados |
| Colunas permitidas | Lista explícita por tabela (`ALLOWED_COLUMNS`) + aliases da consulta |
| Funções | Desconhecidas do parser (ex.: `load_extension`) só passam se estiverem em `ALLOWED_FUNCTIONS` |
| Limite | `LIMIT` acrescentado ou reduzido para `TEXT_TO_SQL_MAX_ROWS` (50). `LIMIT` não literal é recusado |
| Parametrização | Todo literal vira `?`, e os valores vão separados para `execute` |
| Tamanho | Até 4.000 caracteres |

**Camada 2 — SQLite, na execução:**

| Proteção | Como |
|---|---|
| Conexão somente leitura | `file:...?mode=ro`: o próprio SQLite recusa escrita |
| Authorizer | `set_authorizer`: só `SELECT`, `READ` de pares tabela/coluna permitidos (e tabelas-base lidas pela view), funções da lista e recursão de CTE. Todo o resto: `SQLITE_DENY` (ex.: `random()` passa no parser mas é negada aqui) |
| Tempo limite | `set_progress_handler` interrompe a consulta após `TEXT_TO_SQL_TIMEOUT` (2 s) |
| Linhas | `fetchmany(limit)` |

Mensagens de erro são genéricas: não expõem caminho de arquivo nem detalhes internos (testado).

## 5. Grounding

- A ferramenta declara a fonte `SQLite local (snapshots coletados da CoinPaprika)` com o horário da coleta mais recente. Já `get_market_data` declara `CoinPaprika` + `/tickers/{id}`. Os dois aparecem separados em `data_sources`.
- As linhas do banco viram achados "Banco de snapshots (coleta mais recente …)", montados pelo código, não pelo LLM.
- Os números das linhas entram nas observações: valor citado pelo LLM sem linha correspondente é removido por `verify_figures` (teste `test_agent_selects_text_to_query_and_grounds_answer_in_rows`).
- `missing_data`, `insufficient_coverage`, `unsupported` e `invalid_query` chegam ao agente como `ok=false` com a instrução de não inventar valores.

## 6. Exemplos (execução real)

Os exemplos reais com perguntas, SQL gerado pelo `gemma4:e2b`, validação e linhas estão em [phase-4-evidence.md](phase-4-evidence.md), seção 2. Exemplos de recusa do validador (testes `test_unsafe_or_invalid_sql_is_rejected_before_execution`):

| SQL proposto | Resultado |
|---|---|
| `DELETE FROM assets` | `apenas SELECT é permitido (recebido: DELETE)` |
| `SELECT 1 FROM assets; DROP TABLE assets` | `apenas um comando SQL por consulta` |
| `PRAGMA table_info(assets)` | `apenas SELECT é permitido (recebido: PRAGMA)` |
| `SELECT * FROM sqlite_master` | `tabela não permitida: sqlite_master` |
| `SELECT load_extension('x') FROM assets` | `função não permitida: load_extension` |
| `SELECT secret FROM assets` | `coluna não permitida ou inexistente: secret` |
| `SELECT random() FROM assets` | Passa no parser e é negada pelo authorizer (`invalid_query`) |
| `WITH RECURSIVE` com 10⁸ iterações | Interrompida pelo tempo limite (`execution_error`/timeout) |

## 7. Limitações

- **Histórico depende de coletas reais.** Com uma coleta só, a janela coberta é de 0 dias. Perguntas de 90 dias ou mais dão `insufficient_coverage` até existirem snapshots espaçados o bastante. Não há interpolação nem preenchimento.
- **Cobertura é global, não por ativo.** Um ativo coletado só hoje dentro de um banco com 30 dias de histórico passa no teste de cobertura. A consulta devolve só o que existe, mas a mensagem de cobertura não detalha por ativo.
- **O período vem só da regra `find_period`.** Expressões não cobertas ("desde o mês passado") não acionam o teste de cobertura. O SQL pode então devolver poucas linhas, sempre reais, mas sem o aviso de cobertura insuficiente.
- **Qualidade do SQL depende do modelo.** O `gemma4:e2b` acerta consultas simples (filtro, ranking). Consultas temporais entre snapshots são mais frágeis. A validação garante segurança, não correção semântica.
- **`percent_change_30d` depende do provedor**: era 0 (→ `NULL`) no plano gratuito até 2026-10-28 e passou a vir preenchido em 2026-11-05. Quando é `NULL`, filtros nele dão `missing_data`.
- **Sem agendador**: a coleta é manual (endpoint ou `make market-ingest`).
