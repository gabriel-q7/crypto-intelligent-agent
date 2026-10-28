# Decisão: base vetorial — de FAISS para Chroma

| | |
|---|---|
| **Status** | Aceita e implementada |
| **Data** | 2026-10-26 |
| **Decisão** | Substituir o FAISS (via `langchain-community`) pelo Chroma (via `langchain-chroma`) como base vetorial da base de conhecimento local |
| **Decidido por** | Usuário (responsável pelo projeto), com análise técnica e validação registradas aqui |
| **Substitui** | Decisão inicial da Fase 2 (2026-10-19): FAISS, conforme o enunciado |
| **Impacto no enunciado** | **Desvio deliberado.** O enunciado da Fase 2 pede "FAISS para busca semântica local". O requisito funcional (busca semântica local, persistida, exposta como ferramenta do agente e separada dos dados de mercado) continua atendido; muda só a tecnologia |

## 1. Contexto

A base de conhecimento guarda conceitos estáveis (blockchain, Bitcoin, staking, DeFi/TVL, stablecoins, métricas e riscos) e é consultada:

- pela ferramenta `search_knowledge_base` do agente ReAct;
- pela rota `knowledge_retrieval` do roteador;
- pelos endpoints `GET /knowledge/search` e `POST /knowledge/ingest`.

O corpus é pequeno: 7 documentos, 18 trechos de até 800 caracteres. Os embeddings vêm do `nomic-embed-text`, rodando localmente no Ollama.

Todo o acesso à base vetorial fica isolado em `app/knowledge/store.py`. O resto da aplicação usa só a interface `VectorStore` do LangChain e as funções `build_index`, `load_index` e `search`.

## 2. Decisão inicial: FAISS

O FAISS foi escolhido no início da Fase 2 por ser o **requisito explícito do enunciado**.

| Aspecto | Implementação com FAISS |
|---|---|
| Pacotes | `langchain-community` 0.4.2 (classe `FAISS`) + `faiss-cpu` 1.15.1 |
| Índice | `IndexFlatL2` (distância euclidiana), em memória |
| Persistência | `save_local`: `index.faiss` (vetores) + `index.pkl` (documentos e metadados, serializados com **pickle**) em `data/faiss_index` |
| Carga | `FAISS.load_local(..., allow_dangerous_deserialization=True)` |
| Atualização | Só reconstruindo o índice inteiro |

Funcionou: ingestão real, busca correta ("o que é TVL" → `defi-metrics.md`), testes offline e de integração passando (`docs/stage-2-evidence.md`, seções 1–7).

## 3. Problemas observados com o FAISS

Surgiram durante a implementação e a validação, não antes:

1. **Dependência em descontinuação.** Ao importar a classe `FAISS`, o `langchain-community` emite este aviso, que aparecia em toda execução de testes:

   > `DeprecationWarning: langchain-community is being sunset and is no longer actively maintained. See https://github.com/langchain-ai/langchain-community/issues/674`

   O LangChain está migrando as integrações para pacotes dedicados (`langchain-ollama`, `langchain-chroma`...). **Não existe `langchain-faiss` no PyPI** (verificado em 2026-10-20). Manter FAISS no ecossistema LangChain significava depender de um pacote sem manutenção, ou escrever a integração com `faiss` diretamente.

2. **Desserialização com pickle.** O formato do LangChain para o FAISS guarda os documentos em `index.pkl`, e carregá-lo exige `allow_dangerous_deserialization=True`. Um arquivo `.pkl` adulterado executa código arbitrário ao ser carregado. Era aceitável só porque o índice é gerado localmente, mas era um risco documentado (`docs/architecture.md`, versão anterior).

3. **Sem atualização incremental nem metadados consultáveis.** Qualquer mudança no corpus exige reconstruir e regravar tudo, e não há filtro por metadados (ex.: por arquivo ou tema) sem código próprio. Com o corpus atual isso não pesa; pesaria com o crescimento previsto (memória de longo prazo, mais documentos).

## 4. Motivação da mudança

A troca foi **solicitada pelo usuário em 2026-10-26**. Os fatores técnicos que a sustentam:

| Fator | FAISS (antes) | Chroma (depois) |
|---|---|---|
| Integração LangChain | `langchain-community`, em descontinuação | `langchain-chroma`, pacote dedicado e mantido |
| Persistência | Arquivos `index.faiss` + `index.pkl` (pickle) | Banco embutido (`chroma.sqlite3`), sem pickle |
| Segurança na carga | `allow_dangerous_deserialization=True` | Não se aplica |
| Atualização | Reconstrução total | Inserção, atualização e remoção por id (`upsert`/`delete`) |
| Metadados | Só retornados | Filtráveis na consulta (`where`) |
| Organização | Um índice por diretório | Coleções nomeadas no mesmo banco |
| Distância | L2 (`IndexFlatL2`) | Cosseno, configurada na coleção (`hnsw:space=cosine`) |
| Caminho para escalar | Embutido apenas | Mesmo cliente embutido, ou servidor Chroma separado, sem mudar a interface |

Em resumo:
- a mudança **elimina a dependência em descontinuação e o risco do pickle**, que eram problemas presentes;
- ela **prepara a base para as próximas fases**, com atualização incremental, filtros por metadados, coleções separadas (ex.: memória de longo prazo) e opção de servidor;
- tudo isso sem mexer nas interfaces que o resto da aplicação usa.

