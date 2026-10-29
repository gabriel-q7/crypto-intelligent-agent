# Decisão: memória conversacional sem APIs depreciadas, com Mem0

| | |
|---|---|
| **Status** | Aceita e implementada (Fase 3) |
| **Data** | 2026-10-28 |
| **Decidido por** | Usuário, a partir de spikes técnicos registrados aqui |
| **Desvios do enunciado da Fase 3** | (1) `ConversationBufferMemory` **não** é usado (seção 5 do enunciado); (2) um framework de memória (**Mem0**) **entra** nas dependências de execução (a seção 12 pedia só comparar LangMem, Mem0 e Letta, sem adotar) |

## 1. Diretriz do usuário

Antes da implementação, o usuário estabeleceu duas diretrizes:
1. **evitar tudo que esteja depreciado**;
2. avaliar o uso de um framework de memória especializado, começando pelo **LangMem**.

## 2. `ConversationBufferMemory`: por que não foi usado

Verificado na pilha do projeto (`langchain-classic` 1.0.8, `langchain-core` 1.6.12), em 2026-10-28:

```
LangChainDeprecationWarning: The class `ConversationBufferMemory` was deprecated in LangChain 0.3.1 and will be
removed in 2.0.0. Use `langchain.agents.create_agent` instead. For agents that need to remember prior interactions,
use `create_agent` with checkpointing or the `Store` API.
LangChainDeprecationWarning: The class `InMemoryChatMessageHistory` was deprecated in LangChain 1.6.4 and will be
removed in 2.0.0.
```

A classe funciona, mas está depreciada e tem remoção marcada. Pela diretriz 1, ela foi substituída por:
- **histórico persistente próprio** em SQLite (`app/persistence/sqlite_store.py`), que é a fonte da verdade das mensagens ordenadas;
- **janela deslizante + resumo incremental** próprios (`app/memory/short_term.py`), sem nenhuma classe depreciada.

O requisito funcional da seção 5 continua atendido:
- `session_id` por conversa;
- restauração após reinício;
- janela e resumo para o prompt não crescer sem limite;
- persistência testada explicitamente.

O que fica de fora é **a classe legada em si**. Um teste automático verifica que nenhum módulo da Fase 3 emite `LangChainDeprecationWarning` ao ser importado.

Caminho moderno apontado pelo próprio aviso: `create_agent` com checkpointing ou a API `Store` do LangGraph. Ficou para quando o agente da Fase 2 for migrado (ver `docs/orchestrator-comparison.md`).

## 3. Spikes realizados

Todos em container descartável, com as versões do projeto fixadas, `gemma4:e2b` e `nomic-embed-text` no Ollama real.

### 3.1 LangMem 0.0.30 (último release: 2025-10-27)

| Item | Resultado |
|---|---|
| Dependências | Resolvem com `langchain-core` 1.6.12. Entram `langchain` 1.4.6, `langgraph` 1.2.18, `trustcall` 0.0.39 e `langchain-openai`/`langchain-anthropic` (não usados) |
| Depreciação | ⚠️ `trustcall` (dependência obrigatória) importa `Send` de `langgraph.constants`: *"Importing Send from langgraph.constants is deprecated… Deprecated in LangGraph V1.0 to be removed in V2.0."* O aviso aparece em **qualquer** uso do LangMem, inclusive só do resumo (o pacote carrega o `trustcall` ao iniciar) |
| `summarize_messages` | ✅ Resumiu 7 mensagens em 22 s; o `RunningSummary` incremental não refez o que já estava resumido; o prompt padrão gera o texto em inglês |
| `create_memory_manager` | Chamada de ferramentas funcionou (10–26 s); 2 de 3 extrações corretas. **Na terceira, guardou a própria instrução ("Nunca guarde preços.") como memória do usuário** |

Pela política combinada (depreciação → parar e consultar), o usuário foi consultado e pediu para avaliar Mem0 e Letta.

### 3.2 Letta (antigo MemGPT)

Avaliado pela documentação oficial, sem spike de execução:
- **a imagem Docker do servidor "não é mais uma superfície do produto mantida ou suportada"** (docs.letta.com/guides/docker); o pacote `letta` do PyPI hoje é o *Letta Code*, um agente de terminal;
- armazena em **Postgres + pgvector** (sem SQLite nem Chroma, ambos exigidos pelo enunciado);
- roda **o próprio laço de agente**, o que substituiria a orquestração LangChain;
- recomenda começar com "um modelo grande de fronteira", porque modelos fracos "podem fazer o agente se comportar de forma inesperada".

**Descartado:** produto não suportado nesse formato, além de incompatível com SQLite, Chroma e LangChain.

### 3.3 Mem0 2.2.1

