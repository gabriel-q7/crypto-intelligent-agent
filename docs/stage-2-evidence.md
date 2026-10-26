# Evidências — Fase 2

Execuções entre 2026-10-20 e 2026-10-26 (UTC). Linux (Manjaro), Docker Compose 5.5.1, Python 3.12 (container), Ollama 0.40.6, **CPU** (12 threads, sem GPU).

| Componente | Versão / modelo |
|---|---|
| Modelo de chat | `gemma4:e2b`, thinking desligado, `num_ctx` 8192 |
| Embeddings | `nomic-embed-text` (274 MB) |
| LangChain | `langchain-core` 1.6.12, `langchain-ollama` 1.1.0, `langchain-classic` 1.0.8, `langchain-chroma` 1.1.0, `langchain-text-splitters` 1.1.3 |
| Vetores | `chromadb` 1.5.9 (até 2026-10-26: `faiss-cpu` 1.15.1 via `langchain-community` 0.4.2; ver seção 9) |
| API | `fastapi` 0.150.0, `uvicorn` 0.54.0 |
| Fonte de dados | CoinPaprika API v1, plano gratuito (`https://api.coinpaprika.com/v1`) |

## 1. Critérios de aceite

| # | Critério | Evidência | Situação |
|---|---|---|---|
| 1 | Fase 1 continua funcionando | 95 testes offline da Fase 1 inalterados e passando; 5 testes de integração da Fase 1 passando; `POST /analyze`, `/analyze/batch`, `/analyze/stream` com o modelo real (seção 4) | ✅ |
| 2 | ReAct clássico executa chamada real à CoinPaprika | `initialize_agent` + `ZERO_SHOT_REACT_DESCRIPTION` → `get_market_data` → `/tickers/eth-ethereum`; resposta com os valores reais (seção 3) | ✅ |
| 3 | PTCF + few-shot | `app/prompts/ptcf.py`; 4 exemplos (mercado, comparação, informação ausente, falha da API); testes verificam seções e cenários | ✅ |
| 4 | Sequencial, paralela e roteamento testados | Testes offline das três + execuções reais pela API (seção 4) | ✅ |
| 5 | Memória limitada e base vetorial local (FAISS → Chroma) | Memória por sessão com limite de turnos, caracteres e quantidade de sessões; índice FAISS persistido em `./data`; busca real (seção 5) | ✅ |
| 6 | Saída validada e falhas explícitas | `CryptoReport` (Pydantic), status `failed`/`partial`/`unavailable`, grounding de valores; testes de falha (seção 6) | ✅ |
| 7 | Callbacks com rastro útil | `TraceCallbackHandler`: decisões, ferramentas (duração, ok/erro), LLM (duração, tokens) | ✅ |
| 8 | Docker Compose reproduz o fluxo | `docker compose up -d --build app` → Ollama + modelos + API saudável | ✅ |
| 9 | Testes unitários passando e integração documentada | 196 offline; 11 de integração/rede passando (seção 2) | ✅ |
| 10 | Decisões e limitações documentadas | `docs/architecture.md`, `orchestrator-comparison.md`, `prompt-design.md`, `reliability-decisions.md` | ✅ |

## 2. Testes

```bash
docker compose run --rm --no-deps app pytest                         # offline
docker compose run --rm app pytest -m "integration or network" -v    # Ollama + internet
```

| Suíte | Resultado | Tempo |
|---|---|---|
| Offline (unit) | **196 passed** | ~4 s |
| Integração + rede | **11 passed** (5 Fase 1 + 3 CoinPaprika real + 3 Fase 2 com modelo) | 340 s |

Saída do teste de integração do agente (2026-10-26), em que todo valor da resposta é conferido contra a resposta real da API:

```
RESPOSTA: Segundo a CoinPaprika (atualizado em 2026-10-26 00:05 UTC), o preço atual do Bitcoin (BTC) é de US$ 79.960,57, com uma variação de -0,96% nas últimas 24 horas.
PASSED
```

Distribuição dos testes offline novos: cliente CoinPaprika (timeouts, 404, 402, 429 curto/longo, 5xx, JSON malformado, payload inesperado, id inválido sem HTTP), ferramentas, grounding (formatos numéricos pt/en, multiplicadores, tolerância, sinal de percentuais), memória, FAISS (embeddings determinísticos), callbacks, redação de segredos, agente ReAct roteirizado (sucesso, valor inventado removido, falha da API, limite de iterações, erro de formato, memória), chains (sequencial, paralela com concorrência medida, falha parcial, JSON inválido, conhecimento, DeFi, rankings, conclusão determinística), roteadores moderno e legado, retry de LLM, API HTTP (`TestClient`) e validade dos exemplos do Swagger.

