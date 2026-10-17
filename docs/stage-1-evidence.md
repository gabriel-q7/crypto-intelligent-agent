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
docker compose run --rm app python -m scripts.analyze "Compare BTC e ETH" --assets BTC ETH --period "últimos 30 dias"
```

Resultado com o prompt revisado (seção 7), idêntico em 3 execuções:

```json
{
  "intent": "comparison",
  "assets": ["BTC", "ETH"],
  "requested_metrics": ["price_history", "return_pct", "volatility", "max_drawdown"],
  "period": "últimos 30 dias",
  "investigation_steps": [
    "Obter price_history diário de BTC e ETH em USD nos últimos 30 dias",
    "Calcular return_pct, volatility e max_drawdown de cada ativo",
    "Comparar desempenho e risco entre BTC e ETH"
  ],
  "missing_information": [],
  "assumptions": [
    "comparação por retorno e risco, pois o critério não foi informado",
    "cotação em USD",
    "preços de fechamento diários"
  ],
  "confidence": 0.8
}
```

Tempo: ~30–110 s por chamada (a primeira inclui carga do modelo na memória).

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
80 passed, 5 deselected in 0.72s
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

Após a inclusão de `answer` (5 testes, incluindo "pergunta conceitual é respondida"):

```
5 passed, 80 deselected in 409.83s
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

## 7. Revisão do prompt (resultado do "Compare BTC e ETH")

A primeira versão do prompt produzia, para `"Compare BTC e ETH" --assets BTC ETH --period "últimos 30 dias"`:

```json
{
  "requested_metrics": ["rendimento", "volatilidade"],
  "investigation_steps": ["calcular o rendimento médio dos últimos 30 dias", "..."],
  "missing_information": [],
  "assumptions": ["moeda de cotação USD"],
  "confidence": 1.0
}
```

| Problema | Causa | Correção |
|---|---|---|
| Etapas calculavam sem obter dados | Nada exigia etapa de coleta | Regra: a primeira etapa obtém os dados de mercado |
| `requested_metrics` vago e instável ("rendimento", "preço_atual") | Texto livre | Vocabulário sugerido em snake_case (`METRIC_VOCABULARY`: `price_history`, `return_pct`, `volatility`, ...) e exigência dos dados de base |
| `missing_information` ora com dados de mercado, ora vazio | Regra 3 original era ambígua | `requested_metrics` = dados que APIs buscarão; `missing_information` = só o que o usuário não informou e nenhuma API resolve |
| Critério inferido não registrado; `confidence` 1.0 | Sem régua de calibração | Régua explícita (0.9–1.0 / 0.6–0.8 / < 0.6) e premissas obrigatórias para critérios inferidos |
| Moeda/granularidade ausentes | Modelo 7B ignora regras abstratas | Exemplo few-shot (SOL × AVAX) mostrando o padrão |
| Ativos copiados do campo "Ativos informados" em vez de extraídos do texto | Exemplo trazia os ativos nesse campo | No exemplo, ativos aparecem só no texto da pergunta |
| `"período de 2026 é um erro, deve ser 2023"` | O modelo assume a data do seu treino | `Data de referência` (data atual) injetada no prompt + regra para nunca corrigir o período do usuário |

Cada ajuste foi validado reexecutando 5 perguntas (comparação, preço atual/previsão, TVL do Aave, staking, risco de SOL).

### Limitações que persistem após a revisão

- `assets` ainda oscila em perguntas conceituais ("O que é staking de ETH?" retornou `[]` na última execução e `["ETH"]` na anterior).
- A ressalva "o agente não faz previsões" (regra 10) nem sempre aparece em "BTC vai subir amanhã?".
- `confidence` concentra-se em 0.6 e 0.8 — melhor que 1.0 fixo, mas ainda é autoavaliação grosseira.
- Ajustes de prompt em modelo 7B têm efeitos colaterais: cada mudança corrigiu um caso e alterou outros. Uma rede de segurança determinística (ex.: extrair tickers conhecidos do texto) fica para a próxima etapa, junto com o catálogo de ativos das APIs.

## 8. Resposta mínima ao usuário (`answer`)

O plano sozinho não respondia nada ao usuário: "O que é staking de ETH?" retornava só etapas como "Definir o que é staking". Foram adicionados `answer_status` (`answered` / `requires_market_data` / `needs_clarification`) e `answer` (2 a 4 frases).

Resultado final com o modelo real:

| Pergunta | `answer_status` | `answer` (resumo) |
|---|---|---|
| O que é staking de ETH? | `answered` | Explica o conceito (depósito de ETH para validar blocos e receber recompensas) |
| O que é uma exchange descentralizada? | `answered` | Explica DEX, contratos inteligentes, ausência de intermediário |
| Compare BTC e ETH (30 dias) | `requires_market_data` | "Nesta fase não tenho dados de mercado atualizados [...] não consigo comparar [...]" + o que será medido |
| BTC vai subir amanhã? Qual o preço atual? | `requires_market_data` | "Não consigo prever se o BTC vai subir amanhã, pois não tenho dados de mercado atualizados [...]" |
| Como evoluiu o TVL do Aave neste ano? | `requires_market_data` | Declara ausência de dados e o que será medido |
| Qual o risco de manter SOL em carteira? | `answered` | Riscos qualitativos (volatilidade, segurança, desenvolvimento), sem números |

A trava de valores de mercado não precisou agir nestas execuções; é coberta por testes unitários (valores monetários, percentuais, remoção apenas das frases afetadas, fatos de protocolo preservados).

Problemas encontrados e corrigidos durante a implementação:

| Sintoma | Causa | Correção |
|---|---|---|
| "BTC preço" e "Aave TVL" falhavam com `investigation_steps` vazio | `answer_status` era gerado **depois** das etapas; o modelo copiava `[]` do exemplo conceitual antes de decidir o tipo de resposta | `answer_status` movido para logo após `intent` (o modelo gera o JSON em ordem) |
| "BTC preço" ainda sem etapas, mas com `requested_metrics: ["price"]` e resposta correta | Omissão em pergunta simples | Etapa de coleta derivada deterministicamente de `requested_metrics` + `assets`, registrada em `assumptions` |
| Resposta de dados não dizia que faltavam dados | Regra pedia só "explique o que será analisado" | Regra e exemplo exigem declarar explicitamente a ausência de dados atualizados |
| Trava rejeitava a análise inteira | Validação lançava erro | Remove só as frases com valores e acrescenta aviso padrão |

### Limitações da resposta mínima

- **Erros conceituais não são detectados.** Na resposta sobre staking o modelo escreveu "depositar ETH em nós de mineração" — o Ethereum não usa mineração desde a migração para proof-of-stake (2022). A trava só cobre valores de mercado, não a correção do conteúdo.
- **Classificação de risco oscila:** "risco de manter SOL" foi classificada como `answered` (resposta qualitativa, sem plano), apesar da regra que reserva `answered` a conceitos.
- **Trava heurística:** falso positivo em termos como "ataque de 51%"; falso negativo em valores sem moeda ("65 mil").

## 9. O que não foi validado

- **Execução fora do Docker** com Python 3.12 local: o host tem apenas Python 3.14; toda validação foi feita nos containers (Python 3.12.15).
- **GPU:** o `compose.yaml` não reserva GPU; não testado com aceleração.
