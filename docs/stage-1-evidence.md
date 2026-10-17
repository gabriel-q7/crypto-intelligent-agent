# Evidências — Etapa 1

Execução em 2026-10-17, Linux (Manjaro), Docker Compose 5.5.1, imagem `python:3.12-slim` (Python 3.12.15), `ollama/ollama:latest`, modelo `qwen2.5:7b` (4.7 GB), **inferência em CPU** (12 threads, sem GPU).

Pacotes: `langchain-core 1.6.12`, `langchain-ollama 1.1.0`, `pydantic 2.14.1`, `pytest 9.1.1`.

## 1. Subida do ambiente

```bash
cp .env.example .env
docker compose up -d ollama ollama-pull
```

`ollama` ficou `healthy`; `ollama-pull` baixou `qwen2.5:7b` para o volume `ollama_models` e saiu com código 0. Execuções seguintes reutilizam o volume (o `ollama-pull` só verifica o manifesto).

## 2. CLI — análise padrão (LCEL)

```bash
docker compose run --rm app
# = python -m scripts.analyze "Compare BTC e ETH nos últimos 30 dias" --assets BTC ETH --period "últimos 30 dias"
```

```json
{
  "intent": "comparison",
  "assets": ["BTC", "ETH"],
  "requested_metrics": ["rendimento", "volatilidade"],
  "period": "últimos 30 dias",
  "investigation_steps": [
    "calcular o rendimento médio diário",
    "calcular a volatilidade diária",
    "comparar os resultados entre BTC e ETH"
  ],
  "missing_information": [],
  "assumptions": ["moeda de cotação USD"],
  "confidence": 1.0
}
```

Tempo total: ~110 s (inclui carga do modelo na memória na primeira chamada).

## 3. CLI — pergunta que pede dado atual (`@chain`)

```bash
docker compose run --rm app python -m scripts.analyze "BTC vai subir amanhã? Qual o preço atual?" --composition imperative
```

```json
{
  "intent": "price_analysis",
  "assets": ["BTC"],
  "requested_metrics": ["preço_atual"],
  "period": null,
  "investigation_steps": ["Assumir cotação em USD", "Determinar preço atual de BTC"],
  "missing_information": ["preço atual de BTC"],
  "assumptions": ["moeda de cotação USD"],
  "confidence": 0.7
}
```

O modelo **não inventou preço**: o dado foi para `requested_metrics` e `missing_information`.

## 4. Runnables — `python -m scripts.demo_runnables all`

Tempo total: ~5 min 54 s.

### invoke — LCEL vs `@chain`

Mesma pergunta ("Vale a pena comparar o desempenho de BTC e ETH no último trimestre?", ativos `btc`, `eth` normalizados para `BTC`, `ETH`). As duas composições retornaram **JSON idêntico**:

```json
{
  "intent": "comparison",
  "assets": ["BTC", "ETH"],
  "requested_metrics": ["rendimento"],
  "period": "último trimestre",
  "investigation_steps": [
    "definir o período de análise",
    "coletar dados de rendimento para BTC e ETH",
    "comparar os rendimentos"
  ],
  "missing_information": [],
  "assumptions": ["moeda de cotação USD"],
  "confidence": 1.0
}
```

### batch — 3 perguntas, `max_concurrency=2`

| Pergunta | intent | assets | Observação |
|---|---|---|---|
| Qual o risco de manter SOL em carteira? | `risk_assessment` | `SOL` | preço, volume, market cap e TVL listados em `missing_information` |
| Como evoluiu o TVL do Aave neste ano? | `trend_analysis` | `[]` | **não extraiu `AAVE`** para `assets` (ver limitações) |
| O que é staking de ETH? | `general_information` | `ETH` | — |

Todas as saídas passaram na validação Pydantic.

### stream

53 objetos parciais emitidos pelo `JsonOutputParser`; chaves acumulando progressivamente:

```
parcial #1:  chaves=[]
parcial #20: chaves=['intent', 'assets', 'requested_metrics', 'period', 'investigation_steps']
parcial #53: chaves=['intent', 'assets', 'requested_metrics', 'period', 'investigation_steps', 'missing_information', 'assumptions', 'confidence']
```

O objeto final (último parcial) foi validado com `CryptoAnalysisRequest` e é igual ao resultado do `invoke`.

## 5. Testes

### Unitários (sem modelo; LLM falso `FakeListChatModel`)

```bash
docker compose run --rm --no-deps app pytest
```

```
60 passed, 4 deselected in 0.51s
```

Cobrem: schemas (normalização, campos obrigatórios, limites), prompts (tipos, regras anti-invenção, renderização, escape do JSON schema), parsing (`JsonOutputParser`, validação, tradução de erros), as **duas composições** com o mesmo conjunto de testes (invoke, batch, stream, JSON inválido, schema violado, falha de conexão), equivalência LCEL × `@chain`, streaming parcial, isolamento de falhas no batch, configuração por env e `LLMUnavailableError` com Ollama real inacessível.

### Integração (Ollama + `qwen2.5:7b`)

```bash
docker compose run --rm app pytest -m integration -v
```

```
test_invoke_returns_valid_analysis[build_lcel_chain] PASSED
test_invoke_returns_valid_analysis[build_imperative_chain] PASSED
test_batch PASSED
test_stream PASSED
4 passed, 60 deselected in 173.88s
```

## 6. Limitações observadas

1. **Streaming só existe antes da validação.** O Pydantic precisa do objeto completo; por isso:
   - `build_lcel_chain(...).stream()` emite **um único chunk** (o `RunnableLambda` de validação acumula a entrada);
   - `build_imperative_chain(...).stream()` também emite um único chunk (a função `@chain` só retorna no fim);
   - o streaming útil usa `build_json_chain` (sem validação), e os parciais **não são validados** — podem ter campos ausentes ou strings truncadas. `stream_analysis` valida apenas o último objeto.
2. **Qualidade do modelo 7B:**
   - extração de ativos falha quando o nome não é ticker (`Aave` → `assets: []`);
   - `confidence` tende a 1.0 em perguntas claras — pouco calibrada, é autoavaliação do modelo;
   - premissas irrelevantes às vezes aparecem ("moeda de cotação USD" para pergunta conceitual), e etapas podem misturar premissa com ação ("Assumir cotação em USD").
3. **Sem garantia absoluta de não-invenção.** A regra está no prompt de sistema e os testes reais não mostraram números inventados, mas não há verificação automática de que o texto livre (ex.: `investigation_steps`) não contém valores de mercado.
4. **Desempenho em CPU:** ~30–110 s por chamada neste ambiente; `batch` com `max_concurrency=2` é limitado pelo Ollama, que processa as requisições conforme sua configuração de paralelismo.
5. **Enum de intenção estrito:** se o modelo responder uma intenção fora da lista, a saída é rejeitada (`AnalysisOutputError`) em vez de mapeada para `other`. Não ocorreu nas execuções, mas não há retry automático.

## 7. O que não foi validado

- **Execução fora do Docker** com Python 3.12 local: o host tem apenas Python 3.14; toda validação foi feita nos containers (Python 3.12.15).
- **GPU:** o `compose.yaml` não reserva GPU; não testado com aceleração.
