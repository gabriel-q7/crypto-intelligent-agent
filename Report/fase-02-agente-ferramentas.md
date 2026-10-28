# Fase 2 — Agente com ferramentas, roteamento, memória e base vetorial

| | |
|---|---|
| **Período** | 2026-10-19 a 2026-10-26 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose 5.5.1, Ollama 0.40.6, inferência em CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (chat, thinking desligado, `num_ctx` 8192) e `nomic-embed-text` (embeddings) |
| **Versões** | `langchain-core` 1.6.12, `langchain-ollama` 1.1.0, `langchain-classic` 1.0.8, `langchain-chroma` 1.1.0, `chromadb` 1.5.9 (antes: `langchain-community` 0.4.2 + `faiss-cpu` 1.15.1), `fastapi` 0.150.0, `uvicorn` 0.54.0 |

## 1. Objetivo

Evoluir a chain da Fase 1 para um agente que:
- escolhe ferramentas e consulta dados reais (CoinPaprika);
- roteia tarefas e mantém contexto de conversa;
- busca conceitos numa base local (FAISS);
- produz respostas validadas.

Restrições:
- usar as APIs legadas exigidas pelo enunciado (`initialize_agent`, `ZERO_SHOT_REACT_DESCRIPTION`, `RouterChain`) sem comprometer a Fase 1;
- **nunca inventar dado de mercado**.

Os 10 critérios de aceite foram atendidos (`docs/stage-2-evidence.md`, seção 1).

## 2. Escopo

**Dentro:**
- cliente e ferramentas CoinPaprika;
- agente ReAct clássico;
- prompt PTCF com few-shot;
- chains sequencial, paralela e de conhecimento;
- roteamento moderno e legado;
- memória limitada;
- FAISS local;
- callbacks e logging;
- aplicação HTTP;
- testes e documentação.

**Fora (enunciado):** DeFiLlama e CoinGecko (a rota DeFi responde `unavailable`), memória persistente, planejamento autônomo.

## 3. Solução implementada

```
POST /agent  → CryptoAgent → initialize_agent(ZERO_SHOT_REACT) → ferramentas (CoinPaprika, FAISS) ┐
POST /route  → classificador (LLM | RouterChain legado | palavras-chave) → RunnableBranch          ├→ grounding → CryptoReport
               ├ market_analysis: identificar → buscar → validar → analisar (sequencial)            │
               ├ asset_comparison: identificar → [ativos ∥ global] → validar → comparar (paralela)  │
               ├ knowledge_retrieval: FAISS → resposta com base nos trechos                         │
               └ defi_analysis: indisponível                                                        ┘
POST /analyze, /analyze/batch, /analyze/stream → chain da Fase 1 (inalterada)
```

Detalhes por módulo em `docs/architecture.md`.

## 4. Decisões técnicas

| Decisão | Alternativas | Motivo |
|---|---|---|
| APIs legadas via `langchain-classic`, isoladas em `app/legacy/` | Rebaixar para LangChain 0.x; usar só `create_agent` | No LangChain 1.x o legado saiu do pacote `langchain`. O `langchain-classic` 1.0.8 é compatível com o `langchain-core` 1.6.12 da Fase 1, sem rebaixar nada. Isolamento permite trocar depois |
| `RouterChain` só para **classificar** | `MultiPromptChain` completo | `MultiPromptChain` exige destinos `LLMChain`; nossos destinos são chains LCEL com API |
| Saída montada por **código** (grounding), não pelo LLM | Pedir ao ReAct um "Final Answer" em JSON | Modelo 2B + protocolo textual = JSON frágil. Fontes, horários e achados vindos das observações reais eliminam a chance de inventar esses campos |
| Verificação de valores da resposta contra as observações | Confiar no prompt | Na Fase 1 vimos que prompt não garante comportamento |
| Ferramentas de entrada única com normalização | Ferramentas estruturadas | O zero-shot só aceita uma string |
| `nomic-embed-text` via Ollama | sentence-transformers (torch) | Reaproveita o container, 274 MB, sem PyTorch (o disco esteve no limite na Fase 1) |
| Memória própria, limitada por turnos e caracteres, com marcação de dados históricos | `ConversationBufferWindowMemory` | O zero-shot não tem slot de memória e a janela não limita tamanho |
| `OLLAMA_NUM_CTX=8192` | Padrão 4096 | O prompt do agente (cerca de 1.700 tokens antes das observações) mais histórico e observações passaria de 4096, com truncamento silencioso |
| Aplicação HTTP (FastAPI) | Scripts avulsos; CLI única | Decisão do usuário (seção 5.5) |
| Retry só para falhas transitórias | Retry geral | Resposta inválida com temperatura 0 tende a se repetir |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Compatibilidade das APIs legadas

