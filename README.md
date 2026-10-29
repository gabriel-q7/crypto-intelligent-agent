# Crypto Intelligence Agent

Aplicação HTTP (FastAPI) com LangChain e Ollama para análise de criptoativos.

- **Fase 1 — análise estruturada:** interpreta a pergunta e devolve um plano de análise + resposta mínima em JSON validado, sem dados externos.
- **Fase 3 — conversa com memória:** sessões persistentes (SQLite) com janela, resumo incremental e memória episódica/semântica (Mem0 sobre Chroma); interpreta continuações, correções e referências a resultados anteriores.
- **Fase 2 — agente com ferramentas:** agente ReAct clássico e chains roteadas que consultam **dados reais da CoinPaprika**, buscam conceitos numa **base de conhecimento local (Chroma)**, mantêm **memória de conversa limitada** e devolvem um `CryptoReport` validado, com fontes, horários e incertezas.

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
  tools/             cliente e ferramentas CoinPaprika; ferramenta de busca de conhecimento
  knowledge/         ingestão do corpus e coleção Chroma
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
```

API em `http://localhost:8000`; documentação interativa (Swagger) em **`http://localhost:8000/docs`**.

O `ollama-pull` baixa o modelo de chat (`OLLAMA_MODEL`) e o de embeddings (`EMBEDDING_MODEL`) para o volume `ollama_models`; o download acontece uma vez. A aplicação acessa o Ollama pelo nome do serviço (`http://ollama:11434`).

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
| `GET /conversations/{id}` | 3 | Consulta/retoma a conversa (também após reinício da API) |
| `POST /conversations/{id}/messages` | 3 | Envia uma mensagem: intenção, contexto, execução e resposta com evidências |
| `GET /conversations/{id}/messages` | 3 | Histórico ordenado (`after_seq`, `limit`) |
| `GET /conversations/{id}/summary` | 3 | Resumo incremental da sessão e fatos preservados |
| `DELETE /conversations/{id}` | 3 | Apaga a conversa (SQLite) e suas memórias (Chroma) |
| `POST /route` | 2 | Roteamento: mercado (sequencial), comparação (paralela), conhecimento ou DeFi (indisponível); `router`: `modern` ou `legacy` |
| `GET /knowledge/search?q=` | 2 | Busca semântica na base local |
| `POST /knowledge/ingest` | 2 | Recria a coleção Chroma a partir de `knowledge/corpus` |

Exemplos:

```bash
# Agente ReAct com dados reais da CoinPaprika
curl -s -X POST localhost:8000/agent -H 'Content-Type: application/json' \
  -d '{"question": "Qual o preço atual do Bitcoin?", "session_id": "minha-sessao", "include_trace": true}'

# Comparação em paralelo (rota escolhida automaticamente)
curl -s -X POST localhost:8000/route -H 'Content-Type: application/json' \
  -d '{"question": "Compare BTC, ETH e SOL nas últimas 24 horas"}'

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
- [docs/composition-decision.md](docs/composition-decision.md), [docs/stage-1-evidence.md](docs/stage-1-evidence.md), [docs/stage-2-evidence.md](docs/stage-2-evidence.md) — decisões e evidências

## Fora do escopo

DeFiLlama e CoinGecko (fases futuras; a rota DeFi responde `unavailable`), memória persistente entre execuções e planejamento autônomo.