## 3. Agente ReAct com CoinPaprika real

`POST /agent` com `{"question": "Qual o preço atual do Ethereum e a variação em 24 horas?", "session_id": "demo", "include_trace": true}`, em 2026-10-26:

```
HTTP 200 em 32.3s
status=complete
answer: Segundo a CoinPaprika (atualizado em 2026-10-26 00:10 UTC), o preço atual do Ethereum (ETH) é de US$ 2.329,72, com uma variação de -1,07% nas últimas 24 horas.
data_sources: /tickers/eth-ethereum  source_updated_at=2026-10-26T00:10:19Z  retrieved_at=2026-10-26T00:12:30Z
trace: llm 21150 ms → decision get_market_data → tool get_market_data ok 632 ms → llm 10443 ms → finish
```

Continuação na mesma sessão, `{"question": "E o Bitcoin?", "session_id": "demo"}`: o agente entendeu a referência pelo histórico, chamou `get_market_data("BTC")` de novo (não reaproveitou valor antigo) e respondeu "US$ 80.033,51, -0,84% (atualizado em 2026-10-26 00:10 UTC)" em 18,8 s.

## 4. Chains e roteamento (API, modelo real, 2026-10-26)

| Requisição | Rota (classificador) | Status | Tempo | Resultado |
|---|---|---|---|---|
| "Compare BTC, ETH e SOL nas últimas 24 horas" | `asset_comparison` (LLM) | complete | 66,9 s | "Variação 24h, do maior para o menor: SOL -0,79% > BTC -0,84% > ETH -1,07%. Market cap: BTC > ETH > SOL" (conclusão calculada no código; 3 ativos + global buscados em paralelo) |
| "Como está o preço do Ethereum hoje?" | `market_analysis` (LLM) | complete | 36,6 s | "US$ 2329.72, -1.07% em 24h" — valores conferidos |
| "O que é staking de ETH?" com `router=legacy` | `knowledge_retrieval` via **LLMRouterChain** | complete | 23,4 s | Resposta a partir de `ethereum-staking.md` (32 ETH, pools, staking líquido) |
| "Como evoluiu o TVL do Aave neste mês?" | `defi_analysis` (LLM) | **unavailable** | 9,3 s | "depende do DeFiLlama, previsto para uma fase futura… nenhum valor foi estimado" |
| Fase 1 `POST /analyze` "O que é staking de ETH?" | — | `answered` | 23,5 s | Conceito explicado |
| Fase 1 `POST /analyze/stream` | — | — | 27,2 s | 178 eventos NDJSON: `partial` … `final` validado |

## 5. CoinPaprika e conhecimento

`GET /health/coinpaprika` (2026-10-26): 7/7 verificações ok — search, ticker, OHLCV do dia, perfil, global, ativo inexistente → `not_found`, id inválido → `invalid_argument` (sem chamada HTTP).

Comportamentos da API real observados durante a implementação:

| Caso | Resposta da API | Tratamento |
|---|---|---|
| `/search?q=` sem barra final | 301 | Cliente usa `/search/` |
| Id inexistente | 404 `{"error":"id not found"}` | `not_found` |
| `/ohlcv/historical` (plano gratuito) | **402** + `retry-after` de ~12 dias | `plan_restricted`, sem retry |
| Cota | cabeçalhos `ratelimit-limit: 20000` (mensal) | 429 com `Retry-After` > 10 s falha na hora |
| `/global` | `last_updated` em epoch | Convertido para UTC |

Base de conhecimento: 7 documentos → 18 trechos (800/120), `nomic-embed-text`, índice de ~66 KB em `data/faiss_index` + `manifest.json`. A busca "o que é TVL" trouxe primeiro `defi-metrics.md`; "halving" trouxe `bitcoin.md` (distância L2 0,658). O teste de integração com embeddings reais confirma `bitcoin.md` para "o que acontece no halving do bitcoin".

## 6. Problemas encontrados com o modelo real e correções