- **Investigação:** instalação limpa em `python:3.12-slim` com as versões da Fase 1 fixadas.
- **Resultado:** `initialize_agent`, `AgentType`, `LLMRouterChain`, `RouterOutputParser` e `ConversationBufferWindowMemory` importam de `langchain_classic`; FAISS de `langchain_community`.
- **Achado posterior:** o `langchain-community` emite aviso de **descontinuação** ("is being sunset"), e não existe `langchain-faiss` no PyPI. A dependência ficou restrita a `app/knowledge/store.py` e foi registrada como risco.

### 5.2 Comportamento real da CoinPaprika

Investigado com chamadas reais antes de codificar:
- `/search` sem barra final responde 301;
- id inexistente responde 404;
- **OHLCV histórico responde 402 no plano gratuito**, com `retry-after` de cerca de 12 dias;
- a cota é de 20 mil chamadas por mês;
- `/global` informa `last_updated` em epoch.

Consequências no design:
- 402 vira `plan_restricted`, sem retry;
- 429 só é repetido quando o `Retry-After` é de até 10 s;
- não há série histórica nesta fase (documentado no PTCF como limitação da fonte).

### 5.3 Primeira execução real do agente

O `gemma4:e2b` seguiu o protocolo ReAct logo na primeira execução:
- chamou `get_market_data("BTC")`, e a ferramenta resolveu o símbolo para `btc-bitcoin`;
- respondeu com o valor real, a fonte e o horário;
- a verificação de grounding confirmou os números.

Tempo total: 48 s (30 s só para o modelo ler o prompt de cerca de 1.700 tokens).

### 5.4 Comparações erradas do modelo: três iterações

| # | Mudança | Resultado real |
|---|---|---|
| 1 | Resumo livre do LLM (valores verificados) | Valores certos, conclusão errada: "o Bitcoin apresentou a maior variação negativa" (o ETH caiu mais); em outra execução, "o Ethereum apresenta capitalização maior" |
| 2 | Rankings (variação 24h e market cap) calculados no código e enviados ao modelo | **Repetiu o erro**: "o Bitcoin apresentou a maior queda" com o ranking certo no prompt |
| 3 | Na rota de comparação, a conclusão (`answer`) é montada pelo código; o resumo do LLM é descartado; os `key_points` verificados ficam | Correto: "SOL -0,79% > BTC -0,84% > ETH -1,07%" (2026-10-26) |

**Aprendizado:** a verificação de grounding confere *valores*, não *conclusões*. Para modelos pequenos, ordenação e comparação devem ser feitas pelo código.

Correções menores no grounding, achadas com respostas reais:
- "$1.638 trilhões" (correto) era removido, porque "1.638" era lido como milhar;
- "caiu 1,11%" não batia com -1,11, porque o sinal vem da palavra;
- o regex capturava a vírgula final da frase;
- o LLM colocava preços no campo `uncertainties`, que não era verificado.

### 5.5 Formato da aplicação: scripts → CLI única (descartada) → HTTP

- **Inicial:** um script por tarefa em `scripts/`, seguindo o "executar por scripts Python" da Fase 1.
- **Questionamento do usuário:** os vários `__init__.py` seriam consequência dos scripts? Resposta: os `__init__.py` são padrão de pacote Python e ficam em qualquer formato. Só o `scripts/__init__.py` vinha da escolha de scripts. O desvio real eram 8 pontos de entrada com inicialização repetida, sem entrada única e sem instalação.
- **Proposta:** CLI única com subcomandos. Aprovada e depois **cancelada pelo usuário** antes da implementação.
- **Decisão final (usuário): serviço HTTP com FastAPI.**
  - `app/main.py` com contêiner de dependências criado no ciclo de vida da aplicação;
  - 10 endpoints;
  - `scripts/` removido;
  - o benchmark virou módulo de manutenção (`python -m app.services.benchmark_runner`), por levar de 10 a 40 min.
- **Ajuste no stream:** a primeira versão de `/analyze/stream` acumulava os parciais e só enviava no fim. Foi corrigida para enviar cada parcial assim que gerado.

### 5.6 Exemplos do Swagger

O Swagger mostrava `"question": "string"`. Foram criados exemplos reais e nomeados por cenário (`app/api/examples.py`). Um teste valida cada exemplo contra o modelo da requisição, para que não fiquem inválidos se os modelos mudarem.

### 5.7 Retry de LLM

As etapas JSON das chains ganharam `with_retry` para falhas de conexão com o Ollama (duas tentativas). Erros de validação não são repetidos. Ollama inacessível após o retry responde `503`. O agente ReAct não pode usar o wrapper, porque o legado exige `BaseLanguageModel`; nele, a falha vira `CryptoReport` `failed`.

### 5.8 Troca de FAISS por Chroma

- **Decisão do usuário (2026-10-26):** usar Chroma como base vetorial no lugar do FAISS. O enunciado pedia FAISS; a mudança é um desvio deliberado e registrado.
- **Registro completo da decisão:** [docs/vector-store-decision.md](../docs/vector-store-decision.md) (contexto, problemas do FAISS, motivação, alternativas, consequências, validação e como reverter).
- **Por que mudar (problemas observados com o FAISS durante a fase):**
  1. `langchain-community` em descontinuação, com aviso em toda execução; não existe `langchain-faiss`;
  2. persistência via pickle, que exigia `allow_dangerous_deserialization=True`;
  3. sem atualização incremental nem filtro por metadados, o que pesaria com o crescimento previsto da base (memória de longo prazo).
