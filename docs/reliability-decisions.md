# Decisões de confiabilidade — Fase 2

Princípio: **nenhuma resposta com aparência de sucesso sem dados reais.** Toda falha vira estado explícito (`status`, `uncertainties`, `error.kind`), nunca um valor substituto.

## 1. Integração CoinPaprika

| Situação | Comportamento | Onde |
|---|---|---|
| Timeout / erro de rede | Retry com backoff (0,5 s, 1 s); depois `CoinPaprikaError(timeout\|network)` | `CoinPaprikaClient._get` |
| HTTP 5xx | Retry com backoff; depois `http_error` | idem |
| HTTP 429 | Retry só se `Retry-After` ≤ `COINPAPRIKA_MAX_RETRY_WAIT` (10 s); senão falha imediata `rate_limited` (a cota do plano é mensal e o `retry-after` pode ser de dias) | idem |
| HTTP 402 | `plan_restricted`, sem retry (ex.: OHLCV histórico no plano gratuito) | idem |
| HTTP 404 | `not_found`, sem retry | idem |
| Corpo não-JSON ou campos inesperados | `malformed` (validação Pydantic do payload) | `_handle_response`, `_parse` |
| Id inválido | `invalid_argument`, **sem chamada HTTP** | `validate_coin_id` |
| Entrada livre do modelo | Normalizada (`"bitcoin"`, `asset=eth`, primeira linha) e validada | `AssetInput` |

Configuração fora do código: `COINPAPRIKA_BASE_URL`, `COINPAPRIKA_API_KEY` (opcional, enviada só no cabeçalho e redigida nos logs), `COINPAPRIKA_TIMEOUT`, `COINPAPRIKA_MAX_RETRIES`, `COINPAPRIKA_MAX_RETRY_WAIT`.

**Timestamps preservados:** `source_updated_at` (campo `last_updated` da fonte; para `/global`, conversão de epoch) e `retrieved_at` (horário da consulta), em toda observação, achado e fonte.

## 2. Agente ReAct

| Risco | Mitigação |
|---|---|
| Laço infinito / demora | `AGENT_MAX_ITERATIONS` (6) e `AGENT_MAX_EXECUTION_TIME` (600 s); `early_stopping_method="force"` |
| Modelo fora do formato | `handle_parsing_errors` devolve instrução corretiva (conta como iteração) |
| Limite atingido | Status `failed` (sem dados) ou `partial` (com dados), com resposta fixa explicando |
| Ferramenta falhou | Ferramenta devolve `ok=false` + `instruction` "não invente"; se todas as chamadas de mercado falharem, a resposta do modelo é **substituída** pela explicação da falha |
| Valor inventado | Verificação de grounding remove frases com valores sem correspondência nas observações |
| Ollama indisponível | `LLMUnavailable` → `CryptoReport` `failed` com o motivo |
| Contexto truncado | `OLLAMA_NUM_CTX=8192` |

## 3. Chains

| Situação | Comportamento |
|---|---|
| JSON inválido / campo ausente / esquema violado (LLM) | Extração de ativos: incerteza registrada. Rota: fallback por palavras-chave. Análise: `partial` com os dados preservados nos achados e "análise do modelo inválida" |
| Nenhum ativo identificado | `failed` pedindo o ativo |
| Todos os dados de mercado falharam | `failed`, sem chamar o LLM de análise |
| Falha parcial (comparação) | `partial`: o ativo que falhou entra nas incertezas, os demais seguem |
| Dado defasado | Fonte atualizada há mais de 1 h antes da consulta → incerteza "possivelmente defasado" |
| Comparações erradas do modelo | Rankings calculados no código (variação 24h, market cap); na rota de comparação, a conclusão (`answer`) é montada pelo código e o resumo livre do LLM é descartado |
| Pontos-chave e incertezas do LLM | Também verificados; itens com valores não confirmados são descartados |
| Rota DeFi | `unavailable`, explicando que o DeFiLlama não foi integrado |
| Coleção Chroma ausente | `failed` com a instrução de ingestão |

## 4. Retries: o que é e o que não é repetido

- **Repetido:** falhas transitórias de rede ou servidor da CoinPaprika (limitadas e com backoff); falhas de **conexão com o Ollama** nas etapas JSON das chains (`with_transient_retry`: 2 tentativas, backoff exponencial com jitter, só para `ConnectionError`/`httpx.TransportError`).
- **Não repetido:** 4xx (exceto 429 curto), validação e respostas inválidas do LLM. Repetir com temperatura 0 tende a gerar a mesma saída. O agente já tem o próprio laço de correção de formato.

### Mapeamento para HTTP

| Situação | Resposta |
|---|---|
| Requisição inválida | `422` (validação Pydantic do FastAPI) |
| Fase 1: saída do modelo fora do schema | `502` `invalid_model_output` |
| Ollama inacessível (após retry) | `503` `llm_unavailable` |
| Coleção Chroma ausente | `503` `knowledge_index_unavailable` |
| Fase 2: dado de mercado indisponível, falha parcial, DeFi | `200` com `CryptoReport.status` = `failed` / `partial` / `unavailable` — o relatório É o resultado, com a explicação |
| Stream já iniciado | evento NDJSON `{"type": "error"}` (o status 200 já foi enviado) |

O agente ReAct não usa `with_retry`: o `initialize_agent` exige um `BaseLanguageModel`, e o wrapper de retry é um `Runnable`. Falha de conexão no agente vira `CryptoReport` `failed` com o motivo.

## 5. Observabilidade

- **Callback** `TraceCallbackHandler`: decisões, ferramentas (duração, ok/erro + `error.kind`), LLM (duração, tokens), erros de chain. Disponível em `agent.last_trace` e impresso com `--trace`.
- **Logs:** formato único `asctime level logger mensagem`; requisições da CoinPaprika (caminho, status, tentativa, duração), retries, falhas de validação, rota decidida e status final do agente.
- **Segredos:** `SecretRedactionFilter` substitui a chave da CoinPaprika por `***`; o logger do `httpx` (que registraria URLs) fica em `WARNING`; cabeçalhos nunca são registrados.

## 6. Limitações conhecidas

1. **A verificação confere valores, não conclusões.** Uma frase comparativa errada sem número passa. Na rota de comparação isso foi resolvido com a conclusão determinística; no agente ReAct e nos `key_points`, uma afirmação comparativa errada sem número ainda pode passar.
2. **Percentuais comparados em módulo:** "subiu 1%" quando caiu 1% não é detectado.
3. **Tolerância de 1%** aceita arredondamentos e também desvios pequenos.
4. **Números sem moeda nem %** ("cotado a 81 mil") não são verificados.
5. **ReAct textual** com modelo pequeno: erros de formato custam iterações; o legado não tem tool calling nativo.
6. **Plano gratuito da CoinPaprika:** sem séries históricas (402); a cota é de 20 mil chamadas/mês, e `resolve_asset` gasta uma chamada de busca por ativo.
7. **Dependências do Chroma:** `chromadb` traz `onnxruntime` e OpenTelemetry, deixando a imagem maior. (O risco anterior, `langchain-community` em descontinuação, deixou de existir com a troca de FAISS por Chroma.)
8. **Memória só em processo:** encerra com o script; não há persistência entre sessões (fora do escopo).
