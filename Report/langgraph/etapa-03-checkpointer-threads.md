# Etapa 3 — Checkpointer persistente e threads

| | |
|---|---|
| **Período** | 2026-11-24 a 2026-11-24 |
| **Status** | Concluída |
| **Ambiente** | Docker Compose, CPU (12 núcleos, sem GPU); Ollama `gemma4:e2b` |
| **Versões** | langgraph 1.2.20, langgraph-checkpoint 4.3.1, **langgraph-checkpoint-sqlite 3.1.3** (novo; traz aiosqlite 0.22.1 e sqlite-vec 0.1.9), langgraph-prebuilt 1.1.1 |

## 1. Objetivo

Estado do grafo persistente por `thread_id`, retomável entre requisições e após reinício, com threads isoladas,
sem misturar responsabilidades com o SQLite de conversas, o Mem0, o Chroma e os snapshots.

## 2. Escopo

- **Dentro:** `SqliteSaver` em arquivo próprio, mapeamento sessão → thread, reset dos campos por turno, retomada
  de turno interrompido, exclusão das threads da sessão, testes offline e reais.
- **Fora (e por quê):** recorte do histórico e filtragem de mensagens internas (Etapa 4); uso na API, threads de
  sessões antigas e tratamento de thread pendente na nova mensagem (Etapa 7).

## 3. Solução implementada

Referência completa em [docs/langgraph-persistence.md](../../docs/langgraph-persistence.md).

| Arquivo | Mudança |
|---|---|
| [app/graph/checkpointer.py](../../app/graph/checkpointer.py) | `open_checkpointer`, `close_checkpointer`, `thread_id_for`, `thread_config`, `pending_nodes`, `delete_session_threads` |
| [app/graph/run.py](../../app/graph/run.py) | `resume_react`; recusa de `history` em thread com estado; `graph_run` comum |
| [app/config.py](../../app/config.py), `.env.example` | `GraphSettings`, `GRAPH_CHECKPOINT_DB_PATH=data/langgraph_checkpoints.sqlite3` |
| [Makefile](../../Makefile) | `clean-data` apaga também os checkpoints |
| `requirements.txt` | `langgraph-checkpoint-sqlite==3.1.3`; `langgraph-prebuilt==1.1.1` e `langgraph-checkpoint==4.3.1` fixados |

## 4. Decisões técnicas

| Decisão | Alternativas consideradas | Motivo |
|---|---|---|
| Checkpoint = estado de execução; SQLite de conversas segue fonte da verdade (aprovada em 2026-11-23) | Sem checkpointer (projeto-base); checkpoint como fonte única | Estado recuperável sem segundo histórico oficial |
| `SqliteSaver` síncrono, uma conexão com `check_same_thread=False` | `AsyncSqliteSaver`; conexão por requisição | A aplicação é FastAPI síncrona; o saver tem `threading.Lock` próprio |
| `thread_id = session_id`; outras arquiteturas com sufixo `:<arquitetura>` | Sempre com prefixo; mesma thread para todas | Preserva o id da conversa (pedido do enunciado) e evita que um grafo leia o estado de outro esquema |
| Campos do turno zerados na entrada (`turn_input`, `Overwrite` em `llm_calls`) | Acumular e filtrar por turno | Correção única e métricas por turno; checkpoint não cresce com métricas antigas |
| `run_react` recusa `history` em thread com estado | Aceitar e duplicar | Evita mensagens repetidas no estado |
| Fixar `langgraph-prebuilt==1.1.1` e `langgraph-checkpoint==4.3.1` | Aceitar 1.1.3 / 4.3.2 do rebuild | Ver problema 5.1 |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Rebuild trouxe versões novas de dependências transitivas

- **Sintoma:** depois de acrescentar `langgraph-checkpoint-sqlite` ao `requirements.txt`, a imagem passou a ter
  `langgraph-prebuilt` 1.1.3 e `langgraph-checkpoint` 4.3.2 (antes 1.1.1 e 4.3.1).