| Sintoma (execução real) | Causa | Correção |
|---|---|---|
| Resumo da comparação: "o Bitcoin apresentou a maior variação negativa" (ETH caiu mais) | Modelo 2B erra ordenações, mesmo com números certos | 1º: rankings calculados no código e enviados ao modelo → **não bastou** (repetiu o erro); 2º: conclusão da comparação montada pelo código, resumo do LLM descartado → validado em 2026-10-26 |
| "o Ethereum apresenta capitalização maior" (BTC é ~5,6×) | Idem | Ranking de market cap no código |
| LLM colocou preços no campo `uncertainties` | Campo não era verificado | `key_points` e `uncertainties` do LLM também passam pelo grounding |
| "$1.638 trilhões" (correto) removido | "1.638" lido como milhar com multiplicador | Com multiplicador, aceitar as leituras decimal e de milhar |
| "caiu 1,11%" rejeitado (fonte: -1,11) | Sinal está na palavra, não no número | Percentuais comparados em módulo (limitação documentada) |
| Regex capturava a vírgula final ("81.528,29,") | Padrão numérico guloso | Número não termina em separador |
| `/search` retornava 301 | Endpoint exige barra final | `/search/` + `follow_redirects` |

## 7. Limitações conhecidas

1. **"Esse ativo está caro?"** (2026-10-26): o agente consultou a base de conhecimento e explicou que faltam dados atuais, mas não pediu o ativo como no exemplo few-shot. Status `complete`, sem valores inventados.
2. A verificação confere valores, não afirmações qualitativas; fora da rota de comparação, uma conclusão comparativa errada sem número pode passar.
3. Latência em CPU: 15–70 s por requisição (agente ~20–40 s; comparação com 3 ativos ~67 s).
4. ~~`langchain-community` em descontinuação~~: resolvido com a troca para Chroma (seção 9). Em troca, o `chromadb` aumenta a imagem.
5. Memória só em processo: reiniciar a API apaga as sessões.
6. O agente ReAct não tem retry de LLM (o legado exige `BaseLanguageModel`); falha de conexão vira `CryptoReport` `failed`.

## 9. Migração FAISS → Chroma (2026-10-26)

Decisão do usuário: usar Chroma no lugar do FAISS. As seções 1–7 foram validadas com FAISS; a migração foi validada de novo:

| Verificação | Resultado |
|---|---|
| Compatibilidade | `langchain-chroma` 1.1.0 + `chromadb` 1.5.9 com `langchain-core` 1.6.12 fixado, sem conflitos; `langchain-community` e `faiss-cpu` removidos |
| Testes offline | 199 passed — sem o aviso de descontinuação que aparecia antes |
| `POST /knowledge/ingest` (embeddings reais) | 18 trechos em 3,5 s → `data/chroma/chroma.sqlite3` + `manifest.json` (`"vector_store": "chroma"`) |
| `GET /health` | `degraded` (sem coleção) → `ok` após a ingestão |
| Busca (distância de cosseno, menor = melhor) | "o que é TVL" → `defi-metrics.md` 0,276 · "halving do bitcoin" → `bitcoin.md` 0,268 · "riscos do staking de ETH" → `ethereum-staking.md` 0,182 · "tipos de stablecoins" → `stablecoins.md` 0,266 |
| `POST /route` "O que é TVL e quais são suas limitações?" | `knowledge_retrieval`, `complete`, fontes `defi-metrics.md` e `market-metrics.md` |
| `POST /agent` "O que é staking de ETH e quais são seus riscos?" | `complete`; o agente usou `search_knowledge_base` (ok); resposta com 32 ETH, slashing, contrato inteligente, custódia, fila de saída |
| Integração `test_knowledge_index_with_real_embeddings` | passed |

Achado durante a validação: dois trechos do mesmo arquivo geravam achados duplicados ("Ethereum e staking" duas vezes); `findings_from` passou a deduplicar por arquivo (teste novo).

## 8. Reproduzir

```bash
cp .env.example .env
docker compose up -d --build app
curl -X POST localhost:8000/knowledge/ingest      # cria a coleção Chroma em ./data/chroma
curl -s localhost:8000/health/coinpaprika
curl -s -X POST localhost:8000/agent -H 'Content-Type: application/json' \
  -d '{"question": "Qual o preço atual do Ethereum e a variação em 24 horas?", "include_trace": true}'
docker compose run --rm --no-deps app pytest
docker compose run --rm app pytest -m "integration or network" -v
```

Ou pelo Swagger em `http://localhost:8000/docs`, com os exemplos prontos.