## 5. Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Manter FAISS via `langchain-community` | Mantém a dependência em descontinuação e o pickle |
| FAISS direto (`faiss-cpu` sem LangChain) | Resolve a descontinuação, mas exige escrever do zero persistência de documentos, metadados e a adaptação para `VectorStore`; o pickle continuaria ou daria lugar a um formato próprio |
| Chroma **como servidor** (serviço no Compose) | Mais um container, porta e healthcheck para um corpus de 18 trechos. O modo embutido usa o mesmo cliente e pode virar servidor depois |
| Qdrant, Weaviate, Milvus, pgvector | Exigem serviço dedicado (ou Postgres); complexidade desproporcional ao corpus atual |
| **Chroma embutido persistente** | **Escolhida:** pacote dedicado, sem pickle, persistência e CRUD nativos, sem serviço extra |

## 6. Implementação

- `langchain-community` e `faiss-cpu` foram trocados por `langchain-chroma` 1.1.0 e `chromadb` 1.5.9, ambos compatíveis com o `langchain-core` 1.6.12 fixado na Fase 1, sem rebaixar nada.
- `chromadb.PersistentClient` grava em `VECTOR_STORE_DIR` (padrão `data/chroma`, diretório montado no Compose). A coleção `KNOWLEDGE_COLLECTION` (padrão `crypto_knowledge`) usa distância de cosseno.
- Reindexação (`POST /knowledge/ingest`): apaga e recria a coleção, com ids determinísticos (`arquivo.md#n`), para não deixar duplicatas nem trechos de arquivos removidos.
- O `manifest.json` foi mantido e passou a registrar `vector_store` e `collection`. Modelo de embedding diferente gera erro pedindo reindexação; coleção ausente gera erro explícito.
- A telemetria anônima do Chroma foi desligada no código (`anonymized_telemetry=False`) e no Compose (`ANONYMIZED_TELEMETRY=False`).
- Variáveis renomeadas: `FAISS_INDEX_DIR` virou `VECTOR_STORE_DIR` + `KNOWLEDGE_COLLECTION`.
- **Sem mudança de interface:** `KnowledgeSearcher`, a ferramenta `search_knowledge_base`, a rota `knowledge_retrieval` e os endpoints continuam iguais. O isolamento em `app/knowledge/store.py` limitou a mudança a um módulo.

## 7. Consequências

**Positivas**
- O aviso de descontinuação sumiu dos testes, e o projeto não depende mais do `langchain-community`.
- O carregamento via pickle deixou de existir.
- A base suporta atualização incremental, filtros por metadados e múltiplas coleções.

**Negativas (custos aceitos)**
- **Pacotes maiores:** a medição em `python:3.12-slim` com os `requirements.txt` reais deu FAISS 305 MB e 63 pacotes, contra Chroma **564 MB e 108 pacotes** (+259 MB, +45 pacotes). O `chromadb` traz `onnxruntime`, `grpcio`, `kubernetes` e OpenTelemetry, que este projeto não usa diretamente.
- **Telemetria por padrão:** o Chroma envia telemetria anônima se não for desligada. Foi desligada nos dois pontos.
- **Desvio do enunciado:** precisa ser justificado no relatório final, e é por isso que este documento existe.
- **Pontuações mudaram de escala:** distância de cosseno (0 a 2, menor = melhor) no lugar de L2. Os valores das evidências anteriores (FAISS) e posteriores (Chroma) não são comparáveis diretamente.

## 8. Validação (2026-10-26)

| Verificação | Resultado |
|---|---|
| Testes offline | 199 passed, sem o aviso de descontinuação |
| Ingestão com embeddings reais | 18 trechos em 3,5 s → `data/chroma/chroma.sqlite3` + `manifest.json` |
| `GET /health` | `degraded` sem coleção → `ok` após a ingestão; continua `ok` depois de reiniciar a API (persistência confirmada) |
| Buscas (distância de cosseno) | "o que é TVL" → `defi-metrics.md` 0,276 · "halving do bitcoin" → `bitcoin.md` 0,268 · "riscos do staking de ETH" → `ethereum-staking.md` 0,182 · "tipos de stablecoins" → `stablecoins.md` 0,266 (documento correto em 1º lugar nas quatro) |
| Rota de conhecimento e agente | Respostas a partir dos trechos corretos; o agente usou `search_knowledge_base` |
| Integração com embeddings reais | `test_knowledge_index_with_real_embeddings`: passed |

Achado da validação: dois trechos do mesmo arquivo geravam achados duplicados no `CryptoReport`. Isso foi corrigido com deduplicação por arquivo, coberta por teste.

## 9. Como reverter

A mudança é contida em `app/knowledge/store.py`, `requirements.txt`, nas variáveis de ambiente e no `compose.yaml`. Para voltar ao FAISS, restaure esses arquivos do commit `4d334e1` e reingira o corpus (`POST /knowledge/ingest`). Os testes de `tests/unit/test_memory_knowledge_trace.py` usam a interface pública e só exigem ajustar o nome do campo de diretório.

## Referências

- Implementação: [app/knowledge/store.py](../app/knowledge/store.py)
- Arquitetura: [docs/architecture.md](architecture.md), seção 6
- Evidências: [docs/stage-2-evidence.md](stage-2-evidence.md), seção 9
- Diário: [Report/fase-02-agente-ferramentas.md](../Report/fase-02-agente-ferramentas.md), seção 5.8
- Aviso de descontinuação: https://github.com/langchain-ai/langchain-community/issues/674
