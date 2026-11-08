# Crypto Intelligence Agent

Aplicação HTTP (FastAPI) com LangChain e Ollama para análise de criptoativos, com interface Streamlit.

- **Fase 1 — análise estruturada:** interpreta a pergunta e devolve um plano de análise + resposta mínima em JSON validado, sem dados externos.
- **Fase 2 — agente com ferramentas:** agente ReAct clássico e chains roteadas que consultam **dados reais da CoinPaprika**, buscam conceitos numa **base de conhecimento local (Chroma)**, mantêm **memória de conversa limitada** e devolvem um `CryptoReport` validado, com fontes, horários e incertezas.
- **Fase 3 — conversa com memória:** sessões persistentes (SQLite) com janela, resumo incremental e memória episódica/semântica (Mem0 sobre Chroma); interpreta continuações, correções e referências a resultados anteriores.
- **Fase 4 — ferramentas externas e RAG existente:** banco SQLite de **snapshots reais da CoinPaprika** consultado por **Text-to-Query** (SQL validado, somente leitura), **Retrieval Agent** com **Query Rewriting** sobre a coleção Chroma existente e ferramenta de cálculo determinístico, todos integrados ao agente ReAct.
- **Fase 5 — agente integrado e verificado:** um agente com tool calling nativo (`create_agent`, LangChain 1.x) que combina API, SQL, RAG e cálculo, usa o histórico persistente da Fase 3 (SQLite + Mem0), **verifica cada resposta** (afirmações × evidência real com NLI local + coerência lógica) e faz no máximo **uma correção**. Rastreamento e avaliação com **LangSmith** e interface **Streamlit**.

Regra central: **nenhum valor de mercado é inventado.** Achados, fontes e horários vêm das respostas reais das ferramentas; valores da resposta sem correspondência nos dados são removidos; falhas viram status explícito (`failed`, `partial`, `unavailable`).

## Estrutura

```
app/
  main.py            aplicação FastAPI (uvicorn app.main:app)
  api/               rotas HTTP, modelos de requisição e contêiner de dependências
  config.py          configuração por variáveis de ambiente (.env)
  schemas/           Fase 1 (CryptoQuestion, CryptoAnalysisRequest) e Fase 2 (CryptoReport)
  prompts/           prompts da Fase 1 e PTCF + few-shot da Fase 2
  chains/            Fase 1 (LCEL e @chain) e Fase 2 (sequencial, paralela, conhecimento, roteamento)
  agents/            execução do agente ReAct e fábricas
  legacy/            APIs legadas isoladas: initialize_agent/ZERO_SHOT_REACT_DESCRIPTION e RouterChain
  tools/             cliente e ferramentas CoinPaprika; busca de conhecimento; cálculo; Text-to-Query (Fase 4)
  knowledge/         ingestão do corpus e coleção Chroma
  market_data/       Fase 4: banco de snapshots (SQLite), coleta, validação SQL e Text-to-Query
  retrieval/         Fase 4: Retrieval Agent e Query Rewriting
  persistence/ intent/ conversation/   Fase 3: conversas, intenção e contexto
  integrated/        Fase 5: agente integrado (create_agent), ferramentas estruturadas, histórico, turno
  verification/      Fase 5: afirmações, evidência, NLI, coerência lógica, política de correção
  evaluation/        Fase 5: alvo, avaliadores (resposta e trajetória) e runner LangSmith
ui/                  Fase 5: interface Streamlit (só consome a API)
evaluation/          dataset de avaliação versionado (phase5_dataset.json)
  memory/            memória de conversa limitada
  observability/     callbacks de rastreamento e logging
  services/          LLM, validação, grounding, verificação da CoinPaprika, benchmark
knowledge/corpus/    corpus local (conceitos, sem preços)
benchmarks/          perguntas e resultados do benchmark de modelos
tests/unit/          offline (LLM falso, API simulada)
tests/integration/   Ollama (marcador integration) e internet (marcador network)
docs/                arquitetura, decisões, prompts, confiabilidade, evidências
Report/              diário técnico por fase
```

