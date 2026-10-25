# Arquitetura — Crypto Intelligence Agent

## 1. Anatomia do agente

| Bloco | Papel | Fase 1 | Fase 2 |
|---|---|---|---|
| **Percepção** | Receber e normalizar a entrada | `CryptoQuestion` (Pydantic) | + validação dos argumentos das ferramentas (`AssetInput`) e extração de ativos |
| **Raciocínio** | Interpretar e decidir | `ChatPromptTemplate` + `ChatOllama` → plano | Agente ReAct (escolhe ferramentas) e roteador (escolhe a chain), ambos com o prompt PTCF |
| **Ação** | Agir no mundo | Não havia | Ferramentas CoinPaprika (dados reais) e busca semântica FAISS |
| **Memória** | Estado entre interações | Não havia | Memória de curto prazo limitada (turnos e caracteres) com marcação de dados históricos |

Modelo padrão: `gemma4:e2b` com thinking desligado (`OLLAMA_MODEL`, `OLLAMA_REASONING`), configurável; `qwen2.5:7b` continua disponível. Escolha justificada pelo benchmark da Fase 1 (`docs/stage-1-evidence.md`, seção 9).

## 2. Fase 1 — chain estruturada (preservada)

```
CryptoQuestion → build_prompt_input → ANALYSIS_PROMPT → ChatOllama (JSON) → JsonOutputParser → CryptoAnalysisRequest
```

Plano de análise + resposta mínima, sem dados externos. Continua disponível em `POST /analyze`, `/analyze/batch` e `/analyze/stream`, com os mesmos testes (`tests/unit/test_chains.py`, `test_schemas.py`, ...). A Fase 2 não alterou suas interfaces; só acrescentou `json_mode` (padrão `True`) e `num_ctx` à fábrica do LLM.

## 3. Fase 2 — visão geral

**Forma de execução:** aplicação HTTP única (FastAPI, `uvicorn app.main:app`) rodando no Docker Compose ao lado do Ollama. Decisão do usuário em 2026-10-20: substituir os scripts avulsos (`scripts/*.py`) por um app Python padrão. As dependências compartilhadas (LLM, cliente CoinPaprika, índice FAISS, sessões de memória) são criadas uma vez no ciclo de vida da aplicação (`AppContainer`). Os endpoints são síncronos e rodam no thread pool, porque as chamadas ao Ollama e à CoinPaprika são bloqueantes.

```
                          ┌────────────── Agente ReAct clássico (app/agents, app/legacy) ─────────────┐
pergunta ─┬─ POST /agent ►│ PTCF + few-shot → ZERO_SHOT_REACT_DESCRIPTION → ferramentas → Final Answer │──┐
          │               └───────────────────────────────────────────────────────────────────────────┘  │
          │               ┌────────────── Chains roteadas (app/chains) ───────────────────────────────┐  │
          └─ POST /route ─►│ classificar (LLM JSON | RouterChain legado | palavras-chave) → RunnableBranch│  │
                          │   market_analysis   : identificar → buscar → validar → analisar (sequencial)│  │
                          │   asset_comparison  : identificar → [ativos ∥ global] → validar → comparar  │  │
                          │   knowledge_retrieval: FAISS → resposta baseada nos trechos                 │  │
                          │   defi_analysis     : indisponível nesta fase (DeFiLlama)                   │  │
                          └───────────────────────────────────────────────────────────────────────────┘  │
                                                                                                         ▼
                          grounding (app/services/grounding.py): achados, fontes e horários a partir das
                          observações; valores sem correspondência nos dados são removidos → CryptoReport
```

### Mapa de módulos

| Pasta | Conteúdo |
|---|---|
| `app/tools/coinpaprika.py` | Cliente HTTP: timeouts, retries, erros tipados, modelos Pydantic com `source` (endpoint, `source_updated_at`, `retrieved_at`) |
| `app/tools/market_tools.py` | Ferramentas de entrada única para o ReAct; mesmas observações usadas pelas chains |
| `app/tools/knowledge_tool.py` | Ferramenta de busca semântica |
| `app/knowledge/store.py` | Corpus → chunks → embeddings (Ollama) → FAISS persistido + manifesto |
| `app/legacy/` | **APIs legadas isoladas**: `initialize_agent` / `ZERO_SHOT_REACT_DESCRIPTION` e `LLMRouterChain` |
| `app/agents/runner.py` | Execução do agente, montagem do `CryptoReport`, memória |
| `app/chains/market_chains.py` | Chains sequencial, paralela, conhecimento e DeFi (LCEL) |
| `app/chains/routing.py` | Classificação moderna + `RunnableBranch` |
| `app/memory/conversation.py` | Memória limitada |
| `app/prompts/ptcf.py` | Prompt PTCF reutilizável e exemplos few-shot |
| `app/observability/` | Callback de rastreamento e configuração de logging (com redação de segredos) |
| `app/schemas/report.py` | `CryptoReport` (saída validada da Fase 2) |
| `app/main.py`, `app/api/` | Aplicação FastAPI: rotas, modelos de requisição, contêiner de dependências (LLM, cliente, sessões de memória) |
| `app/agents/factory.py` | Fábricas do agente, do roteador e da busca de conhecimento |
| `app/services/benchmark_runner.py` | Benchmark de modelos (manutenção; `python -m app.services.benchmark_runner`) |

