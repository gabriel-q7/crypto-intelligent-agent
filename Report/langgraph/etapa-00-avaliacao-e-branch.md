# Etapa 0 — Avaliação do repositório-base e branch

Projeto: **Crypto Intelligence Agent — evolução LangGraph** (projeto novo; usa como base o código do projeto
anterior, Fases 1 a 5, documentado em [Report/](../README.md)).

| | |
|---|---|
| **Período** | 2026-11-23 a 2026-11-23 |
| **Status** | Concluída (aguarda aprovação de decisões para a Etapa 1) |
| **Ambiente** | Manjaro Linux 6.12, Docker Compose, 12 núcleos de CPU, sem GPU; Ollama `gemma4:e2b` (`OLLAMA_REASONING=false`, `num_ctx` 8192), embeddings `nomic-embed-text` |
| **Versões** | Python 3.12.16 (imagem); langchain 1.4.9, langchain-core 1.7.6, langchain-ollama 1.1.0, langgraph 1.2.20, langgraph-prebuilt 1.1.1, langgraph-checkpoint 4.3.1, langsmith 0.14.17, mem0ai 2.2.1, chromadb 1.5.9 |

## 0. Ponto de partida (Git)

| Item | Valor |
|---|---|
| Commit-base (fim do projeto anterior) | `0f9e5a6380addf8b6c3c91b1a0cea0396e3e3330` ("add video link", 2026-11-13 22:50 -0300) |
| Branch de origem | `main` (igual a `origin/main`, árvore limpa) |
| Branch deste projeto | `feature/langgraph-stategraph-agent` |
| Baseline de testes | `docker compose run --rm --no-deps app pytest -q` → 401 passed, 25 deselected (31 s) |

O código do projeto anterior permanece intacto e serve de baseline: `app/integrated/` (agente integrado, Fase 5) e
`app/legacy/` (`AgentExecutor`, Fase 2).

## 1. Objetivo

Trocar o laço implícito do agente integrado por um `StateGraph` explícito (nós, arestas, roteamento condicional),
com estado persistente por `thread_id` (checkpointer SQLite), gestão explícita do histórico e uma arquitetura de
raciocínio alternativa (Reflexion) comparada ao ReAct no mesmo dataset.

## 2. Avaliação do repositório-base

### 2.1 Arquitetura atual

- **Fase 2 (legado):** `initialize_agent` + `ZERO_SHOT_REACT_DESCRIPTION` (`AgentExecutor`, `langchain-classic`) em
  [app/legacy/react_agent.py](../app/legacy/react_agent.py); `RouterChain` legado em `app/legacy/router_chain.py`.
- **Fase 5 (baseline principal):** `langchain.agents.create_agent` em [app/integrated/agent.py](../app/integrated/agent.py).
  `create_agent` já compila um grafo LangGraph, mas **pré-pronto e opaco**: o laço modelo ↔ ferramentas fica dentro
  dele; prefetch, verificação, correção única e resposta cautelosa ficam em Python imperativo em
  [app/integrated/service.py](../app/integrated/service.py).
- O enunciado deste projeto supõe um `AgentExecutor` como agente anterior; no repositório ele só existe no legado da
  Fase 2. A Fase 5 já migrou para `create_agent` (decisão de 2026-11-05).

### 2.2 Fluxo atual de um turno (Fase 5)

1. trava por sessão; grava a pergunta no SQLite de conversas;
2. `SessionHistoryAdapter` monta resumo + memórias Mem0 + janela de 10 mensagens (só leitura);
3. prefetch por regra de código em comparação entre redes;
4. `create_agent` (tool calling nativo do Ollama) escolhe e executa ferramentas;
5. `VerificationService` (NLI por afirmação + coerência lógica) contra as observações;
6. no máximo 1 correção com feedback estruturado; se falhar, `cautious_answer`;
7. grava resposta e análise; agenda manutenção (resumo, Mem0).

### 2.3 Componentes reutilizáveis

| Componente | Local |
|---|---|
| 9 ferramentas estruturadas (API ×6, SQL, RAG, cálculo) + categorias | `app/integrated/tools.py` |
| Prompt do sistema, `trajectory`, `prefetch_messages` | `app/integrated/agent.py` |
| Heurísticas de falha (recusa, comparação sem RAG, histórico sem `get_price_history`) | `app/integrated/service.py` |
| Verificação (claims, evidência, NLI, lógica, política) | `app/verification/` |
| Histórico + Mem0 + resumo | `app/integrated/history.py`, `app/memory/`, `app/persistence/sqlite_store.py` |
| Text-to-Query + `sql_guard` | `app/market_data/` |
| Retrieval Agent + Query Rewriting | `app/retrieval/` |
| Avaliação (20 exemplos, avaliadores, LangSmith/local) | `app/evaluation/`, `evaluation/phase5_dataset.json` |