## Executar

```bash
cp .env.example .env
docker compose up -d --build app      # sobe Ollama, baixa os modelos (1ª vez) e inicia a API
curl -X POST localhost:8000/knowledge/ingest   # cria a coleção Chroma (1ª vez; fica em ./data/chroma)
curl -X POST localhost:8000/market-data/ingest -H 'Content-Type: application/json' -d '{}'   # snapshots de mercado (Fase 4)
```

API em `http://localhost:8000`; documentação interativa (Swagger) em **`http://localhost:8000/docs`**. Interface: **`http://localhost:8501`** (serviço `ui`, sobe com `make up` ou `docker compose up -d --build app ui`).

Streamlit fora do Docker: `pip install -r ui/requirements.txt && BACKEND_URL=http://localhost:8000 streamlit run ui/app.py --client.toolbarMode=minimal` (a flag esconde o botão Deploy; no container ela já vem em `ui/.streamlit/config.toml` e no `CMD`).

O `ollama-pull` baixa o modelo de chat (`OLLAMA_MODEL`) e o de embeddings (`EMBEDDING_MODEL`) para o volume `ollama_models`; o download acontece uma vez. A aplicação acessa o Ollama pelo nome do serviço (`http://ollama:11434`).

### Atalhos (Makefile)

```bash
make help              # lista todos os alvos
make up                # .env (se faltar) + Ollama + modelos + API + interface (:8501)
make ingest            # cria a coleção de conhecimento (1ª vez)
make market-ingest     # coleta snapshots reais para o banco SQLite (Fase 4; repita para formar histórico)
make market-coverage   # o que o banco de snapshots cobre
make health            # saúde da API
make test              # testes offline   (make test ARGS="-k intent")
make test-integration  # Ollama + Mem0 + CoinPaprika reais
make benchmark MODELS="gemma4:e2b qwen2.5:7b" REASONING=off
make eval PREFIX=fase5   # experimento no LangSmith (exige LANGSMITH_API_KEY) | make eval-local
make logs | make restart | make down
make clean-data CONFIRM=yes   # apaga conversas, memórias, snapshots e coleção em ./data
```

## Endpoints

| Método e rota | Fase | Descrição |
|---|---|---|
| `GET /health` | — | Ollama acessível, modelos baixados, coleção Chroma, sessões ativas |
| `GET /health/coinpaprika` | 2 | Verificação **real** da integração (ticker, OHLCV, perfil, global e os erros esperados) |
| `POST /analyze` | 1 | Plano de análise + resposta mínima (`composition`: `lcel` ou `imperative`) |
| `POST /analyze/batch` | 1 | Várias perguntas; falhas isoladas por item |
| `POST /analyze/stream` | 1 | NDJSON com objetos parciais e o objeto final validado |
| `POST /agent` | 2 | Agente ReAct com ferramentas; `session_id` mantém memória; `include_trace` devolve o rastro |
| `DELETE /agent/sessions/{id}` | 2 | Apaga a memória de uma sessão |
| `POST /conversations` | 3 | Cria uma conversa persistente (devolve `session_id`) |
| `GET /conversations` | 5 | Lista as conversas (mais recentes primeiro, com o título gerado pelo modelo após o 1º turno); usado pela barra lateral da interface |
| `GET /conversations/{id}` | 3 | Consulta/retoma a conversa (também após reinício da API) |
| `POST /conversations/{id}/messages` | 3 | Envia uma mensagem: intenção, contexto, execução e resposta com evidências |
| `GET /conversations/{id}/messages` | 3 | Histórico ordenado (`after_seq`, `limit`) |
| `GET /conversations/{id}/summary` | 3 | Resumo incremental da sessão e fatos preservados |
| `DELETE /conversations/{id}` | 3 | Apaga a conversa (SQLite) e suas memórias (Chroma) |
| `POST /route` | 2 | Roteamento: mercado (sequencial), comparação (paralela), conhecimento ou DeFi (indisponível); `router`: `modern` ou `legacy` |
| `GET /knowledge/search?q=` | 2 | Busca semântica na base local |
| `POST /knowledge/ingest` | 2 | Recria a coleção Chroma a partir de `knowledge/corpus` |
| `POST /market-data/ingest` | 4 | Coleta snapshots reais da CoinPaprika para o SQLite (`{"assets": [...]}` ou lista padrão) |
| `GET /market-data/coverage` | 4 | Ativos, quantidade de snapshots e janela de tempo coberta |
| `GET /market-data/snapshots` | 4 | Snapshots armazenados (`symbol`, `limit`) |
| `POST /market-data/query` | 4 | Text-to-Query: pergunta → SQL validado → linhas reais, com SQL, parâmetros e status |
| `POST /conversations/{id}/agent` | 5 | Agente integrado: ferramentas (API/SQL/RAG/cálculo), histórico persistente, verificação e até 1 correção; `include_trace` devolve as saídas das ferramentas |
| `POST /knowledge/retrieve` | 4 | Retrieval Agent: busca com Query Rewriting (`mode`: `auto`, `always`, `never`) e os dois rankings |

