# Estratégia de memória — Fase 3

Decisões e desvios do enunciado: [memory-decision.md](memory-decision.md).

## 1. Categorias CoALA → implementação

| Categoria (CoALA) | O que é aqui | Onde fica | Como é gravada |
|---|---|---|---|
| **Trabalho** (curto prazo) | Mensagem atual, pedido em andamento (`active_context`), janela das últimas mensagens, resumo | SQLite (`sessions`, `messages`, `session_summaries`) → montada a cada turno por `context_builder` | A cada turno, sem LLM (o resumo usa LLM só para o texto) |
| **Episódica** | Análises concluídas (ativos, intenção, período, resposta, **horário dos dados**) e resumos da sessão | SQLite `analyses` (fonte da verdade) + cópia indexada no Mem0/Chroma (`doc_type=analysis\|summary`) | Determinística, `infer=False` |
| **Semântica** | (a) Fatos duráveis do usuário: restrições, preferências, correções; (b) corpus de conceitos da Fase 2 | (a) Mem0/Chroma `conversation_memory`, por sessão; (b) Chroma `crypto_knowledge`, global e sem dados privados | (a) Mem0 `infer=True` em segundo plano + filtro; (b) `POST /knowledge/ingest` |
| **Procedimental** | Como investigar: prompts PTCF, ferramentas CoinPaprika, chains, regras de intenção, grounding | Código e configuração | Não é gravada como memória (decisão deliberada) |

## 2. Responsabilidades de cada armazenamento

| Armazenamento | Responsável por | Não é usado para |
|---|---|---|
| `data/conversations.sqlite3` (projeto) | Sessões, mensagens **ordenadas**, resumos versionados, análises, contexto ativo. **Fonte da verdade** | Busca semântica |
| `data/memory_chroma` (Mem0) | Vetores das memórias episódicas e semânticas, com metadados (`run_id`, `category`, `doc_type`, `source_ref`, `created_at`) | Histórico ordenado (o Chroma não é log de conversa) |
| `data/mem0_history.sqlite3` (Mem0) | Histórico interno do Mem0 (ADD/UPDATE/DELETE de cada memória) | Dados da aplicação |
| `data/chroma` (Fase 2) | Corpus conceitual global | Nada privado da conversa |

**Referências estáveis** entre SQLite e Chroma: `source_ref = analysis:<id>` e `summary:<session_id>:v<n>`.

**Sincronização sem transação atômica entre os dois:**
1. o SQLite é gravado primeiro;
2. o Chroma é atualizado depois, em melhor esforço;
3. falha na indexação deixa `analyses.indexed = 0` e a manutenção do turno seguinte reindexa;
4. ao apagar uma sessão: primeiro o SQLite (`ON DELETE CASCADE`), depois o Mem0 (`delete_all(run_id)`). Se o segundo falhar, fica registrado em log para limpeza manual.

## 3. Política de retenção

| Conteúdo | Destino | Regra |
|---|---|---|
| Mensagem bruta (usuário/assistente) | Só SQLite | **Nunca** vira vetor |
| Análise concluída com dados reais | SQLite + episódio no Mem0 | Sempre, sem LLM |
| Resumo da sessão | SQLite (versionado) + episódio no Mem0 | Quando o resumo é atualizado |
| Fala do usuário com correção ou preferência | Mem0 `infer=True` | Só falas do **usuário**, em segundo plano |
| Memória extraída com valor monetário ou percentual, ou fala do assistente | **Descartada** | Filtro determinístico `is_rejected_memory` logo após a extração |
| Procedimentos | Código | Nunca como memória |

Motivo da regra anti-valores: no spike, o Mem0 guardou "ETH a US$ 2.329,72" como fato. Um preço guardado na memória seria **dado desatualizado reapresentado depois**, exatamente o que o projeto proíbe.

## 4. Memória curta: janela + resumo

