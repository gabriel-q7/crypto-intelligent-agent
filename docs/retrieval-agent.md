# Retrieval Agent e Query Rewriting (Fase 4)

Agente especializado de recuperação sobre a **coleção Chroma existente** (`crypto_knowledge`), usado pelo agente principal como ferramenta `search_knowledge_base`. A única técnica de transformação de consulta é a **Query Rewriting**: não há Multi-Query nem HyDE.

Código:
- `app/retrieval/rewriter.py`: `QueryRewriter`;
- `app/retrieval/agent.py`: `RetrievalAgent`;
- `app/tools/retrieval_tool.py`: ferramenta.

## 1. Infraestrutura reutilizada (sem mudança)

| Item | Valor | Origem |
|---|---|---|
| Coleção | `crypto_knowledge` (18 trechos, 7 arquivos de `knowledge/corpus/`) | `KNOWLEDGE_COLLECTION` |
| Diretório | `data/chroma` | `VECTOR_STORE_DIR` |
| Embeddings | `nomic-embed-text` (Ollama) | `EMBEDDING_MODEL` |
| Distância | Cosseno (`hnsw:space=cosine`): 0 = idêntico | `app/knowledge/store.py` |
| Acesso | `KnowledgeSearcher.index()`: abre a coleção uma vez, sob demanda | Fase 2 |

O Retrieval Agent:
- **não** reindexa, **não** recria e **não** apaga a coleção (só `similarity_search_with_score`);
- **não** acessa a coleção `conversation_memory` do Mem0, que fica em outro diretório (`data/memory_chroma`).

`GET /knowledge/search` e `POST /knowledge/ingest` continuam como antes.

## 2. Query Rewriting

`QueryRewriter.rewrite(question) → QueryRewrite`, com o Ollama configurado (`OLLAMA_MODEL`) em modo JSON e saída validada por Pydantic:

```json
{"main_concept": "...", "intent": "...", "rewritten_query": "...", "added_terms": ["..."]}
```

O prompt instrui o modelo a:
1. identificar conceito e intenção;
2. escrever **uma** consulta com a terminologia que os documentos usam (termo técnico em português e em inglês, sinônimos, nome do mecanismo);
3. listar em `added_terms` os termos específicos que um documento sobre o assunto deveria conter;
4. preservar sentido e restrições, sem trocar o assunto nem acrescentar fatos, ativos ou números;
5. **não** acrescentar termos genéricos ("blockchain", "criptoativos", "mercado").

O único exemplo few-shot é de um tema **fora** do corpus ("rug pull"), para não ensinar a resposta dos casos avaliados. Uma versão anterior usava "halving" como exemplo e contaminava o experimento; ela foi trocada antes das medições finais (ver [phase-4-evidence.md](phase-4-evidence.md), seção 1.6).

## 3. Política do agente

```
pergunta
 │
 ├─ busca original (top-k + varredura de toda a coleção para medir distâncias)
 │      └─ melhor trecho ≤ RETRIEVAL_REWRITE_DISTANCE (0,33) ─────→ usa a original, sem LLM
 │
 ├─ QueryRewriter (LLM) ── falhou ──────────────────────────────→ usa a original
 ├─ remove ativos que a pergunta não cita (strip_unrequested_assets)
 │
 ├─ busca com a consulta reescrita (o ranking dela passa a valer)
 │      └─ algum trecho válido? ── não ─────────────────────────→ usa a original
 │
 └─ evidência = trechos válidos:
        distância à consulta usada ≤ RETRIEVAL_MAX_DISTANCE (0,38)
        e, se vieram da reescrita, contêm ≥ 1 palavra âncora
        nenhum → status no_relevant_documents
```

**Palavras âncora**: palavras de `main_concept` + `added_terms` com 5 letras ou mais, sem acento e fora de uma lista de termos genéricos (`GENERIC_TERMS`). Elas casam com palavras inteiras do trecho.

Por que existem: no diagnóstico real, a reescrita de "Como funciona a queima de moedas?" (assunto ausente do corpus) acrescentou termos genéricos e levou a distância de 0,437 para 0,183 em trechos irrelevantes. Sem a âncora, o agente teria recebido "evidência" fabricada pela própria reescrita.