Exemplos:

```bash
# Agente ReAct com dados reais da CoinPaprika
curl -s -X POST localhost:8000/agent -H 'Content-Type: application/json' \
  -d '{"question": "Qual o preço atual do Bitcoin?", "session_id": "minha-sessao", "include_trace": true}'

# Comparação em paralelo (rota escolhida automaticamente)
curl -s -X POST localhost:8000/route -H 'Content-Type: application/json' \
  -d '{"question": "Compare BTC, ETH e SOL nas últimas 24 horas"}'

# Fase 4: filtro no banco de snapshots (Text-to-Query)
curl -s -X POST localhost:8000/market-data/query -H 'Content-Type: application/json' \
  -d '{"question": "Quais são os 3 maiores ativos por market cap no banco?"}'

# Fase 4: Query Rewriting (pergunta sem o termo "halving")
curl -s -X POST localhost:8000/knowledge/retrieve -H 'Content-Type: application/json' \
  -d '{"question": "Por que a recompensa dos mineradores cai pela metade?"}'

# Fase 1: análise estruturada
curl -s -X POST localhost:8000/analyze -H 'Content-Type: application/json' \
  -d '{"question": "Compare BTC e ETH", "assets": ["BTC", "ETH"], "period": "últimos 30 dias"}'
```

Códigos de erro: `422` requisição inválida; `502` resposta do modelo fora do schema (Fase 1); `503` Ollama ou índice indisponível. Na Fase 2, falhas de dados **não** viram erro HTTP: o `CryptoReport` volta com `status` `failed`/`partial`/`unavailable` e a explicação.

## Conversa com memória (Fase 3)

```bash
SID=$(curl -s -X POST localhost:8000/conversations -H 'Content-Type: application/json' -d '{}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
curl -s -X POST localhost:8000/conversations/$SID/messages -H 'Content-Type: application/json' -d '{"message": "Compare BTC e ETH no mercado atual"}'
curl -s -X POST localhost:8000/conversations/$SID/messages -H 'Content-Type: application/json' -d '{"message": "Considere os últimos 30 dias"}'
```

A segunda mensagem herda BTC e ETH e acrescenta o período. A resposta traz:
- a intenção e as entidades resolvidas, com a origem de cada uma (`context_sources`);
- análises anteriores referenciadas e memórias usadas;
- fontes e horários dos dados;
- limitações (ex.: 30 dias indisponíveis no plano gratuito);
- o uso do orçamento de contexto.