- **Janela:** últimas `MEMORY_WINDOW_MESSAGES` mensagens (padrão 10), literais.
- **Resumo incremental:** com pelo menos `SUMMARY_TRIGGER_MESSAGES` mensagens fora da janela ainda não resumidas, o `ConversationSummarizer` condensa essas mensagens mais o resumo anterior. A gravação usa **versão otimista**: uma atualização baseada em versão antiga é descartada, sem sobrescrever a mais nova.
- **O que sobrevive ao resumo sem depender do LLM** (`details`): entidades, correções do usuário (mensagem marcada como correção) e resultados anteriores (`análise <id> (intenção: ativos; dados de <horário>)`), extraídos **dos metadados das mensagens**. O LLM só redige o texto corrido e sugere restrições e perguntas em aberto. Se ele falhar, entra um texto determinístico.
- **Quando roda:** em segundo plano, depois da resposta, junto com a extração semântica.

## 5. Montagem do contexto e orçamento

Ordem de prioridade (as últimas são cortadas primeiro quando o orçamento estoura):

1. mensagem atual (nunca cortada);
2. pedido em andamento (`active_context`);
3. resumo da sessão;
4. mensagens recentes (a mais **antiga** sai primeiro);
5. memórias recuperadas (a de **menor score** sai primeiro).

- **Fora deste orçamento:** as instruções de sistema e os dados atuais vão nos prompts das chains, com prioridade máxima. O orçamento (`CONTEXT_BUDGET_TOKENS`, padrão 3000) cobre só o contexto conversacional que é somado a eles, dentro de `OLLAMA_NUM_CTX` (8192).
- **Contagem de tokens:** `count_tokens_approximately` do `langchain-core` (≈ 4 caracteres por token). O Ollama não expõe o tokenizer; o orçamento fica bem abaixo da janela do modelo para absorver o erro da aproximação.
- **Sem duplicação:** memórias episódicas cujo `source_ref` já aparece na janela ou no resumo, e o próprio resumo indexado como memória, não entram de novo.
- **Dado histórico rotulado:** o bloco de contexto começa com "Valores de mercado citados abaixo são HISTÓRICOS"; mensagens trazem o horário; o resumo lista "Resultados anteriores (HISTÓRICOS)".

## 6. Recuperação

- Consulta: mensagem atual + ativos e período resolvidos.
- `Mem0.search(filters={"user_id": "local", "run_id": <sessão>}, top_k=MEMORY_TOP_K, threshold=MEMORY_MIN_SCORE)`.
- **Defesa extra:** resultados com outro `run_id` são descartados no código.
- Falha da memória longa não derruba o turno: a resposta sai sem ela e registra "memória de longo prazo indisponível neste turno".

## 7. Sessões, isolamento e persistência

- Cada conversa tem `session_id` (32 hex). Sessão nova começa sem contexto ativo, janela, resumo ou memórias.
- **SQLite:** toda consulta de mensagens, resumos e análises filtra por `session_id`; `get_analysis` exige o par `(id, session_id)`.
- **Mem0:** `run_id = session_id` em toda gravação e busca.
- **Concorrência:**
  - trava por sessão no serviço (turnos da mesma sessão são serializados);
  - `BEGIN IMMEDIATE` na alocação da sequência de mensagens;
  - versão otimista no resumo;
  - manutenção em uma única thread de segundo plano.
- **Reinício:** tudo o que define a conversa está no SQLite e no Chroma persistentes (diretório `./data` montado no Compose). Uma nova instância da API retoma a sessão pelo id.

## 8. Limitações

- **Sem autenticação:** o `session_id` identifica a conversa mas **não é credencial**. Quem conhece o id lê e altera a conversa. O `user_id` do Mem0 é fixo (`local`).
- **Contagem de tokens aproximada.**
- **Extração semântica depende do modelo pequeno:** o filtro barra valores de mercado, mas não garante utilidade.
- **Concorrência entre processos:** as travas por sessão valem dentro de um processo; com vários workers, o SQLite ainda garante a ordem das mensagens, mas dois turnos da mesma sessão podem se intercalar.
- **Histórico de preços:** o plano gratuito da CoinPaprika não tem séries históricas. Perguntas de 30 dias ou mais recebem a limitação explícita (ver `conversation-evaluation.md`).
