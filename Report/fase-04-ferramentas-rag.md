# Fase 4 — Ferramentas externas e integração com o RAG existente

| | |
|---|---|
| **Período** | 2026-11-04 (horário local; as execuções reais estão em UTC, 2026-11-05 00:30–01:30) |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose, Ollama, CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (thinking desligado) e `nomic-embed-text` |
| **Versões novas** | `sqlglot` 30.27.0. Mantidos: `langchain-core` 1.6.12, `langchain-classic` 1.0.8, `chromadb` 1.5.9, `mem0ai` 2.2.1, `fastapi` 0.150.0 |

## 1. Objetivo

Ampliar o agente ReAct existente para combinar:
- dados de mercado ao vivo (CoinPaprika, já existente);
- consultas estruturadas sobre um banco SQLite de snapshots, via **Text-to-Query** com SQL validado;
- recuperação de documentos na coleção Chroma existente, via **Retrieval Agent** com **Query Rewriting**;
- cálculo determinístico.

Tudo sem reconstruir a ingestão do corpus, sem novos provedores e preservando as Fases 1–3.

**Critério de conclusão:**
- ferramentas catalogadas e classificadas;
- banco populado com dados reais;
- SQL seguro e validado;
- ao menos uma falha real de recuperação diagnosticada e comparada antes/depois da reescrita;
- testes executados;
- documentação atualizada.

## 2. Escopo

**Dentro:**
- catálogo de ferramentas;
- banco SQLite de snapshots (schema, coleta, deduplicação, cobertura);
- Text-to-Query (geração, validação por parser, execução protegida, status explícitos);
- Retrieval Agent e Query Rewriting;
- integração ao ReAct;
- endpoints de operação e avaliação;
- testes unitários e de integração;
- documentação.

**Fora (e por quê):**
- **CoinGecko e DeFiLlama:** proibidos pelo enunciado nesta fase;
- **Multi-Query e HyDE:** só Query Rewriting, por enunciado;
- **mudança no chunking ou reindexação:** proibido ("não reconstruir a ingestão"). Os problemas de chunking encontrados ficaram registrados como limitação;
- **agendador de coleta:** o enunciado pede gatilho simples;
- **migração do ReAct legado:** o enunciado pede integrar ao agente existente.

## 3. Solução implementada

```
POST /agent → ReAct (PTCF, regra 6: pergunta → ferramenta)
   ├─ CoinPaprika ao vivo: search_crypto_asset, get_market_data, get_market_details, get_global_market
   ├─ query_market_database → TextToQueryService → SQL validado → data/market.sqlite3
   ├─ search_knowledge_base → RetrievalAgent (original → [fraca] reescrita → evidência) → crypto_knowledge
   └─ calculate_percent_change (local)
POST /market-data/ingest → MarketDataIngestor → CoinPaprikaClient (reuso) → SQLite
```

Módulos novos:
- `app/market_data/` (`store`, `ingestion`, `sql_guard`, `text_to_query`);
- `app/retrieval/` (`rewriter`, `agent`);
- `app/tools/{market_db_tool, retrieval_tool, calculation_tools}.py`;
- `app/api/phase4_routes.py`.

Detalhes:
- `docs/tools-catalog.md`;
- `docs/text-to-query.md`;
- `docs/retrieval-agent.md`;
- `docs/architecture.md` (seção 9).

## 4. Decisões técnicas

| Decisão | Alternativas | Motivo |
|---|---|---|
| Banco SQLite **separado** (`data/market.sqlite3`) | Tabelas no SQLite das conversas | Enunciado: não misturar responsabilidades; apagar ou recriar um não afeta o outro |
| Schema = campos reais de `MarketTicker`, `NULL` quando ausente | Schema "ideal" com OHLCV histórico | Não inventar métricas; histórico OHLCV dá 402 no plano gratuito |
| Deduplicação por `(asset_id, source_updated_at)` (índice único parcial) | Por horário local | O mesmo dado da fonte coletado duas vezes não é observação nova |
| Validação em **duas camadas**: `sqlglot` (antes) + `mode=ro` + `set_authorizer` + `progress_handler` (durante) | Só regex; só parser | O enunciado pede parser. O authorizer pega o que o parser deixa passar (`random()`, comprovado em teste) |
| Literais do SQL do LLM viram parâmetros `?` | Executar o texto do LLM | Enunciado: preferir parametrização; o texto executado não carrega valores |
| Período detectado por **regra** (`find_period`, Fase 3), não pelo LLM | Campo do LLM | Lição das Fases 1–3: regra é mais confiável que o modelo pequeno |
| `insufficient_coverage` **sem executar** SQL | Executar e devolver o que houver | Responder "90 dias" com 5 minutos de dados seria enganoso |
| Ferramenta de retrieval com o **mesmo nome** da Fase 2 (`search_knowledge_base`) | Nome novo | Prompt, regras e grounding já a conhecem; formato compatível |
| Reescrita só quando a busca original é fraca (`auto`, distância > 0,33) | Sempre reescrever | Enunciado: evitar reescrita sem valor; economiza 10–13 s de LLM por busca boa |
| Evidência = distância à consulta usada ≤ 0,38 **+ palavra âncora** nos trechos da reescrita | Menor distância vence; distância à pergunta original | As duas alternativas foram testadas com dados reais e falharam (seção 5.2) |
| View `snapshot_history` com `symbol`/`name` | Exigir JOIN do modelo | O `gemma4:e2b` errou o JOIN (seção 5.3) |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Primeira escolha de exemplo few-shot contaminava o experimento

