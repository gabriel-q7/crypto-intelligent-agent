# Decisões transversais

Este capítulo reúne as decisões que valem para o projeto inteiro. Os detalhes de cada uma estão nos capítulos das fases e nos documentos de `docs/`.

## Política de não usar APIs depreciadas

### A diretriz

A partir da Fase 3, o projeto adotou uma regra explícita: **nenhum código novo usa classe ou função marcada como depreciada**, mesmo quando o enunciado da fase a pede pelo nome. Quando um spike ou uma checagem encontrava um aviso de depreciação, a implementação parava e o caso era decidido antes de seguir. Em todos os casos a decisão foi trocar pela API atual equivalente e registrar o desvio.

Os motivos:

1. **Prazo de remoção conhecido.** Os avisos encontrados trazem a versão de remoção (LangChain 2.0, LangGraph 2.0). Código novo com essas APIs nasceria com data de expiração.
2. **A própria biblioteca indica o substituto.** Os avisos apontam para `create_agent` e para a persistência do LangGraph. Seguir a recomendação mantém o projeto no caminho que a biblioteca vai continuar mantendo.
3. **Avisos escondem problemas.** Uma execução que imprime avisos em todo import treina a equipe a ignorá-los. Com zero avisos, qualquer aviso novo é sinal de que algo mudou.

### O que foi trocado

| Fase | Pedido pelo enunciado ou candidato | Situação verificada | Substituto adotado |
|---:|------------------------------------|------------------------------|-------------------------|
| 2 | FAISS via `langchain-community` | Pacote em descontinuação ("is being sunset"), sem `langchain-faiss` no PyPI; índice persistido com pickle, exigindo desserialização insegura | Chroma embutido via `langchain-chroma`, pacote dedicado |
| 3 | `ConversationBufferMemory` | Depreciado desde o LangChain 0.3.1, remoção na 2.0 | Histórico próprio em SQLite + janela e resumo incremental próprios |
| 3 | `InMemoryChatMessageHistory` | Depreciado no `langchain-core` 1.6.4, remoção na 2.0 | O mesmo histórico em SQLite |
| 3 | LangMem (framework de memória) | A dependência obrigatória `trustcall` importa `Send` de `langgraph.constants`, depreciado no LangGraph 1.0; o aviso aparece em qualquer uso | Mem0 2.2.1, sem nenhum aviso nos spikes |
| 5 | `create_openai_tools_agent` + `AgentExecutor` | Sem aviso, mas pertencem ao pacote legado `langchain-classic` | `create_agent` do pacote `langchain` 1.4.9 (grafo LangGraph) |
| 5 | `RunnableWithMessageHistory` | Aviso no `langchain-core` 1.7.6: "Use LangGraph's built-in persistence instead"; `BaseChatMessageHistory` também depreciado | `SessionHistoryAdapter`, que lê o SQLite e o Mem0 da Fase 3 |
| 5 | Streamlit `use_container_width` | Depreciado no Streamlit 1.65 ("will be removed in a future release") | `width="stretch"` |

O caso do LangMem mostrou que a depreciação também vem de **dependências transitivas**: o pacote em si não usa nada depreciado, mas uma dependência obrigatória usa. Por isso a checagem foi feita com a pilha real instalada, e não só pela documentação.

### Como a regra foi verificada

- **Spikes com avisos visíveis:** cada candidato foi importado e executado com `warnings.simplefilter("always")` no ambiente da imagem antes da adoção.
- **Teste automático:** `test_phase3_modules_use_no_deprecated_langchain_apis` importa os módulos da Fase 3 num interpretador limpo, com `LangChainDeprecationWarning` tratado como erro. Rodar num subprocesso foi necessário porque recarregar módulos no mesmo processo recriava classes de exceção e quebrava outros testes.
- **Auditoria final (2026-11-13, refeita após as seções 5.9 a 5.11 da Fase 5):** a suíte offline completa rodou com `-W always::DeprecationWarning -W always::PendingDeprecationWarning -W always::FutureWarning`: **391 testes aprovados e nenhum aviso** (`docs/evidence/final/pytest-deprecation-audit.log`).

### A exceção: APIs legadas exigidas na Fase 2

