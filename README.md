# Crypto Intelligence Agent — Etapa 1

Chain LangChain + Ollama que **interpreta** perguntas sobre criptomoedas e devolve um plano de análise em JSON validado por Pydantic. Nesta etapa **não há consulta a APIs de mercado** e o modelo é instruído a nunca inventar preços, volumes ou dados atuais.

```
CryptoQuestion → ChatPromptTemplate → ChatOllama (qwen2.5:7b) → JsonOutputParser → CryptoAnalysisRequest
```

## Estrutura

```
app/
  config.py        configuração via variáveis de ambiente (.env)
  errors.py        AnalysisOutputError, LLMUnavailableError
  schemas/         CryptoQuestion, CryptoAnalysisRequest
  prompts/         PromptTemplate + ChatPromptTemplate e instruções de sistema
  chains/          chain em LCEL (padrão) e em @chain
  services/        ChatOllama, validação, invoke/batch/stream com tratamento de erros
scripts/
  analyze.py         CLI de análise
  demo_runnables.py  demonstração de invoke, batch e stream
  benchmark.py       benchmark de modelos (latência, tok/s, aderência)
  compare_benchmarks.py  comparação de resultados salvos
benchmarks/
  questions.json     perguntas do benchmark com respostas esperadas
  results/           resultados (JSON) de cada execução
tests/
  unit/            sem modelo (LLM falso)
  integration/     exigem Ollama + modelo (marcador `integration`)
docs/
  architecture.md, composition-decision.md, stage-1-evidence.md
Report/
  diário técnico por fase (base do relatório final)
```

## Executar com Docker Compose

```bash
cp .env.example .env
docker compose up -d ollama ollama-pull   # sobe o Ollama e baixa o modelo (volume persistente)

# análise padrão
docker compose run --rm app

# pergunta própria
docker compose run --rm app python -m scripts.analyze \
  "Compare BTC e ETH" --assets BTC ETH --period "últimos 30 dias"

# mesma chain em composição imperativa (@chain)
docker compose run --rm app python -m scripts.analyze "O que é staking de ETH?" --composition imperative

# invoke (LCEL e @chain), batch e stream
docker compose run --rm app python -m scripts.demo_runnables          # todos
docker compose run --rm app python -m scripts.demo_runnables stream   # só um
```

A aplicação acessa o Ollama por `http://ollama:11434` (nome do serviço). Os modelos ficam no volume `ollama_models`, então o download acontece uma vez.

## Trocar de modelo

`OLLAMA_MODEL` controla tanto o modelo baixado (`ollama-pull`) quanto o usado pela aplicação. Três formas, da mais permanente à mais pontual:

```bash
# 1. Padrão do projeto: editar .env
OLLAMA_MODEL=gemma4:e4b

# 2. Só nesta execução (o shell tem prioridade sobre o .env; o modelo é baixado automaticamente)
OLLAMA_MODEL=gemma4:e4b docker compose run --rm app python -m scripts.analyze "O que é staking de ETH?"

# 3. Só nesta análise, com o modelo já baixado
docker compose run --rm app python -m scripts.analyze "O que é staking de ETH?" --model gemma4:e4b
```