- **Sintoma:** o exemplo do prompt de reescrita era justamente "recompensa cai pela metade → halving", um dos casos avaliados.
- **Correção:** o exemplo foi trocado por um tema fora do corpus ("rug pull"), antes das medições finais.
- **Efeito:** sem o exemplo, o modelo não escreve mais "halving", mas a reescrita ainda melhora o ranking (`docs/phase-4-evidence.md`, seção 1.2). O resultado registrado é o honesto, sem a ajuda do exemplo.

### 5.2 Política de escolha entre busca original e reescrita (três versões)

| Versão | Regra | Falha observada com dados reais |
|---|---|---|
| 1 | Fica a busca com o melhor trecho mais próximo | "Queima de moedas" (fora do corpus): a reescrita acrescentou termos genéricos e levou a distância de 0,437 para 0,183 em trechos **irrelevantes**. Isso fabricaria evidência |
| 2 | Relevância = distância à **pergunta original** (piso de 0,42; ranking da reescrita) | Funcionou no halving, mas em "impermanent loss" os trechos de DeFi trazidos pela reescrita ficaram **mais longe** da pergunta (0,425–0,455) que trechos errados (0,387) e foram descartados. Usar essa distância para **comparar** as buscas falha por construção: a original é ranqueada por ela |
| 3 (final) | Distância à consulta usada ≤ 0,38 + **palavra âncora** da reescrita presente no trecho (prefixo, sem termos genéricos) | Barra a evidência fabricada (queima) e aceita a tradução de vocabulário (halving). Limitações na seção 7 |

### 5.3 Erros semânticos do gerador SQL

| Sintoma (execução real) | Causa | Correção |
|---|---|---|
| `... WHERE symbol IN ('ADA', ..., 'USDT')` sem filtro pedido, **omitindo XRP** no ranking por market cap | A lista de símbolos estava no prompt, e o modelo a copiou como filtro | Prompt passa só a quantidade de ativos e a janela; regra "só filtre por symbol quando a pergunta citar o ativo" |
| `SELECT AVG(price_usd) FROM market_snapshots WHERE symbol = 'BTC'` | `market_snapshots` não tem `symbol` (exige JOIN). **O validador recusou** antes da execução (`invalid_query`) | View `snapshot_history` com `symbol`/`name`, mais um exemplo de média histórica |

Depois das correções, a bateria final de 9 perguntas teve 9 status corretos (`docs/phase-4-evidence.md`, seção 2.1).

### 5.4 CTE recursiva negada pelo authorizer com erro genérico

- **Sintoma:** o teste de timeout dava `execution_error` em vez de `timeout`.
- **Causa:** o SQLite pede autorização da ação `SQLITE_RECURSIVE`, e "not authorized" chega como `DatabaseError`, não como `OperationalError`.
- **Correção:** a ação recursiva (só leitura) passou a ser permitida, e "not authorized" é tratado em qualquer `DatabaseError`.

### 5.5 Agente real: pergunta combinada sem documento e entrada resumida

Achados da 1ª rodada com o agente real (`docs/phase-4-evidence.md`, seção 4.2):

- **Pergunta combinada (mercado + PoW × PoS):**
  - **Sintoma:** o agente só chamou `get_market_data`, explicou o conceito com o conhecimento do modelo e devolveu `complete`.
  - **Correção:** regra no PTCF e guarda determinística: pedido conceitual sem trecho recuperado vira `partial` com incerteza.
  - **Resultado:** o modelo continua sem buscar, mas a falta de base documental agora fica explícita.
- **Halving pelo agente:**
  - **Sintoma:** o ReAct enviou "recompensa dos mineradores", sem "cai pela metade". A busca não acionou a reescrita, e a resposta disse que o texto não tratava da queda.
  - **Correção:** a descrição da ferramenta pede a pergunta completa.
  - **Resultado:** reescrita aplicada e resposta correta citando o halving.
- **"Maior que 5%" removido da resposta:**
  - **Causa:** o limiar do filtro não estava nas observações.
  - **Correção:** os `params` do SQL entram na observação.

### 5.6 Regressão completa: duas falhas reais

1ª execução da suíte de integração completa: **16 passed, 2 failed** (`docs/evidence/phase-4/regression-run1.log`).

- **Fase 3 (`test_live_multi_turn_restart_and_isolation`):**
  - **Causa:** mudança da fonte, não do código. Em 2026-11-05 a CoinPaprika passou a devolver `percent_change_30d` real (BTC -6,00%), e a resposta trouxe "Variação 30d" em vez da limitação "30 dias", que o teste exigia.
  - **Correção:** o teste aceita os dois comportamentos corretos; no Text-to-Query, 30 dias passou a ser janela do provedor (`PROVIDER_WINDOWS_DAYS = {1, 7, 30}`); a documentação que dizia "30d sempre NULL" foi corrigida.
