# Fase 3 — Memória conversacional e raciocínio contextual

| | |
|---|---|
| **Período** | 2026-10-28 a 2026-10-29 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose, Ollama, CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (thinking desligado) e `nomic-embed-text` |
| **Versões novas** | `mem0ai` 2.2.1 (mantidos: `langchain-core` 1.6.12, `chromadb` 1.5.9, `fastapi` 0.150.0) |

## 1. Objetivo

Transformar a aplicação num assistente conversacional que:
- mantém o contexto entre mensagens e entre sessões, com persistência em SQLite e memória semântica no Chroma;
- interpreta continuações, correções e referências a resultados anteriores;
- não apresenta dado antigo como atual;
- preserva as Fases 1 e 2.

## 2. Escopo e adaptações do enunciado

O enunciado foi **adaptado por decisão do usuário** antes da implementação:

1. **Nada depreciado.** O `ConversationBufferMemory` (exigido na seção 5) está depreciado desde o LangChain 0.3.1, com remoção na 2.0, e **não foi usado**. Histórico e janela são próprios, sobre SQLite.
2. **Framework de memória especializado.** A seção 12 pedia só comparar. O usuário pediu para usar um: LangMem primeiro; depois da avaliação, Mem0.

Mantido do enunciado:
- API existente;
- LangChain na orquestração;
- SQLite como fonte da verdade e Chroma para memória semântica;
- 7 intenções;
- 8 cenários;
- documentação pedida.

Fora do escopo: CoinGecko e DeFiLlama (DeFi continua `unavailable`) e autenticação.

## 3. Solução implementada

```
POST /conversations/{id}/messages
 → trava por sessão → SQLite (mensagem) → intenção (regras + LLM) → resolver (contexto ativo, análises)
 → contexto (janela + resumo + memórias Mem0 da sessão, com orçamento) → chains LCEL da Fase 2 ou comparação com resultado anterior
 → SQLite (resposta, análise, contexto ativo) + episódio no Mem0 → segundo plano: resumo, reindexação, extração semântica
```

Módulos novos:
- `app/persistence/` (SQLite);
- `app/memory/{short_term,long_term}.py`;
- `app/intent/` (schemas, classificador, resolver);
- `app/conversation/` (`context_builder`, serviço);
- `app/api/conversation_routes.py`.

Detalhes em `docs/architecture.md` (seção 8) e `docs/memory-strategy.md`.

## 4. Decisões técnicas

| Decisão | Alternativas | Motivo |
|---|---|---|
| Não usar `ConversationBufferMemory` | Usar isolado em `app/legacy/` (como o ReAct da Fase 2) | Diretriz do usuário: depreciado (remoção na 2.0); `InMemoryChatMessageHistory` também depreciado |
| Mem0 para a memória longa | LangMem, Letta, código próprio | Spikes (seção 5.1): único sem depreciação, Chroma e SQLite nativos, isolamento por `run_id` testado |
| Resumo e janela em código próprio (LCEL) | `summarize_messages` do LangMem | LangMem descartado (depreciação na dependência); o Mem0 não resume conversa |
| Fatos que sobrevivem ao resumo extraídos dos **metadados** | Deixar tudo para o LLM | Lição das Fases 1 e 2: modelo pequeno erra e omite. Resultados anteriores, correções e entidades não podem depender dele |
| Regras determinísticas prevalecem sobre o LLM na intenção | Só LLM | Período, correção, ativos e referência são detectáveis por regra com mais confiabilidade que o e2b |
| Episódios com `infer=False` e extração semântica em segundo plano | Extração em todo turno | Extração do Mem0 leva de 68 a 89 s; episódios precisam ser exatos |
| Filtro anti-valores de mercado na memória | Confiar no prompt | No spike, o Mem0 guardou "ETH a US$ 2.329,72" como fato |
| `MemoryBackend` (interface) + `LazyMemoryBackend` | Mem0 direto | Testes offline sem rede; API sobe sem Ollama; troca de backend localizada |
| Conclusões numéricas (diferença atual × anterior) feitas pelo código | Pedir ao LLM | Lição da Fase 2: o modelo inverte comparações |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Escolha do framework de memória: três spikes

| Candidato | Achado | Decisão |
|---|---|---|
| **LangMem 0.0.30** | Dependências compatíveis; `summarize_messages` funcionou (22 s); **`trustcall` importa `langgraph.constants.Send`, depreciado no LangGraph 1.0** (o aviso aparece em qualquer uso); 1 de 3 extrações inventou memória | Pela política combinada, parar e consultar → usuário pediu Mem0/Letta |
| **Letta** | Documentação oficial: a imagem Docker "não é mais mantida nem suportada"; Postgres + pgvector; laço de agente próprio | Descartado sem execução |
| **Mem0 2.2.1** | Sem depreciação; Chroma e SQLite; isolamento por `run_id` ✅; extração **lenta** (68–89 s) e **guardou preço como fato** | **Escolhido**, com mitigações (seção 4) |

