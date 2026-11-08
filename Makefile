# Atalhos para as ações do Crypto Intelligence Agent (tudo via Docker Compose).
# Uso: make <alvo> [VAR=valor]. Lista completa: make help

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE   ?= docker compose
RUN       := $(COMPOSE) run --rm
API_URL   ?= http://localhost:$(or $(APP_HOST_PORT),8000)
ARGS      ?=
MODELS    ?=
REASONING ?=

# Lê APP_HOST_PORT do .env, se existir, para montar a URL da API.
-include .env
export

.PHONY: help env build up down restart logs ps shell pull-models \
        ingest health check-api docs market-ingest market-coverage \
        test test-network test-integration test-all \
        benchmark benchmark-compare clean-data ui ui-logs eval-upload eval eval-local report

## ---------------------------------------------------------------- ambiente

help: ## Lista os alvos disponíveis
	@awk 'BEGIN {FS = ":.*## "} /^## -/ {t = $$0; sub(/^## -+ */, "", t); printf "\n\033[1m%s\033[0m\n", t} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Exemplos: make test ARGS=\"-k intent\" | make benchmark MODELS=\"gemma4:e2b qwen2.5:7b\""

env: ## Cria o .env a partir do .env.example (não sobrescreve)
	@test -f .env && echo ".env já existe (nada alterado)" || (cp .env.example .env && echo ".env criado")

build: ## Reconstrói a imagem da aplicação
	$(COMPOSE) build app

up: env ## Sobe Ollama + modelos + API + interface (API :8000/docs, UI :8501)
	$(COMPOSE) up -d --build app ui
	@echo "API: $(API_URL)/docs"
	@echo "UI:  http://localhost:$(or $(UI_HOST_PORT),8501)"

ui: ## Sobe (ou reconstrói) só a interface Streamlit
	$(COMPOSE) up -d --build ui

ui-logs: ## Acompanha os logs da interface
	$(COMPOSE) logs -f ui

down: ## Para e remove os containers (volumes e ./data são preservados)
	$(COMPOSE) down

restart: ## Reinicia só a API (sessões persistidas são retomadas)
	$(COMPOSE) restart app

logs: ## Acompanha os logs da API
	$(COMPOSE) logs -f app

ps: ## Estado dos containers
	$(COMPOSE) ps

shell: ## Abre um shell no container da aplicação
	$(RUN) app bash

pull-models: ## Baixa o modelo de chat e o de embeddings (OLLAMA_MODEL, EMBEDDING_MODEL)
	$(RUN) ollama-pull

## ---------------------------------------------------------------- operação (API em execução)

ingest: ## Cria/recria a coleção Chroma da base de conhecimento
	curl -sf -X POST $(API_URL)/knowledge/ingest | python3 -m json.tool

health: ## Saúde da API (Ollama, modelos, coleção, sessões)
	curl -sf $(API_URL)/health | python3 -m json.tool

check-api: ## Verificação real da CoinPaprika (consome cota da API)
	curl -sf $(API_URL)/health/coinpaprika | python3 -m json.tool

docs: ## Mostra a URL da documentação interativa (Swagger)
	@echo "$(API_URL)/docs"

market-ingest: ## Coleta snapshots reais da CoinPaprika para o banco SQLite (Fase 4)
	curl -sf -X POST $(API_URL)/market-data/ingest -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool

market-coverage: ## Cobertura do banco de snapshots (ativos, quantidade, janela de tempo)
	curl -sf $(API_URL)/market-data/coverage | python3 -m json.tool

## ---------------------------------------------------------------- testes

test: ## Testes offline (sem modelo, sem rede). Filtro: ARGS="-k nome"
	$(RUN) --no-deps app pytest $(ARGS)

test-network: ## Testes com a API real da CoinPaprika
	$(RUN) app pytest -m network -v $(ARGS)

test-integration: ## Integração com Ollama + Mem0 + CoinPaprika reais (~35 min em CPU)
	$(RUN) app pytest -m "integration or network" -v $(ARGS)

test-all: test test-integration ## Offline + integração

## ---------------------------------------------------------------- avaliação (Fase 5)

eval-upload: ## Cria/atualiza o dataset de avaliação no LangSmith (exige LANGSMITH_API_KEY)
	$(RUN) app python -m app.evaluation.runner --upload

eval: ## Experimento no LangSmith. Ex.: make eval PREFIX=fase5-final [IDS="api-01 rag-02"]
	$(RUN) -v $(CURDIR)/docs/evidence:/app/docs/evidence app python -m app.evaluation.runner --run --prefix $(or $(PREFIX),fase5) $(if $(IDS),--ids $(IDS))

eval-local: ## Mesma avaliação sem LangSmith (resultados em docs/evidence/phase-5/)
	$(RUN) -v $(CURDIR)/docs/evidence:/app/docs/evidence app python -m app.evaluation.runner --local --prefix $(or $(PREFIX),local) $(if $(IDS),--ids $(IDS))

## ---------------------------------------------------------------- relatório

report: ## Relatório técnico final (docs/final-report.md + PDF); confere os números contra as evidências
	./scripts/generate-report.sh

## ---------------------------------------------------------------- manutenção

benchmark: ## Benchmark de modelos. Ex.: MODELS="gemma4:e2b qwen2.5:7b" REASONING=off
	$(RUN) app python -m app.services.benchmark_runner $(if $(MODELS),--models $(MODELS)) $(if $(REASONING),--reasoning $(REASONING)) --pull $(ARGS)

benchmark-compare: ## Compara os resultados de benchmark salvos
	$(RUN) --no-deps app python -m app.services.benchmark_runner --compare

clean-data: ## APAGA ./data (conversas, memórias, snapshots, coleção Chroma). Exige CONFIRM=yes
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "Isto apaga conversas (SQLite), memórias (Mem0/Chroma), snapshots de mercado e a coleção de conhecimento em ./data."; \
		echo "Para confirmar: make clean-data CONFIRM=yes"; exit 1; \
	fi
	$(COMPOSE) stop app
	rm -rf data/conversations.sqlite3* data/memory_chroma data/mem0_history.sqlite3* data/chroma data/market.sqlite3*
	@echo "./data limpo. Rode 'make up', 'make ingest' e 'make market-ingest'."