O enunciado da Fase 2 exigia `initialize_agent`, `ZERO_SHOT_REACT_DESCRIPTION` e `RouterChain`. No LangChain 1.x essas APIs saíram do pacote `langchain` e vivem no `langchain-classic`. A política ainda não existia, e elas foram usadas como o enunciado exigia, com duas salvaguardas:

- ficam **isoladas** em `app/legacy/`, consumidas por interfaces modernas (o `CryptoAgent` cuida de memória, rastreamento e montagem da saída; o `RouterChain` só classifica);
- atendem só os endpoints da Fase 2 (`POST /agent` e `POST /route?router=legacy`).

Na Fase 5 o agente principal migrou para `create_agent`. O código legado continua no repositório só para preservar a Fase 2, e removê-lo afeta apenas `app/legacy/`.

## Desvios deliberados do enunciado

Cada desvio tem o motivo registrado no capítulo da fase correspondente.

| Fase | Enunciado | Implementado | Motivo |
|---:|------------------------------------|---------------------------|------------------------------|
| 2 | Scripts Python | Aplicação HTTP (FastAPI) | Uma entrada única com dependências criadas uma vez, no lugar de oito scripts com inicialização repetida |
| 2 | FAISS | Chroma | Descontinuação do `langchain-community`, pickle inseguro, sem atualização incremental nem filtro por metadados |
| 3 | `ConversationBufferMemory` | SQLite + janela + resumo próprios | Depreciado |
| 3 | Só comparar frameworks de memória | Usar um (Mem0) | Framework mantido para a memória de longo prazo, no lugar de código próprio; LangMem descartado por depreciação transitiva, Letta por imagem Docker sem suporte |
| 5 | `create_openai_tools_agent` + `AgentExecutor` | `create_agent` | Pacote legado; `create_agent` é o substituto oficial |
| 5 | `RunnableWithMessageHistory` | `SessionHistoryAdapter` | Depreciado |
| 5 | NLI com Transformers (`mDeBERTa-v3-base-mnli-xnli`) | NLI com `gemma4:e2b` via Ollama | Cerca de 2 GB a mais (torch, transformers e modelo) com 5,3 GB livres em disco, e um segundo motor de inferência na mesma CPU |

## Modelo local (Ollama) em CPU

### Por que um modelo local e pequeno

A escolha de servir o modelo localmente pelo Ollama, em vez de usar uma API comercial, partiu da natureza do problema:

1. **O conhecimento que importa vem das ferramentas.** Uma análise de mercado cripto depende de preços, volumes e capitalizações que mudam a cada minuto. Nenhum modelo, por maior que seja, tem esses valores no treinamento. A qualidade da resposta depende de o agente **escolher a ferramenta certa, passar os argumentos certos e usar o resultado sem inventar**, e não da memória do modelo. Por isso um modelo pequeno é suficiente, desde que siga o protocolo de ferramentas. O projeto serviu justamente para medir esse potencial: até onde um modelo local leve consegue orquestrar ferramentas reais.
2. **Privacidade.** Perguntas, histórico de conversa e memórias de longo prazo ficam na máquina. Só as consultas à CoinPaprika (símbolos de ativos) saem para a internet; o LangSmith é opcional e desligado por padrão. Quando ligado, perguntas e respostas vão para o LangSmith; a suíte offline não envia traces (desligados no `conftest` desde 2026-11-13, Fase 5, seção 5.11).
3. **Avaliar o estado dos modelos locais.** Outro objetivo era verificar a qualidade que modelos abertos já alcançam num computador pessoal, sem GPU dedicada.
4. **Custo e reprodutibilidade.** Sem cobrança por token e sem dependência de disponibilidade de um provedor; qualquer pessoa reproduz o ambiente com `make up`.

### Hardware

Todas as medições foram feitas num notebook com **AMD Ryzen 5 5500U** (6 núcleos, 12 threads), 22 GB de RAM e **sem GPU dedicada**. A inferência roda só na CPU.

### Escolha do modelo: benchmark

O benchmark da Fase 1 (`benchmarks/results/`) comparou cinco configurações nas mesmas 6 perguntas, medindo latência, validade do JSON e acerto da classificação:

| Modelo | Thinking | Latência média (s) | JSON válido | Classificação correta |
|---------------|------------|-------------------:|------------:|----------------------:|
| `qwen2.5:7b` | — | 71,0 | 100% | 83% |
| `gemma4:e4b` | desligado | 83,1 | 100% | 100% |
| `gemma4:e4b` | ligado | 118,1 | 100% | 100% |
| `mistral:7b` | — | 141,9 | 67% | 100%¹ |
| `gemma4:e2b` | desligado | **42,3** | 100% | 100% |

¹ Só sobre as respostas válidas.

O **`gemma4:e2b` (família Gemma 4) com *thinking* desligado** foi o mais rápido (cerca de 40% menos latência que o `qwen2.5:7b`), com JSON sempre válido e acerto máximo nas perguntas do conjunto. Ele virou o padrão em 2026-10-18 e foi mantido até o fim. Na Fase 5, o mesmo modelo provou suportar tool calling nativo pelo Ollama, o que permitiu migrar o agente sem trocar de modelo.

### Resultado

Considerando o hardware, o desempenho foi razoável: na avaliação final da Fase 5, o modelo escolheu a categoria de ferramenta certa em 14 dos 15 exemplos do agente (`tool_selection` 0,933), combinou API, SQL e documentos na mesma resposta e não inventou valores de mercado (`groundedness` 1,0 nas avaliações da Fase 5). Um turno completo do agente integrado, com verificação, levou de 10 s a 104 s na avaliação final; respostas com correção ou várias ferramentas chegaram a quase 3 min em execuções anteriores.

O preço de um modelo de 2B em CPU aparece em todas as fases: latência de dezenas de segundos por chamada e erros de raciocínio numérico. As duas decisões seguintes são a resposta a isso.

## Código determinístico onde o modelo pequeno falha

Uma lição repetida em todas as fases: **o que pode ser calculado por código não deve ser pedido ao modelo**.

| Fase | O modelo errou | Passou a ser feito por código |
|-----:|----------------------------------------------|-------------------------------------------------------------|
| 1 | Omitia a etapa de coleta de dados | Etapa derivada de `requested_metrics` e registrada em `assumptions` |
| 1 | Escrevia valores de mercado sem fonte | Trava que remove frases com valores e acrescenta aviso |
| 2 | Invertia comparações, mesmo com o ranking no prompt | Conclusão da rota de comparação montada por código |
| 2–4 | Inventava fontes e horários | Achados, fontes e horários extraídos das observações (grounding) |
| 3 | Instável para detectar período, correção e ativos | Regras determinísticas prevalecem sobre o LLM na intenção |
| 4 | Errava JOIN e copiava filtros indevidos | View `snapshot_history`, período por regra, SQL validado e parametrizado |
| 5 | Comparações numéricas na verificação | Checagem numérica determinística; o LLM só julga afirmações conceituais |
| 5 | Recusava pedido de recomendação sem consultar os dados dos ativos citados | Guarda `skipped_lookup` aciona a correção para consultar cada ativo |

## Segurança

- **SQL gerado por LLM** passa por duas camadas: parser (`sqlglot`) antes da execução e conexão somente leitura com `set_authorizer` e `progress_handler` durante. Os literais viram parâmetros.
- **Sem pickle:** a troca para o Chroma eliminou o `allow_dangerous_deserialization`.
- **Segredos:** chaves ficam no `.env` (fora do git); os logs redigem a chave da CoinPaprika; os traces do LangSmith recebem só um hash do `session_id`.
- **Sem autenticação:** o `session_id` não é credencial, e a listagem de conversas expõe todos os ids. Isso é aceitável apenas no uso local de um único usuário e está registrado como limitação.

## Estratégia de testes

- **Testes offline** (padrão do `pytest`): LLM falso, API simulada e bancos temporários. Rodam em segundos e cobrem contratos, regras determinísticas, segurança do SQL e fluxos completos da API.
- **Testes de integração** (opt-in com `-m integration` e `-m network`): Ollama, CoinPaprika, Chroma e Mem0 reais. Como a saída do modelo varia mesmo com temperatura 0, esses testes usam critérios de comportamento (ferramenta certa, melhora de ranking, status esperado), e não igualdade de texto.
- **Avaliação (Fase 5):** dataset versionado (20 exemplos na versão final), avaliadores de resposta e de trajetória, execução local ou no LangSmith.