**Por que a distância à pergunta original não é o critério**: ela foi testada e descartada. Na pergunta "O que é impermanent loss?", o trecho certo (que fala em "perda impermanente") ficou **mais longe** da pergunta (0,455) que trechos errados (0,387). Quando o usuário usa outro vocabulário, a distância à pergunta original penaliza exatamente o trecho que a reescrita deveria trazer. Ela continua no resultado (`question_distance`), só para avaliação.

**Limiares**: calibrados com medições reais (seção 1.7 da evidência). O melhor trecho de buscas boas ficou entre 0,20 e 0,31; falhas de vocabulário, entre 0,34 e 0,39; assunto ausente, acima de 0,43. Os limiares dependem do modelo de embedding e do corpus, e são configuráveis (`RETRIEVAL_REWRITE_DISTANCE`, `RETRIEVAL_MAX_DISTANCE`).

**Modos** (`RETRIEVAL_REWRITE_MODE` ou campo `mode` em `POST /knowledge/retrieve`):
- `auto`: padrão;
- `always`: reescreve mesmo quando a original é boa (avaliação);
- `never`: sem reescrita (linha de base).

## 4. Saída

`RetrievalResult`:
- `status` (`ok` ou `no_relevant_documents`);
- `original_query`, `rewritten_query`, `rewrite` (saída completa do LLM), `rewrite_applied`, `decision` (texto com as distâncias e o motivo), `query_used`, limiares;
- `documents`: evidência. Cada trecho traz `chunk_id`, `source`, `title`, `score`, `question_distance`, `anchor_terms`, `rank`, `content` e `metadata` (os metadados originais do Chroma, preservados);
- `original_documents` e `rewritten_documents`: os dois rankings completos, para comparação.

A pergunta original e a reescrita ficam sempre disponíveis para depuração e avaliação.

## 5. Integração

- **Agente principal (`POST /agent`):** `search_knowledge_base` chama `RetrievalAgent.retrieve`. A observação mantém o formato da Fase 2 (`data` = trechos) e acrescenta `retrieval` com a decisão de reescrita. Sem evidência, devolve `ok=false, error.kind=no_relevant_documents`, e a regra 7 do PTCF manda dizer que a base local não cobre o assunto.
- **Grounding:** os trechos viram achados "Base de conhecimento: '<título>' (<arquivo>)" e fontes `search_knowledge_base`.
- **Endpoint de avaliação:** `POST /knowledge/retrieve {question, mode, k}` devolve o `RetrievalResult` completo.
- **Fases 2 e 3:** `/route` e `/conversations` continuam usando a busca simples (`KnowledgeSearcher`). A Fase 4 não alterou essas rotas.

## 6. Limitações

- **Chunking existente** (não alterado nesta fase, por escopo):
  - `risks.md#12` contém só o título "# Riscos em criptoativos" e aparece em muitas buscas;
  - `defi-metrics.md#6` mistura DEX/AMM, empréstimos e métricas, o que dilui o embedding da frase sobre perda impermanente.

  Reescrever a consulta não corrige isso.
- **Distância não é relevância:** trechos genéricos podem ficar abaixo de 0,38 numa busca original fraca. O agente recebe esses trechos como evidência, e só a instrução do prompt evita afirmações que eles não sustentam.
- **A âncora pode recusar trecho relevante:** se o LLM não listar em `added_terms` a palavra que o documento usa, a reescrita é descartada e fica a busca original.
- **Variação do modelo:** mesmo com temperatura 0 e o mesmo prompt, o Ollama em CPU gerou reescritas diferentes entre execuções (sem `seed` fixo). Em 5 execuções seguidas do caso halving, o trecho certo ficou em 1º quatro vezes e em 2º uma vez (evidência, seção 1.2).
- **Ativos acrescentados pelo LLM:** numa execução real, a reescrita ganhou "bitcoin ethereum", que a pergunta não citava, e o trecho de Ethereum passou à frente. Agora esses ativos são removidos (`removed_terms` no resultado). Às vezes isso custa precisão: com "Bitcoin" na consulta, o halving chegou a 0,286; sem ele, fica a 0,357. Ainda assim, o trecho fica em 1º.
- **Custo:** a reescrita leva de 10 a 13 s em CPU com `gemma4:e2b`. Buscas boas não pagam esse custo (modo `auto`).
