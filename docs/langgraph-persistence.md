# Persistência do grafo: checkpointer SQLite e threads

Projeto **Crypto Intelligence Agent — evolução LangGraph** (Etapa 3). Arquitetura geral em
[langgraph-architecture.md](langgraph-architecture.md).

## 1. Divisão de responsabilidades

| Armazenamento | Arquivo | Responsabilidade | Quem grava |
|---|---|---|---|
| Conversas (projeto-base) | `data/conversations.sqlite3` | **Fonte da verdade da conversa**: mensagens exibidas, resumos, análises | Serviço da aplicação, uma vez por turno |
| Mem0 (projeto-base) | `data/memory_chroma`, `data/mem0_history.sqlite3` | Memória episódica e semântica | Manutenção da Fase 3 |
| Base de conhecimento (projeto-base) | `data/chroma` | RAG | Ingestão |
| Snapshots (projeto-base) | `data/market.sqlite3` | Dados de mercado armazenados | Ingestão |
| **Checkpoints (novo)** | `data/langgraph_checkpoints.sqlite3` (`GRAPH_CHECKPOINT_DB_PATH`) | **Estado de execução do grafo por thread**: mensagens com tool calls e observações, evidência, verificação, correção, nós pendentes | O LangGraph, a cada super-step |

Decisão aprovada em 2026-11-23. O checkpoint guarda o que o SQLite de conversas não guarda (chamadas de ferramenta
e observações de turnos anteriores, estado intermediário de um turno interrompido). Ele não é a fonte do histórico
exibido pela API nem dos resumos e do Mem0. A cópia de Human/AI no checkpoint é estado de execução, não um segundo
histórico consultável; ao apagar a sessão, as threads são apagadas também (`delete_session_threads`).

## 2. Implementação

| Peça | Código |
|---|---|
| `open_checkpointer(path)` → `SqliteSaver` com `check_same_thread=False` (o saver serializa o acesso com `threading.Lock`) e `setup()` | [app/graph/checkpointer.py](../app/graph/checkpointer.py) |
| `thread_id_for(session_id, architecture)`: ReAct usa o próprio id da sessão; outras arquiteturas, `<sessão>:<arquitetura>` | idem |
| `pending_nodes`, `delete_session_threads`, `thread_config` | idem |
| `build_react_graph(..., checkpointer=saver)`: compila o grafo com o checkpointer | [app/graph/builder.py](../app/graph/builder.py) |
| `run_react(graph, pergunta, config=graph_config(..., thread_id=...))`: com thread, o histórico vem do checkpoint; `history` em thread com estado → `ValueError` | [app/graph/run.py](../app/graph/run.py) |
| `resume_react(graph, config)`: retoma um turno interrompido (`invoke(None)`) | idem |
| `GraphSettings.checkpoint_db_path` | [app/config.py](../app/config.py) |

Dependências: `langgraph-checkpoint-sqlite==3.1.3` (exige `langgraph-checkpoint>=4.3.1,<5`). `langgraph-prebuilt`
e `langgraph-checkpoint` ficaram fixados em 1.1.1 e 4.3.1: sem o pin, o rebuild da imagem trouxe 1.1.3 e 4.3.2.

### Threads

- `thread_id = session_id` (`uuid4().hex`, gerado pelo SQLite de conversas): uma thread por conversa, sem
  reaproveitamento entre conversas.
- Mesma sessão em outra arquitetura (Reflexion, Etapa 5) → thread própria (`<sessão>:reflexion`), porque os
  esquemas de estado diferem.
- Thread vazia é criada no primeiro turno. Sessões anteriores a esta etapa não têm thread (ver seção 5).

### Estado por turno

`turn_input` dá `id` à pergunta (`turn_id`) e zera os campos do turno (evidência, verificação, tentativas de
correção, `llm_calls` com `Overwrite`). `messages` acumula entre turnos (redutor `add_messages`). Assim cada
turno tem sua própria correção única e métricas próprias, e o modelo vê os turnos anteriores.

### Recuperação de turno interrompido