| Item | Resultado |
|---|---|
| Dependências | Resolvem com a pilha (`pip check` sem conflitos). +51 MB: `openai`, `qdrant-client` (não usado), `posthog` (telemetria), `sqlalchemy`, `protobuf`. O extra `mem0ai[extras]` **não** é instalado (exige `langchain-core<1.0`) |
| Depreciação | ✅ **Nenhum aviso** no import, no `from_config` nem nas chamadas |
| Armazenamento | ✅ Chroma nativo (`vector_store.provider = chroma`, persistente) + histórico de eventos em SQLite (`history_db_path`) |
| Isolamento | ✅ `run_id = session_id`; `search`/`get_all` com `filters={"user_id", "run_id"}` só devolvem a sessão pedida; `delete_all` por sessão funcionou |
| Extração (`infer=True`) | ⚠️ **68–89 s** por conversa com o e2b (várias chamadas ao LLM); guardou **um preço como fato** ("ETH a US$ 2.329,72") e confirmações do assistente como memórias |
| Avisos de execução | Informativos: Chroma sem busca híbrida (BM25); spaCy ausente (lematização opcional) |

## 4. Decisão

**Mem0 para a memória de longo prazo** (episódica e semântica) e **código próprio em LCEL para a memória curta** (janela + resumo), porque o Mem0 não cobre o resumo da conversa.

Por quê:
- é o único dos três sem depreciação;
- conversa nativamente com Chroma e SQLite, como pede o enunciado;
- o isolamento por sessão (`run_id`) é nativo e foi testado;
- é chamado como biblioteca pelo nosso código, sem substituir a orquestração LangChain.

## 5. Mitigações dos problemas observados no spike

| Problema (spike) | Mitigação implementada |
|---|---|
| Preço guardado como fato | Só **falas do usuário** entram na extração; prompt próprio proíbe valores de mercado; **filtro determinístico** (`is_rejected_memory`) apaga, logo após a extração, qualquer memória com valor monetário ou percentual |
| Confirmações do assistente viram memória | Falas do assistente não são enviadas; o mesmo filtro rejeita textos do tipo "The assistant…/O assistente…" |
| 68–89 s por extração | Extração em **segundo plano**, depois da resposta, e **só** quando a fala do usuário traz correção ou preferência ("prefiro", "sempre", "nunca", "quis dizer"...) |
| Extração por LLM é imprevisível para registros importantes | **Episódios** (análises concluídas e resumos) são gravados com `infer=False`: texto e metadados determinísticos, sem LLM |
| Telemetria | `MEM0_TELEMETRY=False` (no código e no Compose) |
| API sobe sem Ollama? | `LazyMemoryBackend`: o Mem0 é criado no primeiro uso; falha → o turno segue sem memória longa e a indexação fica pendente (`analyses.indexed = 0`) |
| Testes sem rede | Interface `MemoryBackend`; testes offline usam `InMemoryBackend` com a mesma política de retenção; integração usa o Mem0 real |

## 6. Consequências

**Positivas**
- Nenhuma API depreciada no código novo; há teste automático para isso.
- Memória longa persistente e isolada por sessão, sem escrever a camada vetorial do zero.
- Histórico de eventos do Mem0 (ADD/UPDATE/DELETE) disponível para auditoria.

**Negativas (aceitas)**
- Mais dependências: `openai` e `qdrant-client` são instalados sem uso; `posthog` fica com a telemetria desligada.
- Duas bases SQLite (`conversations.sqlite3` do projeto e `mem0_history.sqlite3` do Mem0) e dois diretórios Chroma (corpus e memória). As responsabilidades estão em `docs/memory-strategy.md`.
- A qualidade da extração semântica depende do modelo pequeno. O filtro barra valores de mercado, mas não garante que toda memória aceita seja útil.
- O Mem0 2.x mudou a API de `search`/`get_all` (exige `filters=`); atualizações de versão precisam ser testadas.

## 7. Como reverter

A memória longa é acessada só pela interface `MemoryBackend` (`app/memory/long_term.py`). Para trocar o Mem0:
1. implemente outra classe com os mesmos 5 métodos;
2. ajuste a fábrica em `app/api/container.py`;
3. remova `mem0ai` do `requirements.txt`.

SQLite, resumo, intenção e API não mudam.

## Referências

- Implementação: `app/memory/long_term.py`, `app/memory/short_term.py`, `app/persistence/sqlite_store.py`
- Estratégia: [memory-strategy.md](memory-strategy.md) · Comparação: [memory-tools-comparison.md](memory-tools-comparison.md)
- Avaliação dos cenários: [conversation-evaluation.md](conversation-evaluation.md)
- Docs consultadas: LangMem (langchain-ai.github.io/langmem), Mem0 OSS (docs.mem0.ai), Letta Docker (docs.letta.com/guides/docker), Letta + Ollama (docs.letta.com/models/ollama)