### 2.4 Persistência atual

| Armazenamento | Responsabilidade |
|---|---|
| `data/conversations.sqlite3` | Mensagens, resumos, análises (fonte da verdade da conversa) |
| `data/memory_chroma` + `data/mem0_history.sqlite3` | Mem0 (episódica e semântica) |
| `data/chroma` | Base de conhecimento (RAG) |
| `data/market.sqlite3` | Snapshots de mercado |

A Fase 5 recusou de propósito o checkpointer do LangGraph para não criar uma segunda persistência da conversa
(docstring de `app/integrated/history.py`). Este projeto introduz o checkpointer; a divisão de papéis está na seção 4.

### 2.5 Limitações atuais

- Laço de raciocínio, verificação e correção não aparecem como transições de grafo; difícil de testar por etapa e
  de inspecionar no LangSmith além de spans `traceable`.
- Sem estado de execução recuperável: tool calls e observações de turnos anteriores se perdem (só o texto fica).
- Correção é um segundo `invoke` completo do agente, não um nó.
- Janela de histórico fixa por quantidade de mensagens, sem orçamento de tokens.
- Modelo de 2B ignora parte do roteamento; daí as regras de prefetch e de correção.

### 2.6 Pontos de integração propostos

- Novo pacote isolado (`app/graph/`) com `state`, `nodes`, `routing`, `builder`, `checkpointer`, `history`,
  `reflexion`, `service`, reaproveitando ferramentas, prompt, verificação e heurísticas existentes.
- `AppContainer` constrói os grafos uma vez (compilados com o checkpointer) e os reaproveita entre requisições.
- Seleção de arquitetura no endpoint do agente, padrão = comportamento atual.
- Avaliação: `app/evaluation/` ganha a arquitetura como parâmetro, sobre o mesmo dataset de 20 exemplos.

### 2.7 Riscos de compatibilidade

- `langgraph-checkpoint-sqlite` não está instalado; a versão 3.1.3 exige `langgraph-checkpoint>=4.3.1,<5`
  (instalado: 4.3.1) e traz `aiosqlite` e `sqlite-vec`.
- `SqliteSaver` com FastAPI síncrono (threadpool): conexão com `check_same_thread=False` e escrita serializada.
- Tool calling do `gemma4:e2b` foi validado na Fase 5, mas precisa ser revalidado no laço `ToolNode` (várias
  chamadas na mesma mensagem, retorno ao nó do agente).
- Duplicação de mensagens entre checkpoint e SQLite de conversas (seção 4).
- Latência em CPU: a integração completa da Fase 5 levou cerca de 35 min; avaliação de duas arquiteturas × 20
  exemplos será longa.

## 3. Decisões técnicas

| Decisão | Alternativas consideradas | Motivo |
|---|---|---|
| Branch a partir de `0f9e5a6` (`main` = `origin/main`) | — | Último commit do projeto-base, árvore limpa |
| Projeto novo com documentação própria (`Report/langgraph/`), separado das Fases 1 a 5 | Continuar como "Fase 6" do diário anterior | Pedido do usuário (2026-11-23): os requisitos LangGraph formam um projeto novo que usa o código atual como base; o relatório das Fases 1 a 5 fica fechado |

| Checkpointer com papel restrito (aprovado em 2026-11-23): SQLite de conversas segue fonte oficial da conversa (API, resumos, Mem0); checkpoint com `thread_id = session_id` guarda só o estado de execução do grafo (tool calls, observações, evidência, verificação), recortado antes do LLM e apagado no `DELETE` da sessão | Sem checkpointer (decisão do projeto-base); checkpoint como fonte única da conversa | Atende ao requisito de estado recuperável sem criar segunda fonte da verdade das mensagens; reverte de forma documentada a recusa da Fase 5 |
| Relatório final (aprovado em 2026-11-23): capítulos em `Report/langgraph/relatorio-final/`, gerado em `docs/langgraph-final-report.md` + PDF por variante do `generate-report.sh`, em português | `reports/langgraph-evolution-final-report.md` (sugestão do enunciado) | Segue a convenção do repositório (`Report/` → script → `docs/`) sem tocar no relatório das Fases 1 a 5 |

## 4. Pendências para a Etapa 1

Nenhuma: decisões aprovadas.