- **Alternativas avaliadas:**
  - manter FAISS via `langchain-community`;
  - FAISS puro;
  - Chroma como servidor;
  - Qdrant, Weaviate, Milvus, pgvector.

  Escolhido: **Chroma embutido persistente** (pacote dedicado, sem pickle, CRUD e metadados nativos, sem serviço extra).
- **Investigação:** `langchain-chroma` 1.1.0 (pacote dedicado) + `chromadb` 1.5.9 instalam com `langchain-core` 1.6.12 fixado.
- **Custo medido (2026-10-28, `python:3.12-slim` com os `requirements.txt` reais):** FAISS 305 MB / 63 pacotes → Chroma 564 MB / 108 pacotes (+259 MB). O `chromadb` traz `onnxruntime`, `grpcio`, `kubernetes` e OpenTelemetry, que o projeto não usa diretamente.
- **Modo de uso:** Chroma embutido persistente (`PersistentClient`) em `data/chroma`, montado no Compose, sem serviço extra; coleção com distância de cosseno; telemetria anônima desligada.
- **Reindexação:** a coleção é apagada e recriada, com ids determinísticos por trecho, para não acumular duplicatas.
- **Efeitos colaterais positivos:**
  - removeu o `langchain-community`, cuja descontinuação estava registrada como risco em 5.1;
  - removeu o carregamento do índice via pickle (`allow_dangerous_deserialization`), um risco de segurança;
  - as interfaces (`KnowledgeSearcher`, ferramenta, rota, endpoints) não mudaram, porque o acesso à base já estava isolado em `app/knowledge/store.py`.
- **Validação real:** ingestão em 3,5 s; as 4 buscas de teste trouxeram o documento correto em 1º lugar; rota e agente responderam a partir dos trechos; 199 testes offline e o teste de integração passaram (`docs/stage-2-evidence.md`, seção 9).
- **Achado:** achados de conhecimento duplicados (dois trechos do mesmo arquivo), corrigidos com deduplicação.

## 6. Evidências e métricas

Detalhes em `docs/stage-2-evidence.md`.

| Verificação | Resultado |
|---|---|
| Testes offline | 196 passed (~4 s) |
| Integração + rede | 11 passed (340 s) |
| `GET /health/coinpaprika` | 7/7 |
| Agente ReAct (ETH) | `complete`, 32 s, valores e horários reais |
| Continuação na mesma sessão | Nova consulta à API, sem reaproveitar valor antigo |
| Comparação de 3 ativos (paralela) | `complete`, 67 s, conclusão correta |
| `RouterChain` legado | Classificou corretamente |
| Rota DeFi | `unavailable` explícito |

## 7. Limitações conhecidas

Ver `docs/reliability-decisions.md` (seção 6) e `docs/stage-2-evidence.md` (seção 7). As principais:
- a verificação confere valores, não conclusões, fora da rota de comparação;
- percentuais são comparados em módulo;
- a latência em CPU é de 15 a 70 s;
- a memória vive só no processo;
- na pergunta "Esse ativo está caro?", o agente não pediu o ativo como no exemplo few-shot.

## 8. Aprendizados

- **Investigar a API real antes de codificar** evitou suposições erradas: o 301 do `/search`, o 402 do histórico e o `retry-after` de dias mudaram o design de retries.
- **Montar a saída por código** (fontes, horários, achados, status) é mais confiável que pedir JSON a um modelo pequeno em protocolo textual.
- **Ordenação e comparação não devem ser delegadas a modelos pequenos.** Nem com o ranking no prompt o modelo acertou.
- **Janela de contexto é requisito funcional:** o padrão de 4.096 tokens truncaria o agente em silêncio.
- **Isolar o legado** reduziu o custo de cumprir o enunciado. A troca para LangGraph ou `create_agent` afeta só `app/legacy/`.
- **Exemplos de documentação também são código:** sem teste, eles ficam inválidos.

## 9. Pendências e insumos para a próxima fase

- Integrar DeFiLlama (rota `defi_analysis`) e CoinGecko (séries históricas, que o plano gratuito da CoinPaprika não oferece).
- Migrar o agente para LangGraph ou `create_agent` com tool calling nativo, com ferramentas de argumentos tipados e retry no LLM.
- Executar o plano da Fase 1 (`requested_metrics` → ferramentas), o planejamento hierárquico.
- Memória persistente (sessões) e de longo prazo.
- ~~Substituir o `langchain-community`~~ — resolvido com a troca para Chroma (5.8).
- Avaliar se o few-shot de "informação ausente" precisa de reforço, ou de uma checagem determinística de "sem ativo".