## 4. Arquitetura híbrida: LangChain clássico + LCEL

O enunciado exige APIs legadas (`initialize_agent`, `ZERO_SHOT_REACT_DESCRIPTION`, `RouterChain`). Elas foram **isoladas** em `app/legacy/` e consumidas por interfaces modernas:

- `app/legacy/react_agent.py` só constrói o `AgentExecutor`. O `CryptoAgent` (moderno) cuida de memória, rastreamento e da montagem determinística da saída.
- `app/legacy/router_chain.py` só **classifica**. O despacho usa os mesmos handlers LCEL do roteador moderno. `MultiPromptChain` não foi usado porque exige destinos `LLMChain`.

O resto (chains, roteamento padrão, conhecimento) é LCEL: `RunnableLambda`, `RunnableParallel`, `RunnableBranch`, `batch`.

Trocar o legado pela alternativa moderna (ver `docs/orchestrator-comparison.md`) exige mudar apenas `app/legacy/`.

### Compatibilidade de dependências

| Pacote | Versão | Motivo |
|---|---|---|
| `langchain-core` | 1.6.12 (inalterado) | Base da Fase 1 |
| `langchain-ollama` | 1.1.0 (inalterado) | `ChatOllama` e `OllamaEmbeddings` |
| `langchain-classic` | 1.0.8 | No LangChain 1.x as APIs legadas saíram do pacote `langchain` e foram para cá |
| `langchain-community` | 0.4.2 | Vector store FAISS. **Em descontinuação** (aviso na importação); não há pacote dedicado `langchain-faiss`. A dependência fica isolada em `app/knowledge/store.py` |
| `langchain-text-splitters` | 1.1.3 | Divisão do corpus |
| `faiss-cpu` | 1.15.1 | Índice vetorial local |
| `httpx` | 0.28.1 | Cliente da CoinPaprika (já vinha como dependência transitiva; agora fixado) |

Verificação: instalação limpa em `python:3.12-slim` com as versões da Fase 1 fixadas. Todos os imports legados funcionam e os 95 testes da Fase 1 continuam passando.

## 5. Memória

`BoundedConversationMemory` (`app/memory/conversation.py`):

- **Limite de turnos:** `MEMORY_MAX_TURNS` (padrão 4), com `deque(maxlen)`.
- **Limite de caracteres no prompt:** `MEMORY_MAX_CHARS` (padrão 3000). Os turnos mais recentes têm prioridade, e cada resposta é truncada em 500 caracteres.
- **Dados históricos:** turnos com dados de mercado guardam o horário da consulta e entram no prompt com um aviso explícito ("valores HISTÓRICOS... consulte as ferramentas novamente"). O prompt PTCF reforça a regra.
- **Por que não `ConversationBufferWindowMemory`:** o `ZeroShotAgent` não tem slot de memória (seria preciso trocar o tipo de agente), e a janela por turnos não limita tamanho. O histórico entra como variável `{history}` do prompt.

A memória de longo prazo continua conceitual (seção 8).

## 6. Base de conhecimento (FAISS)

| Aspecto | Decisão |
|---|---|
| Corpus | `knowledge/corpus/*.md` — 7 documentos curtos sobre blockchain, Bitcoin, Ethereum/staking, DeFi/TVL, stablecoins, métricas e riscos. **Sem preços** (há teste que garante) |
| Chunking | `RecursiveCharacterTextSplitter`, 800 caracteres, sobreposição de 120, separando por seção `##` primeiro |
| Embeddings | `nomic-embed-text` via Ollama (274 MB, local, reaproveita o container) |
| Índice | FAISS (`IndexFlatL2` via `langchain-community`), salvo em `data/faiss_index` (diretório montado no Compose) |
| Reprodutibilidade | `manifest.json` registra modelo de embedding, parâmetros e hash de cada arquivo. Modelo diferente → erro pedindo reindexação; corpus alterado → aviso |
| Segurança | O docstore do FAISS é serializado com pickle (`allow_dangerous_deserialization=True`): só índices gerados localmente são carregados |
| Separação | Conhecimento ≠ mercado: a ferramenta e a rota de conhecimento nunca trazem preços, e valores de mercado são removidos das respostas dessa rota |

## 7. Classificação do agente na Fase 2

- **Agente ReAct:** reativo com ferramentas. Ciclo observar → agir limitado por `AGENT_MAX_ITERATIONS` e `AGENT_MAX_EXECUTION_TIME`.
- **Chains roteadas:** fluxos determinísticos escolhidos por um classificador. Planejamento fixo por rota, sem autonomia.
- **Planejamento autônomo:** fora do escopo. O plano da Fase 1 (`investigation_steps`) ainda não é executado automaticamente.

## 8. Evolução prevista

1. **DeFiLlama / CoinGecko:** novas ferramentas no formato de observação existente; a rota `defi_analysis` passa a ter handler real.
2. **Execução do plano da Fase 1:** `requested_metrics` → ferramentas (planejamento hierárquico: objetivo → etapas → chamadas).
3. **Memória de longo prazo:** preferências e análises passadas em vector store separado do corpus conceitual.
4. **Migração do legado:** `create_agent`/LangGraph com tool calling nativo, sem o parser de texto do ReAct.