O LangGraph grava um checkpoint a cada super-step. Se o turno cai no meio (ex.: Ollama fora do ar depois de a
ferramenta rodar), `pending_nodes` mostra onde parou (`("agent",)`) e `resume_react` continua dali: nós já
concluídos não rodam de novo, então a ferramenta não é chamada outra vez. Como o serviço da API trata uma
thread com nós pendentes ao receber nova mensagem é decisão da Etapa 7.

## 3. Testes

Offline ([tests/unit/test_graph_persistence.py](../tests/unit/test_graph_persistence.py), 8 testes):

| Roteiro do enunciado | Teste |
|---|---|
| 1–8: conversa, execução, checkpoint, nova requisição, estado recuperado, seguimento, continuação | `test_follow_up_resumes_from_checkpoint_in_a_separate_request` |
| Campos do turno zerados (cada turno com sua correção) | `test_turn_fields_are_reset_so_each_turn_gets_its_own_correction` |
| Histórico duplicado recusado | `test_history_argument_is_rejected_on_a_thread_with_state` |
| 9–10: reinício (nova conexão, novo grafo) | `test_checkpoint_survives_restart_new_connection_and_new_graph` |
| 9–10: reinício em processo novo | `test_checkpoint_is_readable_from_a_new_process` |
| 11–12: isolamento | `test_threads_are_isolated` |
| Threads por arquitetura e exclusão | `test_architectures_use_distinct_threads_and_delete_removes_them` |
| Turno interrompido retomado sem repetir a ferramenta | `test_interrupted_turn_resumes_without_repeating_the_tool` |

Real ([tests/integration/test_graph_persistence_live.py](../tests/integration/test_graph_persistence_live.py)), com
Ollama, ferramentas e verificador reais: 4 passed. Evidência:
[etapa-03-persistence-live.jsonl](evidence/langgraph/etapa-03-persistence-live.jsonl).

| Caso | Resultado |
|---|---|
| Turno 1 (thread A): preço do BTC | `get_market_data(BTC)`; 4 mensagens e 9 checkpoints na thread |
| Seguimento (thread A, nova requisição): "E o Ethereum? Qual dos dois teve a maior variação em 24h?" | Consultou só o ETH; usou o BTC do turno anterior (observação no checkpoint) |
| Isolamento (thread B): "Sobre qual criptomoeda eu perguntei antes?" | Nenhuma ferramenta; "Não foi possível identificar nenhuma criptomoeda mencionada…"; nenhuma mensagem de A em B |
| Reinício (processo Python novo, nova conexão, novo grafo): "Quais foram os dois ativos que eu perguntei?" | Thread com 10 mensagens recuperada; resposta cita Bitcoin e Ethereum |

## 4. Limitações e pontos abertos (insumos da Etapa 4)

1. **Evidência × contexto.** No seguimento, o modelo reaproveitou o preço do BTC do turno anterior, mas a
   verificação só usa as observações do turno atual: a afirmação saiu "não confirmada" e a resposta, cautelosa.
   Proposta para a Etapa 4: a evidência da verificação passa a ser **as observações que o modelo de fato viu**
   (as do turno + as de turnos anteriores que sobrarem após o recorte), com o horário de coleta original.
2. **Afirmações sobre a própria conversa** ("você perguntou sobre X") não têm evidência de ferramenta e saem
   "não confirmadas" (mesmo comportamento do projeto-base).
3. **Mensagens internas na thread.** O feedback da correção (`HumanMessage` "Revisão automática…") e a instrução
   da pré-consulta ficam no estado e seriam vistos nos turnos seguintes. A Etapa 4 deve filtrá-las do contexto.
4. **Crescimento.** Após 2 turnos, a thread A tinha 10 mensagens e 22 checkpoints; o segundo turno usou 8.200
   tokens de entrada em 3 chamadas. Sem recorte, o contexto cresce a cada turno (Etapa 4).
5. **Sessões antigas** (anteriores ao checkpointer) não têm thread; o serviço da Etapa 7 precisa iniciar a thread
   a partir do histórico do SQLite de conversas.
6. **Histórico de checkpoints** não é podado: cada super-step fica gravado. Aceitável no volume atual; só a
   exclusão da sessão remove.