- **Fase 4 (halving):**
  - **Causa:** com o mesmo prompt e temperatura 0, o Ollama gerou outra reescrita, acrescentando "bitcoin ethereum". O trecho de Ethereum subiu para 1º.
  - **Correção:** `strip_unrequested_assets` remove da reescrita os ativos que a pergunta não cita, e o teste passa a exigir melhora para o top-2.
  - **Medição:** em 5 execuções seguidas, o trecho ficou em 1º quatro vezes e em 2º uma vez.

### 5.7 Testes rodavam o código antigo da imagem

- **Sintoma:** `ModuleNotFoundError` para os módulos novos.
- **Causa:** a imagem copia o código no build, e o Compose só monta `./data`.
- **Correção:** durante o desenvolvimento, os testes rodaram com `-v $PWD:/app`. A imagem final foi reconstruída.

## 6. Evidências e métricas

Detalhes e saídas completas: `docs/phase-4-evidence.md` (dados brutos em `docs/evidence/phase-4/`).

| Verificação | Resultado |
|---|---|
| Testes offline (Fases 1–4) | **312 passed** (52 novos da Fase 4) |
| Integração Fase 4 (Ollama + CoinPaprika + Chroma reais) | **6 passed** em 169,5 s |
| Integração completa (Fases 1–4), 1ª execução | 16 passed, 2 failed (seção 5.6) |
| Integração Fases 3 e 4 após correções | **7 passed** em 595,5 s |
| Coleta real | 10 ativos inseridos; repetição imediata = `duplicates`; 3ª coleta: 8 novos + 2 duplicados (19 snapshots) |
| Text-to-Query real (9 perguntas) | 9 status corretos: 5 `ok`, 1 `no_matching_records`, 1 `insufficient_coverage`, 2 `unsupported` |
| Diagnóstico de recuperação (halving) | Trecho relevante: 3º → 1º lugar (MRR 0,33 → 1,0); em 5 execuções seguidas: 1º em 4, 2º em 1 |
| Agente real (5 perguntas) | Ferramenta certa nas 4 perguntas de um só tipo (mercado, banco, conceito, halving com reescrita); a combinada saiu `partial` (sem busca documental, declarada) |

## 7. Limitações conhecidas

- **Chunking existente:**
  - `risks.md#12` só tem o título e aparece em muitas buscas;
  - `defi-metrics.md#6` dilui a frase sobre perda impermanente.

  Reescrever a consulta não corrige isso.
- **Distância não é relevância:** trechos genéricos ficam perto de perguntas fracas, e os limiares são empíricos (corpus de 18 trechos).
- **A palavra âncora é heurística:** palavras de domínio ("liquidez", "perda") deixam passar trechos só parcialmente relevantes (caso impermanent loss).
- **Histórico de mercado depende de coletas reais:** perguntas de 90 dias ou mais dão `insufficient_coverage`.
- **Saída do LLM não é estritamente determinística** em CPU, mesmo com temperatura 0: a reescrita variou entre execuções. Os testes de integração usam critérios de melhora, não igualdade.
- **O SQL do `gemma4:e2b` é correto em filtros e rankings simples;** consultas temporais complexas são frágeis. A validação garante segurança, não semântica.

## 8. Aprendizados

- **Medir antes de calibrar:** os limiares e a política final só saíram das distâncias reais. As duas políticas "óbvias" (menor distância; distância à pergunta original) falharam em casos concretos.
- **Reescrita também introduz risco:** termos genéricos acrescentados pelo LLM criam relevância aparente. A transformação de consulta precisa de uma checagem de que a evidência tem a ver com o conceito.
- **Few-shot pode contaminar a avaliação:** um exemplo igual ao caso de teste mascara o comportamento real do modelo.
- **Parser e authorizer se complementam:** cada um pegou casos que o outro não pegaria (função conhecida pelo parser, mas negada pelo SQLite; coluna inexistente recusada antes de chegar ao banco).
- **Contexto demais atrapalha o modelo pequeno:** a lista de ativos no prompt virou filtro indevido.
- **Fontes externas mudam:** a premissa "30d = 0 no plano gratuito", verificada na Fase 3, deixou de valer uma semana depois. Testes de integração precisam aceitar os dois comportamentos legítimos, e a documentação deve datar esse tipo de fato.

## 9. Pendências e insumos para a próxima fase

- **Chunking por item de lista** em `risks.md` e uma seção própria para perda impermanente, se uma fase futura permitir mexer na ingestão.
- **Coleta agendada** para formar histórico (hoje é manual).
- **Cobertura por ativo** no Text-to-Query.
- **Migração do ReAct legado** para tool calling nativo (LangGraph), com ferramentas de entrada estruturada no lugar de texto único.
- **DeFiLlama/CoinGecko:** `unsupported` no Text-to-Query e `unavailable` nas rotas DeFi indicam onde entram.