Registro completo: `docs/memory-decision.md`.

### 5.2 30 dias da CoinPaprika

O plano gratuito devolve `percent_change_30d = 0` (e `1y = 0`) para todos os ativos, verificado com BTC, ETH e SOL. Na Fase 2 esse 0 chegava ao modelo como dado. Agora:
- 0 exato vira "indisponível";
- pedidos de 30 dias recebem limitação explícita (`partial`), com as variações reais de 24h e 7d.

### 5.3 Ajustes de regras e testes

- "Qual o preço atual?" e "Considere os últimos 30 dias" **sem contexto** caíam em "pesquisa geral". Corrigido: viram pedido de esclarecimento.
- **Referência ambígua** a resultado anterior: o contexto ativo resolvia sozinho. Agora só resolve com referência explícita ("os dois", "them"); sem ela, pergunta.
- **Teste de depreciação** com `importlib.reload` recriava classes de exceção e quebrava outros testes. Movido para um subprocesso limpo.

### 5.4 Problemas revelados pela execução real

| Sintoma | Correção |
|---|---|
| Declaração de preferência rodou de novo a análise DeFi | Preferência só é confirmada e enviada à extração |
| Resumos acumulavam na memória longa (v1, v2, v3) | `replace_episode`: um resumo por sessão no Chroma |

## 6. Evidências e métricas

Detalhes em `docs/conversation-evaluation.md`.

| Verificação | Resultado |
|---|---|
| Testes offline (Fases 1–3) | **260 passed** |
| Integração Fase 3 (Ollama + Mem0 + CoinPaprika reais) | 1 passed, 537 s (A, D, B, preferência, reinício, C, isolamento) |
| Integração completa (Fases 1–3) | **12 passed** em 702 s (5 Fase 1 + 6 Fase 2 + 1 Fase 3), sem regressão |
| API real com `docker compose restart app` | Sessão retomada (8 mensagens em ordem, contexto ativo restaurado); continuação, resultado anterior e isolamento corretos; sessão inexistente → 404 |
| Memória semântica real | "O usuário prefere que as análises… focadas em risco" recuperada com score 0,617; nenhum valor de mercado gravado |
| Tempo por turno (CPU) | 7 s (resultado anterior) a cerca de 70 s; 8 s para confirmar uma preferência |

## 7. Limitações conhecidas

- **Disputa pelo Ollama:** a extração em segundo plano compete com as requisições ("Analise SOL" levou 177 s).
- **"Resultado anterior"** pega a análise mais recente com os mesmos ativos, o que pode ser a do turno anterior (diferença de 0%).
- **Memórias semânticas** às vezes gravadas em inglês pelo Mem0.
- **Sem autenticação**; `session_id` não é credencial.
- **Travas por sessão** valem por processo, não entre vários workers.
- **Contagem de tokens aproximada**, porque o Ollama não expõe o tokenizer.

## 8. Aprendizados

- **Depreciação também vem de dependências transitivas:** o LangMem em si não usa nada depreciado, mas o `trustcall` sim. Medir no spike, com a pilha real, evitou adotar algo com prazo de remoção.
- **Frameworks de memória pressupõem modelos fortes.** Com um modelo de 2B em CPU, extração por LLM é lenta e precisa de filtros determinísticos. O que é crítico (episódios, resultados anteriores) não deve depender dela.
- **A memória pode reintroduzir dado desatualizado.** Um preço guardado como "fato" seria reapresentado depois; a política de retenção precisa proibir isso explicitamente.
- **Regras primeiro, LLM depois,** na interpretação de continuações: período, correção e ativos citados são detectados com mais confiabilidade por regras do que pelo modelo pequeno.
- **Testar com a API real e reinício real** revelou dois comportamentos (preferência e resumos acumulados) que os testes com dublês não pegaram.

## 9. Pendências e insumos para a próxima fase

- **Fila ou prioridade para o Ollama**, para a manutenção em segundo plano não atrasar as respostas (ou extração só em horários ociosos).
- **Heurística de "resultado anterior"**, distinguindo "o de agora há pouco" de "o de antes".
- **Autenticação e `user_id` real** no Mem0, se houver mais de um usuário.
- **Migração do agente da Fase 2** para LangGraph (`create_agent` com checkpointing), que unificaria `/agent` e `/conversations`.
- **DeFiLlama e CoinGecko:** as intenções `defi_analysis` e `historical_analysis` já preservam o contexto para quando houver fonte.
