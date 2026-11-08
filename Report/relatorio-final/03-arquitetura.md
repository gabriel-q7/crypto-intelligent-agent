# Arquitetura geral

Este capítulo descreve o sistema como ele está ao fim da Fase 5. Os capítulos das fases mostram como ele chegou até aqui.

## Visão de componentes

```
                    Streamlit (ui/)  ── HTTP ──┐
                                               ▼
 ┌──────────────────────────── FastAPI (app/main.py, AppContainer) ─────────────────────────────┐
 │ Fase 1  /analyze[/batch|/stream]  → chain LCEL → plano JSON validado                         │
 │ Fase 2  /agent (ReAct legado)  /route (chains roteadas)  /knowledge/*                        │
 │ Fase 3  /conversations, /conversations/{id}/messages  → intenção → contexto → chains         │
 │ Fase 4  /market-data/*  (coleta, cobertura, Text-to-Query)   /knowledge/retrieve             │
 │ Fase 5  /conversations/{id}/agent → agente integrado → verificação → (1 correção)            │
 └───────────────┬──────────────────┬──────────────────┬──────────────────┬─────────────────────┘
                 ▼                  ▼                  ▼                  ▼
          Ollama (CPU)       CoinPaprika API     SQLite               Chroma (data/chroma)
          gemma4:e2b         (dados ao vivo)     conversations.sqlite3  crypto_knowledge (corpus)
          nomic-embed-text                       market.sqlite3         memórias Mem0 por sessão
                                                                         ▲
                                            LangSmith (traces e experimentos, opcional)
```

Os serviços do Docker Compose são `ollama`, `ollama-pull` (baixa os modelos uma vez), `app` (API) e `ui` (Streamlit). Os dados persistem em `./data` no host.

## Anatomia do agente

| Bloco | Fase 1 | Fase 2 | Fase 3 | Fase 4 | Fase 5 |
|-----------|-----------------|-------------|-------------|-------------|-------------|
| **Percepção** | `CryptoQuestion` (Pydantic) | Validação dos argumentos e extração de ativos | Intenção e entidades no contexto da conversa | Query Rewriting no vocabulário dos documentos | Argumentos tipados das ferramentas (tool calling) |
| **Raciocínio** | Prompt + `ChatOllama` → plano | ReAct e roteador com prompt PTCF | Resolução de continuação, correção e resultado anterior | O ReAct escolhe também banco, retrieval e cálculo; LLM gera SQL validado | `create_agent` com tool calling nativo; verificação e correção |
| **Ação** | — | CoinPaprika e busca semântica | Mesmas ferramentas, com contexto | SQL somente leitura, Retrieval Agent, cálculo | As mesmas ferramentas, combinadas numa execução |
| **Memória** | — | Janela limitada em processo | SQLite (janela + resumo) e Mem0 (episódica e semântica) | Sem mudança | Histórico persistente da Fase 3 lido por um adaptador |

## Fluxo de um turno do agente integrado

```
POST /conversations/{id}/agent
 └─ trava por sessão → grava a pergunta (SQLite)
    → SessionHistoryAdapter: resumo + memórias Mem0 + janela (só leitura)
    → create_agent: LLM ⇄ ferramentas (API, SQL, RAG, cálculo); trajetória registrada
    → VerificationService: afirmações → evidência → checagem numérica / NLI → coerência lógica → status
    → [se necessário] 1 correção com as próprias observações → nova verificação → resposta cautelosa se falhar
    → grava a resposta, a análise e o episódio → manutenção em segundo plano (resumo, título, extração)
```

## Armazenamento

| Repositório | Conteúdo | Papel |
|------------------------------------|--------------------------------|--------------------------------|
| `data/conversations.sqlite3` | Sessões, mensagens, resumos, análises, contexto ativo | Fonte da verdade da conversa (Fase 3) |
| `data/market.sqlite3` | Snapshots reais da CoinPaprika | Dado de mercado histórico, consultado só por SQL validado (Fase 4) |
| Chroma `crypto_knowledge` | Trechos do corpus local com metadados | Base de conhecimento (Fases 2 e 4) |
| Chroma (Mem0) | Memórias episódicas e semânticas por sessão | Memória de longo prazo (Fase 3) |

Os dois bancos SQLite são separados por decisão da Fase 4: apagar ou recriar um não afeta o outro.

## Pilha e versões finais

| Pacote | Versão | Uso |
|----------------------------|---------|-----------------------------------------------------|
| `langchain-core` | 1.7.6 | Runnables, prompts, mensagens (era 1.6.12 até a Fase 4) |
| `langchain` | 1.4.9 | `create_agent` (Fase 5) |
| `langchain-ollama` | 1.1.0 | `ChatOllama` e `OllamaEmbeddings` |
| `langchain-classic` | 1.0.8 | Só as APIs legadas exigidas na Fase 2, isoladas em `app/legacy/` |
| `langchain-chroma` / `chromadb` | 1.1.0 / 1.5.9 | Base vetorial |
| `mem0ai` | 2.2.1 | Memória de longo prazo |
| `sqlglot` | 30.27.0 | Validação do SQL gerado pelo LLM |
| `fastapi` / `uvicorn` | 0.150.0 / 0.54.0 | API HTTP |
| `pydantic` | 2.14.1 | Schemas e validação |
| `langsmith` | 0.14.17 | Rastreamento e avaliação (dependência do `langchain-core`) |
| `streamlit` | 1.65.0 | Interface |

## Interface

A interface Streamlit (`ui/`) só fala com a API por HTTP (`ui/backend.py`). Nenhuma lógica de agente, memória ou verificação roda nela. A barra lateral lista as conversas com o título gerado pelo modelo após o primeiro turno. Cada resposta mostra o status da verificação, as afirmações sustentadas, neutras e contraditas, as ferramentas usadas e as fontes com horário.
