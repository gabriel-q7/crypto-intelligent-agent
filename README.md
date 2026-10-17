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
| `OLLAMA_MODEL` | `qwen2.5:7b` | Modelo usado pelo `ChatOllama` e baixado pelo `ollama-pull` |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | URL do Ollama (no Compose é sempre o nome do serviço) |
| `OLLAMA_TEMPERATURE` | `0` | Temperatura de geração |
| `OLLAMA_REQUEST_TIMEOUT` | `180` | Timeout por requisição (s) |
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