Memória:
- **SQLite** é a fonte da verdade (sessões, mensagens, resumos, análises);
- **Mem0 sobre Chroma** guarda memórias episódicas e semânticas por sessão;
- detalhes em [docs/memory-strategy.md](docs/memory-strategy.md).

O `session_id` **não é credencial**: não há autenticação nesta fase.

## Saída da Fase 2 (`CryptoReport`)

```json
{
  "question": "Qual o preço atual do Bitcoin e como variou nas últimas 24 horas?",
  "mode": "react_agent",
  "status": "complete",
  "answer": "Segundo a CoinPaprika (atualizado em 2026-10-20 00:55 UTC), o preço atual do Bitcoin (BTC) é de US$ 81.528,29, com uma variação de -1,11% nas últimas 24 horas.",
  "key_points": [],
  "findings": [{"statement": "Bitcoin (BTC): preço US$ 81.528,29", "source_tool": "get_market_data",
                "source_updated_at": "2026-10-20T00:55:15Z", "retrieved_at": "2026-10-20T00:59:57Z"}],
  "data_sources": [{"provider": "CoinPaprika", "tool": "get_market_data", "endpoint": "/tickers/btc-bitcoin",
                    "source_updated_at": "2026-10-20T00:55:15Z", "retrieved_at": "2026-10-20T00:59:57Z"}],
  "uncertainties": [],
  "tool_calls": [{"tool": "get_market_data", "input": "BTC", "ok": true}],
  "decision_summary": ["get_market_data input=BTC"],
  "model": "gemma4:e2b"
}
```

`status`: `complete` (dados obtidos e resposta verificada), `partial` (parte falhou), `failed` (dados essenciais não obtidos: não há análise, só a explicação), `unavailable` (capacidade fora desta fase, ex.: DeFi).

## Testes

```bash
docker compose run --rm --no-deps app pytest                         # offline (padrão)
docker compose run --rm app pytest -m network                        # API real da CoinPaprika
docker compose run --rm app pytest -m "integration or network" -v    # tudo (Ollama + modelos + internet)
docker compose run --rm app pytest -m "integration or network" tests/integration/test_phase4_live.py -v   # só Fase 4
docker compose run --rm app pytest -m "integration or network" tests/integration/test_phase5_live.py -v   # só Fase 5 (~12 min)
```

Os testes que precisam de rede ou de modelo ficam fora da execução padrão e são ignorados (`skip`) quando o recurso não está disponível.

## Trocar de modelo

`OLLAMA_MODEL` controla o modelo baixado (`ollama-pull`) e o usado pela aplicação. Edite o `.env` ou sobrescreva pelo shell (prioridade sobre o `.env`):

```bash
OLLAMA_MODEL=qwen2.5:7b docker compose up -d app
```