- **Causa:** as duas não estavam fixadas; com a camada do `pip install` invalidada, o pip resolveu as mais novas.
- **Correção:** fixadas nas versões validadas nas Etapas 1 e 2 (o tratamento de erro do `ToolNode` foi verificado
  em 1.1.1). `pip check` sem conflitos.

### 5.2 Seguimento com dado do turno anterior saiu "não confirmado"

- **Sintoma:** no teste real, "E o Ethereum? Qual dos dois teve a maior variação em 24h?" consultou só o ETH; a
  resposta citou o BTC do turno anterior com "(não confirmado pelas fontes consultadas)".
- **Causa:** o modelo viu a observação do BTC no checkpoint, mas a verificação usa só a evidência do turno.
  A correção única não reconsultou o BTC.
- **Situação:** registrado como insumo da Etapa 4 (evidência = observações que o modelo viu após o recorte).

## 6. Evidências e métricas

- Offline: `docker compose run --rm --no-deps -v "$PWD":/app app pytest -q` → **435 passed**, 34 deselected (8
  novos em [tests/unit/test_graph_persistence.py](../../tests/unit/test_graph_persistence.py)); sem avisos de
  desserialização.
- Real (2026-11-24 00:27–00:29 UTC): `pytest -m "integration or network" tests/integration/test_graph_persistence_live.py`
  → **4 passed** em 124,79 s. Log: [etapa-03-persistence-live.log](../../docs/evidence/langgraph/etapa-03-persistence-live.log);
  JSONL: [etapa-03-persistence-live.jsonl](../../docs/evidence/langgraph/etapa-03-persistence-live.jsonl).

| Caso | Ferramentas | Chamadas ao modelo | Tokens de entrada | Mensagens na thread | Checkpoints | Total (s) |
|---|---|---|---|---|---|---|
| Turno 1 (A): preço do BTC | `get_market_data(BTC)` | 2 | 4.126 | 4 | 9 | 23,6 |
| Seguimento (A): ETH × BTC em 24h | `get_market_data(ethereum)` | 3 (com correção) | 8.200 | 10 | 22 | 51,9 |
| Isolamento (B): "sobre qual cripto perguntei?" | — | 2 | 3.926 | 4 | 11 | 12,7 |
| Reinício (processo novo, A): "quais ativos perguntei?" | — | — | — | 10 antes do turno | — | 30,2 |

Respostas reais:
- Isolamento (B): "Não foi possível identificar nenhuma criptomoeda mencionada em suas perguntas anteriores nesta
  conversa."
- Reinício (A): "Você perguntou sobre o preço atual do Bitcoin e sobre a variação de 24 horas entre o Bitcoin e o
  Ethereum (não confirmado pelas fontes consultadas). Aviso: a verificação automática não confirmou toda a
  resposta." O conteúdo está certo; a marca vem da limitação 2 da seção 7.

## 7. Limitações conhecidas

1. Evidência da verificação restrita ao turno (problema 5.2).
2. Afirmações sobre a própria conversa não têm evidência de ferramenta e saem "não confirmadas" (herdado).
3. Feedback da correção e instrução da pré-consulta ficam na thread e seriam vistos nos turnos seguintes.
4. Contexto cresce a cada turno (8.200 tokens no 2º turno, com correção).
5. Sessões anteriores ao checkpointer não têm thread.
6. Histórico de checkpoints sem poda (só a exclusão da sessão remove).

## 8. Aprendizados

- Retomada após falha é um ganho concreto do checkpoint por super-step: o turno continua do nó pendente sem
  repetir ferramentas.
- Persistir o estado expõe ao modelo observações antigas; a régua da verificação precisa acompanhar o que o modelo
  vê.

## 9. Pendências e insumos para a próxima etapa

- Etapa 4: recorte (`trim_messages`) e filtragem sem chamadas órfãs, preservando instruções do sistema e a pergunta;
  filtrar mensagens internas; evidência alinhada ao contexto visto; reaproveitar resumo e Mem0 do projeto-base sem
  injeção repetida; testes de conversa longa.
