# Comparação de frameworks de memória: LangMem, Mem0 e Letta

Contexto do projeto:
- API FastAPI com orquestração LangChain/LCEL;
- modelo pequeno local (`gemma4:e2b` no Ollama, CPU);
- SQLite e Chroma exigidos pelo enunciado;
- diretriz do usuário de **não usar nada depreciado**.

Os números vêm de **spikes reais** (2026-10-28) com as versões do projeto fixadas; detalhes em [memory-decision.md](memory-decision.md). Para o Letta não houve execução: a avaliação se baseia na documentação oficial.

## Visão geral

| | LangMem | Mem0 | Letta (antigo MemGPT) |
|---|---|---|---|
| O que é | Biblioteca de utilitários de memória do ecossistema LangChain/LangGraph | Camada de memória como biblioteca (OSS) ou serviço gerenciado | Plataforma de agentes com estado; servidor próprio |
| Versão avaliada | 0.0.30 (2025-10-27) | 2.2.1 | Servidor Docker `letta/letta` (docs) |
| Ideia central | Extrair e consolidar memórias por LLM (tool calling) e resumir mensagens; persistir num `BaseStore` do LangGraph | `add()` extrai fatos por LLM e decide ADD/UPDATE/DELETE; `search()` vetorial com filtros por usuário, agente e execução | O próprio agente edita "blocos de memória" e consulta memória arquival e de recordação via ferramentas (abordagem MemGPT) |

## Critérios

| Critério | LangMem | Mem0 | Letta |
|---|---|---|---|
| **Memória curta** | `summarize_messages` + `RunningSummary` ✅ (testado: 22 s) | Não cobre (resumo da conversa fica com a aplicação) | Blocos de memória no contexto do agente |
| **Memória longa / semântica** | `create_memory_manager` (independe de store) ou `create_memory_store_manager` (exige `BaseStore`) | `add(infer=True)` + `search` ✅ | Memória arquival (vetorial) |
| **Persistência** | Store do LangGraph (Postgres/memória); Chroma exigiria adaptador | **Chroma nativo + histórico em SQLite** ✅ | Postgres + pgvector |
| **Sessões / isolamento** | Namespaces do store | `user_id` / `agent_id` / `run_id` ✅ (testado) | Por agente |
| **Integração com a nossa arquitetura LangChain** | Alta (mesmo ecossistema), mas traz `langgraph` e `trustcall` | Média: biblioteca independente, chamada pelo nosso código; usa clientes próprios de LLM e vetores | Baixa: substitui a orquestração (o laço do agente roda no servidor Letta) |
| **Execução local** | ✅ com ChatOllama | ✅ Ollama (LLM + embeddings) + Chroma | ✅ via `OLLAMA_BASE_URL`, mas a doc recomenda "modelo grande de fronteira" |
| **Complexidade operacional** | Baixa (biblioteca) | Baixa (biblioteca); +51 MB de dependências | Alta: serviço + Postgres; autenticação própria |
| **Depreciação / suporte** | ⚠️ `trustcall` usa import depreciado do LangGraph (removido na 2.0) | ✅ nenhum aviso | ❌ imagem Docker "não mais mantida nem suportada" |
| **Qualidade com `gemma4:e2b`** | Extração 10–26 s; 1 de 3 memórias inventada | Extração 68–89 s; guardou preço como fato e confirmações do assistente | Não testado (exige modelo forte para tool calling) |
| **Testes e observabilidade** | Funções puras, fáceis de isolar | Interface simples (cabe atrás de um protocolo); histórico de eventos auditável | Testes dependem do servidor; observabilidade da plataforma |
| **Manutenção** | Série 0.0.x, cerca de 1 ano sem release | Ativo, mas a API 2.x mudou (`filters=` obrigatório) | Produto mudou de foco (Letta Code / App Server) |

## Decisão e justificativa

**Mem0 na memória longa + código próprio na memória curta.**

1. **Diretriz de depreciação:** o Mem0 foi o único sem avisos. O LangMem carrega uma dependência com import depreciado. O servidor Docker do Letta não é mais suportado.
2. **Requisitos do enunciado:** o Mem0 já fala Chroma e SQLite. O LangMem exigiria um adaptador `BaseStore` para o Chroma; o Letta usa Postgres.
3. **Orquestração preservada:** o Mem0 é chamado como biblioteca, e o LangChain/LCEL continua orquestrando. O Letta assumiria o laço do agente.
4. **Isolamento por sessão** nativo (`run_id`), testado.

**Por que não usar o Mem0 também na memória curta:** ele não resume conversas. A janela e o resumo incremental ficaram em código próprio (LCEL + Pydantic), com o que precisa sobreviver ao resumo extraído de forma determinística dos metadados.

**Riscos aceitos e mitigados:** qualidade e latência da extração com modelo pequeno, tratadas com:
- extração em segundo plano;
- só falas do usuário;
- prompt próprio;
- filtro anti-valores de mercado;
- episódios sem LLM.

## O que justificaria rever a decisão

- **Modelo maior ou GPU:** a extração do LangMem ou do Mem0 fica mais confiável e rápida.
- **`trustcall` corrigido (ou LangMem sem ele):** o LangMem volta a ser candidato, em especial pelo `summarize_messages`.
- **Agente migrado para LangGraph** (pendência da Fase 2): o `Store` do LangGraph com o LangMem passa a ser o caminho nativo.
- **Memória entre usuários ou dispositivos:** Mem0 gerenciado ou uma plataforma como o Letta App Server (com avaliação de custo e privacidade).