Modelo padrão: **`gemma4:e2b` com thinking desligado**, escolhido pelo benchmark (mais rápido e 100% de acerto). Também avaliados: `qwen2.5:7b`, `gemma4:e4b`, `mistral:7b` (resultados em [docs/stage-1-evidence.md](docs/stage-1-evidence.md#9-benchmark-de-modelos)).

**Thinking:** o `gemma4` raciocina internamente antes de responder; esses tokens não aparecem na saída, mas custam tempo (1.232 contra 211 tokens na mesma pergunta; 197 s → 88 s em CPU). `OLLAMA_REASONING=false` é o padrão; `true` liga e vazio mantém o padrão do modelo.

## Benchmark de modelos (manutenção)

Não é exposto pela API (uma rodada leva de 10 a 40 min em CPU):

```bash
docker compose run --rm app python -m app.services.benchmark_runner --models gemma4:e2b qwen2.5:7b
docker compose run --rm app python -m app.services.benchmark_runner --models gemma4:e4b --reasoning off --pull
docker compose run --rm app python -m app.services.benchmark_runner --compare
```

Mede cold start, latência (média, mediana, p95), tokens e tok/s de prompt e saída, taxa de JSON válido e acerto de `answer_status`/`assets` contra [benchmarks/questions.json](benchmarks/questions.json). Resultados em `benchmarks/results/<data>_<modelo>.json`.

## Executar sem Docker (opcional)

Requer Python 3.12 e Ollama local com os modelos (`ollama pull gemma4:e2b && ollama pull nomic-embed-text`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
OLLAMA_BASE_URL=http://localhost:11434 uvicorn app.main:app --reload
```

## Configuração (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `OLLAMA_MODEL` | `gemma4:e2b` | Modelo de chat |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | URL do Ollama |
| `OLLAMA_TEMPERATURE` | `0` | Temperatura |
| `OLLAMA_REQUEST_TIMEOUT` | `300` | Timeout por chamada ao modelo (s) |
| `OLLAMA_REASONING` | `false` | Thinking (`false`, `true`, vazio = padrão do modelo) |
| `OLLAMA_NUM_CTX` | `8192` | Janela de contexto (o padrão 4096 do Ollama trunca o prompt do agente) |
| `COINPAPRIKA_BASE_URL` | `https://api.coinpaprika.com/v1` | API da CoinPaprika |
| `COINPAPRIKA_API_KEY` | vazio | Opcional (planos pagos); nunca registrada em log |
| `COINPAPRIKA_TIMEOUT` / `_MAX_RETRIES` / `_MAX_RETRY_WAIT` | `15` / `2` / `10` | Timeout, retries e espera máxima por `Retry-After` |
| `AGENT_MAX_ITERATIONS` / `AGENT_MAX_EXECUTION_TIME` | `6` / `600` | Limites do agente ReAct |
| `MEMORY_MAX_TURNS` / `MEMORY_MAX_CHARS` | `4` / `3000` | Limites da memória por sessão |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embeddings da base de conhecimento |
| `KNOWLEDGE_CORPUS_DIR` / `VECTOR_STORE_DIR` / `KNOWLEDGE_COLLECTION` / `KNOWLEDGE_TOP_K` | `knowledge/corpus` / `data/chroma` / `crypto_knowledge` / `3` | Corpus, diretório e coleção do Chroma, trechos por busca |
| `LOG_LEVEL` | `INFO` | Nível de log |
| `CONVERSATION_DB_PATH` | `data/conversations.sqlite3` | SQLite das conversas |
| `MEMORY_WINDOW_MESSAGES` / `SUMMARY_TRIGGER_MESSAGES` | `10` / `4` | Janela curta e gatilho do resumo |
| `CONTEXT_BUDGET_TOKENS` | `3000` | Orçamento do contexto conversacional (tokens estimados) |
| `MEMORY_STORE_DIR` / `MEMORY_COLLECTION` / `MEMORY_HISTORY_DB` | `data/memory_chroma` / `conversation_memory` / `data/mem0_history.sqlite3` | Memória longa (Mem0 sobre Chroma) |
| `MEMORY_TOP_K` / `MEMORY_MIN_SCORE` / `MEMORY_EXTRACTION` | `3` / `0.3` / `true` | Recuperação e extração semântica |
| `STALE_AFTER_MINUTES` | `15` | Resultado anterior mais antigo é marcado como "não atual" |
| `MARKET_DB_PATH` | `data/market.sqlite3` | Banco de snapshots de mercado (Fase 4), separado das conversas |
| `MARKET_DATA_ASSETS` | 10 ids (BTC, ETH, USDT, BNB, SOL, XRP, USDC, ADA, DOGE, TRX) | Ativos coletados quando a requisição não lista nenhum |
| `TEXT_TO_SQL_MAX_ROWS` / `TEXT_TO_SQL_TIMEOUT` | `50` / `2` | Teto de linhas e tempo máximo (s) por consulta gerada |
| `RETRIEVAL_REWRITE_MODE` | `auto` | Query Rewriting: `auto`, `always` ou `never` |
| `RETRIEVAL_REWRITE_DISTANCE` / `RETRIEVAL_MAX_DISTANCE` | `0.33` / `0.38` | Distância que dispara a reescrita / máxima para um trecho contar como evidência |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | `false` / vazio / `crypto-intelligence-agent` | Rastreamento e avaliação no LangSmith (opcional; a chave fica só no `.env`) |
| `VERIFICATION_COHERENCE_LLM` / `VERIFICATION_EVIDENCE_PER_CLAIM` | `true` / `3` | Checagem de coerência por LLM; evidências comparadas por afirmação |
| `UI_HOST_PORT` / `BACKEND_TIMEOUT` | `8501` / `600` | Porta da interface; timeout (s) da interface ao chamar a API |
| `OLLAMA_HOST_PORT` / `APP_HOST_PORT` | `11434` / `8000` | Portas publicadas no host |

## Documentação

- [docs/architecture.md](docs/architecture.md) — arquitetura, compatibilidade de dependências, legado isolado, memória, Chroma
- [docs/orchestrator-comparison.md](docs/orchestrator-comparison.md) — LangChain × LangGraph × LlamaIndex
- [docs/prompt-design.md](docs/prompt-design.md) — PTCF, few-shot, grounding
- [docs/reliability-decisions.md](docs/reliability-decisions.md) — erros, retries, observabilidade, limitações
- [docs/memory-strategy.md](docs/memory-strategy.md) — memória (CoALA, SQLite × Chroma, retenção, orçamento, isolamento)
- [docs/memory-decision.md](docs/memory-decision.md) — sem APIs depreciadas; Mem0 após spikes de LangMem, Mem0 e Letta
- [docs/memory-tools-comparison.md](docs/memory-tools-comparison.md) — LangMem × Mem0 × Letta
- [docs/conversation-evaluation.md](docs/conversation-evaluation.md) — cenários multi-turno A–H e resultados reais
- [docs/vector-store-decision.md](docs/vector-store-decision.md) — decisão FAISS → Chroma: motivação, alternativas, custos e validação
- [docs/tools-catalog.md](docs/tools-catalog.md) — catálogo de ferramentas (hardcoded, semânticas, API, Text-to-Query, retrieval)
- [docs/text-to-query.md](docs/text-to-query.md) — banco de snapshots, coleta, geração, validação e execução de SQL
- [docs/retrieval-agent.md](docs/retrieval-agent.md) — Retrieval Agent e Query Rewriting sobre a coleção existente
- [docs/phase-4-evidence.md](docs/phase-4-evidence.md) — testes e diagnóstico real de falha de recuperação
- [docs/agent-integration.md](docs/agent-integration.md) — agente integrado (create_agent), ferramentas, histórico, compatibilidade de tool calling
- [docs/response-verification.md](docs/response-verification.md) — afirmações, NLI, coerência lógica, status e correção
- [docs/langsmith-evaluation.md](docs/langsmith-evaluation.md) — tracing, dataset, avaliadores e experimentos
- [Report/fase-05-agente-integrado.md](Report/fase-05-agente-integrado.md) — diário da Fase 5: problemas, testes reais e resultados da avaliação (evidências brutas em `docs/evidence/phase-5/` e `docs/evidence/final/`)
- `make report` — gera o relatório técnico final (`docs/final-report.md` e PDF), conferindo os números contra as evidências
- [docs/composition-decision.md](docs/composition-decision.md), [docs/stage-1-evidence.md](docs/stage-1-evidence.md), [docs/stage-2-evidence.md](docs/stage-2-evidence.md) — decisões e evidências

## Fora do escopo

DeFiLlama e CoinGecko (fases futuras; a rota DeFi responde `unavailable`), agendamento automático da coleta de snapshots, Multi-Query e HyDE, e planejamento autônomo.