Modelos já avaliados: `qwen2.5:7b`, `gemma4:e4b`, `mistral:7b` (resultados em [docs/stage-1-evidence.md](docs/stage-1-evidence.md#9-benchmark-de-modelos)).

**Thinking:** o `gemma4` raciocina internamente antes de responder. Esses tokens não aparecem na saída, mas custam tempo: na pergunta "BTC vai subir amanhã?", 1.232 tokens gerados com thinking contra 211 sem (197 s → 88 s em CPU). Controle com `OLLAMA_REASONING=false` (ou `true`); vazio mantém o padrão do modelo. Modelos sem thinking (`qwen2.5`) ignoram `false`.

Para baixar outro modelo sem executar a aplicação: `OLLAMA_MODEL=<modelo> docker compose run --rm ollama-pull`.

## Benchmark de modelos

```bash
# um ou mais modelos; --pull baixa os que faltarem
docker compose run --rm app python -m scripts.benchmark --models qwen2.5:7b gemma4:e4b --runs 3

# mesmo modelo com e sem thinking
docker compose run --rm app python -m scripts.benchmark --models gemma4:e4b --reasoning off
docker compose run --rm app python -m scripts.benchmark --models gemma4:e4b --reasoning on

# comparar resultados salvos (mais recente de cada modelo + thinking)
docker compose run --rm app python -m scripts.compare_benchmarks
```

Para cada modelo, o benchmark descarrega os modelos da memória, mede o *cold start* em uma execução de aquecimento (com uma pergunta fora do conjunto, para não deixar o prompt de um caso em cache) e roda as perguntas de [benchmarks/questions.json](benchmarks/questions.json) `--runs` vezes. Os resultados ficam em `benchmarks/results/<data>_<modelo>.json`, com métricas por execução e a saída de cada resposta.

| Métrica | Origem |
|---|---|
| Cold start | `load_duration` do Ollama no aquecimento |
| Latência média, mediana e p95 | Tempo de parede de cada chamada |
| Tokens e tok/s do prompt | `prompt_eval_count` / `prompt_eval_duration` (taxa = soma dos tokens ÷ soma dos tempos) |
| Tokens e tok/s da saída | `eval_count` / `eval_duration`; inclui tokens de thinking, que não aparecem na resposta |
| JSON válido | Saída aprovada no parser e no schema |
| `answer_status` ok / `assets` ok | Comparação com `expected_status` / `expected_assets` de cada pergunta |

Para adicionar perguntas, inclua itens em `benchmarks/questions.json` (`expected_*` são opcionais).

## Testes

```bash
docker compose run --rm --no-deps app pytest            # unitários (padrão, sem modelo)
docker compose run --rm app pytest -m integration -v    # integração (Ollama + modelo)
```

Os testes de integração são ignorados automaticamente (`skip`) se o Ollama ou o modelo não estiverem disponíveis.

## Executar sem Docker (opcional)

Requer Python 3.12 e Ollama local com o modelo baixado (`ollama pull qwen2.5:7b`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
OLLAMA_BASE_URL=http://localhost:11434 python -m scripts.analyze "Compare BTC e ETH"
```

## Configuração (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:7b` | Modelo usado pelo `ChatOllama` e baixado pelo `ollama-pull`; pode ser sobrescrito pelo shell |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | URL do Ollama (no Compose é sempre o nome do serviço) |
| `OLLAMA_TEMPERATURE` | `0` | Temperatura de geração |
| `OLLAMA_REQUEST_TIMEOUT` | `300` | Timeout por requisição (s) |
| `OLLAMA_REASONING` | vazio | Thinking em modelos que suportam (`gemma4`): vazio = padrão do modelo, `false` = desliga, `true` = liga |
| `OLLAMA_HOST_PORT` | `11434` | Porta do Ollama publicada no host |

## Saída

A saída tem duas partes: o **plano de análise** (para as próximas etapas executarem) e uma **resposta mínima** ao usuário.

Pergunta conceitual — respondida com o conhecimento do modelo:

```json
{
  "intent": "general_information",
  "answer_status": "answered",
  "assets": ["ETH"],
  "requested_metrics": [],
  "period": null,
  "investigation_steps": [],
  "missing_information": [],
  "assumptions": [],
  "answer": "Staking de ETH é o processo de depositar ETH para validar transações e criar novos blocos, recebendo recompensas em troca. ...",
  "confidence": 0.9
}
```

Pergunta que depende de dados de mercado — o agente diz que não tem os dados e entrega o plano:

```json
{
  "intent": "comparison",
  "answer_status": "requires_market_data",
  "assets": ["BTC", "ETH"],
  "requested_metrics": ["price_history", "return_pct", "volatility", "max_drawdown"],
  "period": "últimos 30 dias",
  "investigation_steps": ["Obter price_history diário de BTC e ETH em USD nos últimos 30 dias", "..."],
  "missing_information": [],
  "assumptions": ["comparação por retorno e risco, pois o critério não foi informado", "cotação em USD", "preços de fechamento diários"],
  "answer": "Nesta fase não tenho dados de mercado atualizados e respondo apenas com o conhecimento do modelo, então não consigo comparar o desempenho e o risco de BTC e ETH nos últimos 30 dias. ...",
  "confidence": 0.8
}
```

- `answer_status`: `answered` (conceito respondido), `requires_market_data` (depende de dados de mercado) ou `needs_clarification` (pergunta ambígua).
- `answer`: resposta de 2 a 4 frases. **Nunca contém valores de mercado** (ver abaixo).
- `requested_metrics`: dados de mercado que uma etapa futura buscará em APIs (vocabulário sugerido em `METRIC_VOCABULARY`).
- `missing_information`: apenas o que o usuário não informou e nenhuma API resolve.
- `investigation_steps`: vazio só quando `answered`; se o modelo omitir as etapas de uma pergunta de dados, a etapa de coleta é derivada de `requested_metrics` (registrado em `assumptions`).
- `intent` ∈ `price_analysis`, `comparison`, `trend_analysis`, `risk_assessment`, `fundamental_analysis`, `general_information`, `other`. `confidence` mede a clareza da pergunta (0–1), não uma previsão.

### Por que a resposta não traz valores de mercado

O modelo só conhece o que aprendeu até o fim do treinamento. Preço, volume, TVL e rendimento mudam diariamente: qualquer número que ele escrevesse estaria desatualizado (sem data) ou seria alucinado, e não há fonte para conferir nesta etapa. Por isso:

1. O prompt proíbe valores de mercado e manda dizer explicitamente que não há dados atualizados.
2. O schema aplica uma trava determinística: frases de `answer` com valor monetário (`US$ 100`, `2 bilhões de dólares`) ou percentual (`4%`) são removidas e substituídas por um aviso padrão; o ocorrido fica em `assumptions`. Fatos de protocolo (ex.: "21 milhões de BTC") são permitidos.

A trava é heurística: pode remover termos como "ataque de 51%" e não detecta valores sem moeda (ex.: "65 mil").

## Tratamento de erros

| Situação | Erro |
|---|---|
| Modelo responde algo que não é JSON | `AnalysisOutputError` (via `OutputParserException`) |
| JSON fora do schema (campo ausente, intent/answer_status inválido, confiança fora de 0–1, plano vazio sem métricas) | `AnalysisOutputError` com `raw_output` |
| Ollama inacessível / modelo inexistente | `LLMUnavailableError` |

No `batch`, falhas são isoladas por item: o lote retorna `AnalysisError` na posição que falhou.

## Fora do escopo desta etapa

Integrações CoinPaprika/DeFiLlama/CoinGecko, memória persistente, RAG, ferramentas e planejamento autônomo. Ver [docs/architecture.md](docs/architecture.md) para a evolução planejada.
