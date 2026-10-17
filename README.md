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

```json
{
  "intent": "comparison",
  "assets": ["BTC", "ETH"],
  "requested_metrics": ["rendimento", "volatilidade"],
  "period": "últimos 30 dias",
  "investigation_steps": ["calcular o rendimento médio diário", "..."],
  "missing_information": [],
  "assumptions": ["moeda de cotação USD"],
  "confidence": 1.0
}
```

`intent` ∈ `price_analysis`, `comparison`, `trend_analysis`, `risk_assessment`, `fundamental_analysis`, `general_information`, `other`. `confidence` mede a clareza da pergunta (0–1), não uma previsão.

## Tratamento de erros

| Situação | Erro |
|---|---|
| Modelo responde algo que não é JSON | `AnalysisOutputError` (via `OutputParserException`) |
| JSON fora do schema (campo ausente, intent inválida, confiança fora de 0–1) | `AnalysisOutputError` com `raw_output` |
| Ollama inacessível / modelo inexistente | `LLMUnavailableError` |

No `batch`, falhas são isoladas por item: o lote retorna `AnalysisError` na posição que falhou.

## Fora do escopo desta etapa

Integrações CoinPaprika/DeFiLlama/CoinGecko, memória persistente, RAG, ferramentas e planejamento autônomo. Ver [docs/architecture.md](docs/architecture.md) para a evolução planejada.
