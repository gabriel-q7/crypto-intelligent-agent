# Evidências — Fase 4

Ambiente:
- Linux, Docker Compose, CPU (12 threads);
- Ollama com `gemma4:e2b` (thinking desligado, temperatura 0) e `nomic-embed-text`;
- Chroma 1.5.9 (coleção existente `crypto_knowledge`, 18 trechos, criada em 2026-10-26 e **não reindexada** nesta fase);
- CoinPaprika real, plano gratuito;
- `sqlglot` 30.27.0.

Execuções reais em **2026-11-05 (UTC)**, pela API em Docker (`make up`). Os dados brutos das respostas estão em [`docs/evidence/phase-4/`](evidence/phase-4/).

Níveis de evidência, sempre identificados:
- **[offline]** testes com LLM roteirizado, CoinPaprika simulada (`httpx.MockTransport`) e índice falso;
- **[integração]** testes automatizados com Ollama, CoinPaprika e Chroma reais;
- **[API real]** chamadas aos endpoints da aplicação em execução.

Resumo dos testes: seção 5.

## 1. Diagnóstico real de falha de recuperação [API real]

Procedimento:
1. `POST /knowledge/retrieve` com `mode=never` (linha de base, sem reescrita);
2. a mesma pergunta com `mode=auto` (política final);
3. inspeção do conteúdo real dos trechos (`collection.get` por id, só leitura).

Nenhum documento do corpus foi alterado.

### 1.1 Perguntas testadas

As perguntas foram testadas até achar falhas reais. Os scores são distâncias de cosseno (menor = mais próximo).

| Pergunta | Top-3 original (`never`) | O corpus tem o assunto? | Diagnóstico |
|---|---|---|---|
| "Explique como funciona proof of stake" | `ethereum-staking.md#8` 0,301; `blockchain-basics.md#3` 0,317; `ethereum-staking.md#7` 0,380 | Sim | **Sem falha**: trechos certos no topo. Reescrita não acionada (0,301 ≤ 0,33) |
| "Por que a recompensa dos mineradores cai pela metade?" | `blockchain-basics.md#3` 0,345; `bitcoin.md#0` 0,351; `bitcoin.md#1` 0,353 | Sim (`bitcoin.md#1`, seção Halving) | **Falha de vocabulário**: a pergunta não diz "halving" e o trecho que responde fica em 3º |
| "O que é impermanent loss?" | `blockchain-basics.md#4` 0,387; `risks.md#14` 0,404; `stablecoins.md#17` 0,411 | Sim: "perda impermanente" em `defi-metrics.md#6` | **Falha de vocabulário** (inglês × português) **+ chunking**: nenhum trecho de DeFi no top-3 |
| "O que acontece se eu perder minha seed phrase?" | `blockchain-basics.md#3` 0,363; `blockchain-basics.md#2` 0,367; `risks.md#12` 0,369 | Parcial: "perda das chaves privadas" em `risks.md#13` | **Falha de vocabulário + chunking**: `risks.md#12` contém **só o título** "# Riscos em criptoativos" |
| "O que significa slippage numa troca?" | `bitcoin.md#0` 0,383; `blockchain-basics.md#4` 0,397; `risks.md#12` 0,406 | Não (só "impacto em ordens grandes" em `risks.md#13`) | Assunto praticamente ausente |
| "Como funciona a queima de moedas?" | `market-metrics.md#9` 0,437; `stablecoins.md#16` 0,444; `stablecoins.md#17` 0,448 | Não | Assunto ausente |

### 1.2 Caso principal: "Por que a recompensa dos mineradores cai pela metade?"

**Conteúdo relevante no corpus** (`bitcoin.md#1`, metadados `{"source": "bitcoin.md", "title": "Bitcoin"}`):

> ## Halving
> O halving reduz pela metade a recompensa por bloco a cada 210.000 blocos, aproximadamente a cada quatro anos. [...]

**Busca original:**

| Rank | Trecho | Distância | Conteúdo | Responde? |
|---|---|---|---|---|
| 1 | `blockchain-basics.md#3` | 0,3449 | "## Consenso … PoW: mineradores competem … recebe a recompensa … PoS …" | Não (fala de recompensa, não da redução) |
| 2 | `bitcoin.md#0` | 0,3515 | "# Bitcoin … oferta máxima 21 milhões … recompensa de bloco paga aos mineradores …" | Parcial (contexto de emissão) |
| 3 | `bitcoin.md#1` | 0,3528 | "## Halving — reduz pela metade a recompensa por bloco a cada 210.000 blocos" | **Sim** |

**Problema identificado:** incompatibilidade de vocabulário. O usuário descreve o fenômeno ("recompensa cai pela metade") sem o nome técnico ("halving"). As palavras "mineradores" e "recompensa" aparecem com mais peso no trecho de Consenso, que fica em 1º sem responder. O melhor trecho (0,345) passa do limiar de reescrita (0,33), e o modo `auto` aciona a reescrita.

**Reescrita (`gemma4:e2b`, saída real da versão final, `retrieval-diagnosis.jsonl`):**

```json
{"main_concept": "recompensa de mineração",
 "rewritten_query (do LLM)": "recompensa mineradores redução recompensa metade mecanismo de recompensa bitcoin ethereum",
 "rewritten_query (usada)":  "recompensa mineradores redução recompensa metade mecanismo de recompensa",
 "removed_terms": ["bitcoin", "ethereum"]}
```

O LLM acrescentou dois ativos que a pergunta não cita. `strip_unrequested_assets` os removeu antes da busca (seção 1.6, última linha).

**Busca com a consulta reescrita:**

| Rank | Trecho | Distância à consulta reescrita | Distância à pergunta original | Palavras âncora no trecho |
|---|---|---|---|---|
| 1 | `bitcoin.md#1` (Halving) | **0,3565** | 0,3528 | recompensa, metade |
| 2 | `blockchain-basics.md#3` | 0,3614 | 0,3449 | recompensa, mineradores |
| 3 | `stablecoins.md#17` | 0,3988 | — | — (fora do limiar e sem âncora: não é evidência) |

Evidência devolvida: `bitcoin.md#1`, `blockchain-basics.md#3`.

**Comparação:**

| Métrica | Original | Reescrita |
|---|---|---|
| Posição do trecho que responde (`bitcoin.md#1`) | 3º | **1º** |
| Acerto no top-1 | Não | **Sim** |
| Reciprocal rank do trecho relevante | 0,33 | **1,00** |
| Trecho genérico de consenso | 1º | 2º |
| Distância do trecho relevante | 0,353 (à pergunta) | 0,357 (à consulta reescrita) |

**Por que melhorou:** a reescrita juntou "redução", "metade" e "recompensa" numa consulta sem a forma interrogativa ("Por que…"). Isso pôs o trecho que descreve a redução pela metade à frente do trecho genérico de consenso. A distância absoluta não caiu: a melhora é de **ordenação**, que é o que define o que o agente lê primeiro.

**O que não melhorou:** o modelo não produziu o termo técnico "halving". A melhora veio da reorganização do vocabulário do próprio usuário, não da tradução para o termo do documento.

**Variação entre execuções (temperatura 0, mesmo prompt):**

| Execução | Reescrita | `bitcoin.md#1` |
|---|---|---|
| API, antes da remoção de ativos (00:47 e 00:51 UTC) | "… mecanismo consenso **Bitcoin**" | 1º, distância 0,286 |
| Regressão de integração (≈00:58 UTC), antes da remoção | "… mecanismo de recompensa **bitcoin ethereum**" | 3º (`ethereum-staking.md#7` subiu para 1º) → teste **falhou** |
| 5 execuções seguidas com a remoção | 1ª: "… mecanismo consenso" (bitcoin removido); 2ª–5ª: idênticas à tabela acima | 1ª: 2º lugar; 2ª–5ª: 1º lugar |

A saída do Ollama em CPU não é estritamente determinística, mesmo com temperatura 0 (sem `seed` fixo). Com a remoção de ativos não citados, o trecho certo ficou entre os 2 primeiros em 5 de 5 execuções, contra 3º na busca original. O teste de integração usa esse critério ("melhora para o top-2"). Sem a remoção, chegou a 0,286 quando o modelo acrescentou só "Bitcoin". A remoção foi mantida porque "mineradores" não implica Bitcoin, e o enunciado proíbe acrescentar suposições.

Uma versão ainda anterior do prompt tinha "halving" no exemplo few-shot e produzia o termo (distância 0,139). Foi descartada por contaminar a avaliação (seção 1.6).

### 1.3 Segundo caso: "O que é impermanent loss?" (melhora parcial)

**Conteúdo relevante** (`defi-metrics.md#6`): um trecho de cerca de 800 caracteres com três seções ("DEX e AMM", "Empréstimos", "Métricas complementares"). A perda impermanente aparece em uma frase: "Provedores de liquidez … ficam expostos à **perda impermanente** quando os preços relativos mudam."

| | Original (`never`) | Reescrita (`auto`) |
|---|---|---|
| Consulta | "O que é impermanent loss?" | "impermanent loss perda impermanente provedor liquidez yield farming variação de valor posições" |
| Top-3 | `blockchain-basics.md#4` (nós e finalidade) 0,387; `risks.md#14` (pontes) 0,404; `stablecoins.md#17` (depeg) 0,411 | `defi-metrics.md#5` (DeFi e TVL) 0,349; `blockchain-basics.md#4` 0,350; `risks.md#13` (riscos, inclui liquidez) 0,353 |
| Trechos de DeFi/liquidez no top-3 | 0 | 2 |
| Trecho que define o termo (`defi-metrics.md#6`) | Fora do top-3 | Fora do top-3 |
| Evidência devolvida | `no_relevant_documents` (nada ≤ 0,38) | `defi-metrics.md#5`, `risks.md#13` (âncoras "liquidez", "perda") |

**Diagnóstico:** há duas causas.
1. **Vocabulário:** termo em inglês contra termo em português. A reescrita resolveu esta parte: a consulta reescrita contém "perda impermanente".
2. **Chunking:** a frase relevante está diluída num trecho dominado por DEX/AMM e empréstimos. O embedding do trecho não fica próximo de "perda impermanente". A reescrita não corrige isso, e a correção (dividir o trecho) exigiria mexer na ingestão, o que está fora do escopo desta fase.

**Efeito colateral registrado:** as âncoras "liquidez" e "perda" são palavras do domínio, e por isso aceitaram trechos só parcialmente relevantes. O agente recebe contexto de DeFi, mas não a definição.

### 1.4 Casos sem melhora

**"O que acontece se eu perder minha seed phrase?"**
- A reescrita ("perda seed phrase recuperação carteira … chave privada") trouxe `risks.md#12` (0,263), `stablecoins.md#16` e `blockchain-basics.md#4`.
- Nenhum deles tem palavra âncora, e `risks.md#12` é só o título do arquivo. A reescrita foi descartada e ficou a busca original.
- O trecho certo (`risks.md#13`, "perda das chaves privadas") não aparece em nenhuma das duas buscas. A causa dominante é o chunking: o título separado do conteúdo atrai as buscas sobre riscos.

**"O que significa slippage numa troca?" e "Como funciona a queima de moedas?"**
- São assuntos ausentes do corpus. As duas terminam em `no_relevant_documents`, com ou sem reescrita.

### 1.5 Risco observado: a reescrita pode fabricar relevância

Uma das reescritas reais de "Como funciona a queima de moedas?" foi "mecanismo queima de criptoativos destruição de tokens **blockchain**".
- A distância caiu de 0,437 (original) para **0,183** em `blockchain-basics.md#2`, um trecho que só define blockchain.
- Pela política "fica a busca com o melhor trecho", isso viraria evidência de um assunto que o corpus não tem.

A política final exige palavra âncora: "queima", "destruicao" e "tokenomics" não aparecem nos trechos trazidos. Ela também ignora termos genéricos ("blockchain", "tokens"). Com isso, a reescrita é descartada e o resultado é `no_relevant_documents` (teste [integração] `test_live_rewrite_does_not_fabricate_evidence_for_absent_topic`).

### 1.6 Evolução da política (o que foi medido e descartado)

| Política | Halving | Impermanent loss | Queima | Decisão |
|---|---|---|---|---|
| Fica a busca com o melhor trecho mais próximo | ✅ | ❌ (trechos errados) | ❌ evidência fabricada (0,183) | Descartada |
| Comparar as duas buscas pela distância à pergunta original | ❌ a original sempre vence (é ranqueada por essa distância) | ❌ | ✅ | Descartada antes de medir (falha por construção) |
| Piso de relevância pela distância à pergunta original (≤ 0,42), ranking da reescrita | ✅ 3º → 1º | ❌ trecho de DeFi certo a 0,425–0,455 rejeitado; ficaram trechos errados a 0,387 | ✅ | Descartada |
| Distância à consulta usada ≤ 0,38 + palavra âncora | ✅ 3º → 1º (quando o LLM acrescentou só "Bitcoin") / ❌ 3º (quando acrescentou "bitcoin ethereum") | ⚠️ parcial | ✅ `no_relevant_documents` | Mantida, com o acréscimo abaixo |
| + remoção de ativos não citados na pergunta (`strip_unrequested_assets`) (final) | ✅ 1º em 4/5, 2º em 1/5 | ⚠️ parcial (inalterado) | ✅ | Adotada |

O exemplo few-shot do rewriter também foi trocado: o original era "recompensa cai pela metade → halving", o próprio caso avaliado. Com aquele exemplo, o modelo escrevia "halving" e levava o trecho a 0,139, uma melhora inflada pelo prompt. O exemplo atual ("rug pull") é de um tema ausente do corpus.

### 1.7 Calibração dos limiares

| Situação | Melhor distância observada (pergunta original) |
|---|---|
| Recuperação boa (proof of stake, staking como garantia, TVL com sinônimos, Ethereum sem mineração, tempo de bloco) | 0,197–0,309 |
| Falha de vocabulário (halving, seed phrase, impermanent loss, slippage) | 0,345–0,387 |
| Assunto ausente (queima de moedas) | 0,437 |

Valores adotados:
- `RETRIEVAL_REWRITE_DISTANCE = 0,33`: aciona a reescrita só nas falhas;
- `RETRIEVAL_MAX_DISTANCE = 0,38`: trechos acima disso não são evidência. Isso eliminou trechos irrelevantes como `bitcoin.md#0` (0,383) para "slippage".

Os limiares valem para este corpus e para o `nomic-embed-text`.

## 2. Text-to-Query sobre dados reais [API real]

Banco depois das coletas da seção 3:
- 10 ativos, 19 snapshots;
- de 2026-11-05T00:36:39Z a 00:41:00Z (janela de 0,003 dia).

### 2.1 Bateria final (`POST /market-data/query`)

| Pergunta | Status | SQL executado (literais → `?`) | Parâmetros | Resultado real |
|---|---|---|---|---|
| Quais ativos tiveram alta de preço maior que 2% nas últimas 24 horas? | `ok` | `SELECT symbol, name, percent_change_24h FROM latest_snapshots WHERE percent_change_24h > ? ORDER BY percent_change_24h DESC LIMIT 50` | `[2]` | 7 linhas: ETH 7,27; ADA 5,66; BTC 4,83; XRP 4,66; SOL 4,34; DOGE 4,17; BNB 3,94 |
| Quais são os 3 maiores ativos por market cap no banco? | `ok` | `SELECT symbol, name, market_cap_usd FROM latest_snapshots ORDER BY market_cap_usd DESC LIMIT 3` | `[]` | BTC 1,62 tri; ETH 296,4 bi; USDT 178,3 bi |
| Quais ativos caíram nos últimos 7 dias? | `ok` | `SELECT symbol, name, percent_change_7d FROM latest_snapshots WHERE percent_change_7d < ? ORDER BY percent_change_7d ASC LIMIT 50` | `[0]` | 7 linhas: SOL -5,12; BNB -4,31; TRX -3,01; ADA -2,82; DOGE -2,45; XRP -0,29; BTC -0,01 |
| Qual o volume de 24h do Ethereum no banco? | `ok` | `SELECT symbol, name, volume_24h_usd FROM latest_snapshots WHERE symbol = ? ORDER BY retrieved_at DESC LIMIT 1` | `['ETH']` | ETH 11.612.867.029,47 |
| Qual a média de preço dos snapshots de BTC armazenados? | `ok` | `SELECT symbol, name, AVG(price_usd) AS avg_price_usd, COUNT(*) AS snapshots FROM snapshot_history WHERE symbol = ? GROUP BY symbol, name LIMIT 1` | `['BTC']` | 80.606,19 (2 snapshots) |
| Qual ativo mais subiu nos últimos 90 dias? | `insufficient_coverage` | — (não executado; LLM não chamado) | — | "pede 90 dias, mas os snapshots armazenados cobrem só 0.00 dia(s) …" |
| Qual o TVL da Uniswap? | `unsupported` | — | — | "o banco não tem TVL nem dados de protocolos DeFi" |
| Quais ativos subiram mais de 50% em 24 horas? | `no_matching_records` | `... WHERE percent_change_24h > ? ...` | `[50]` | 0 linhas (dados existem; nenhum atende) |
| Apague todos os snapshots do banco | `unsupported` | — | — | O modelo recusou ("Não é permitido executar comandos de modificação") |

A janela de 7 dias usa `percent_change_7d` do provedor: não exige 7 dias de snapshots e não aciona `insufficient_coverage`. Já 90 dias exige observações reais.

### 2.2 Problemas encontrados nas execuções anteriores (mesmo dia)

| Execução | SQL gerado | Problema | Tratamento |
|---|---|---|---|
| 1ª | `SELECT symbol, name, market_cap_usd FROM latest_snapshots WHERE symbol IN ('ADA', 'BNB', 'BTC', 'DOGE', 'ETH', 'SOL', 'TRX', 'USDC', 'USDT') ORDER BY market_cap_usd DESC LIMIT 3` | Filtro não pedido, **sem XRP**. O modelo copiou a lista de ativos que estava no prompt | Lista removida do prompt; regra de só filtrar ativo citado |
| 2ª | `SELECT AVG(price_usd) FROM market_snapshots WHERE symbol = 'BTC'` | `market_snapshots` não tem `symbol`. **Recusado pelo validador** (`coluna não permitida ou inexistente: symbol`) | View `snapshot_history` + exemplo |
| 2ª | `SELECT symbol, name, percent_change_7d FROM market_snapshots WHERE ...` | Idem | Idem; regra 4 do prompt reescrita |

### 2.3 Segurança

| Verificação | Evidência |
|---|---|
| 16 formas de SQL inseguro ou inválido recusadas antes da execução (DELETE, UPDATE, INSERT, DROP, ALTER, CREATE, PRAGMA, ATTACH, múltiplos comandos, `sqlite_master`, coluna inexistente, `load_extension`, `main.assets`, LIMIT por subconsulta, texto não SQL, vazio) | [offline] `test_unsafe_or_invalid_sql_is_rejected_before_execution` |
| Conexão somente leitura recusa `DELETE` direto; o authorizer nega `random()`, que passa no parser | [offline] `test_execution_is_read_only_and_authorizer_blocks_unlisted_functions` |
| CTE recursiva de 10⁸ iterações interrompida em 0,05 s | [offline] `test_timeout_interrupts_long_query` |
| `LIMIT 500` reduzido para o máximo; literais parametrizados | [offline] `test_valid_select_gets_limit_and_bound_parameters`, `test_limit_above_maximum_is_capped_and_ctes_are_allowed` |
| Mensagens sem caminho de arquivo | [offline] `test_unknown_column_in_generated_sql_is_invalid` |

## 3. Coleta de snapshots reais [API real]

| Coleta (UTC) | Pedido | Inseridos | Duplicados | Falhas | Observação |
|---|---|---|---|---|---|
| 00:36:39 | Lista padrão (10) | 10 | 0 | 0 | `source_updated_at` entre 00:31:13 e 00:36:12 |
| 00:36:48 | Lista padrão | 1 | 9 | 0 | Só o USDC tinha `last_updated` novo (00:31:13 → 00:36:12) |
| 00:37:58 | `["btc-bitcoin"]` | 0 | 1 | 0 | Mesmo `source_updated_at`: não duplicou |
| 00:40:59 | Lista padrão | 8 | 2 | 0 | USDT e USDC sem atualização na fonte |

Valores gravados como o provedor envia:
- `percent_change_30d` veio **preenchido** pela fonte nestas coletas (BTC -6,0%, ETH -10,19%, SOL -20,9%). Até 2026-10-28 o plano gratuito devolvia 0, e o cliente converte esse 0 exato para `NULL` (seção 5);
- `source_updated_at` e `retrieved_at` são preservados separadamente.

[integração] `test_live_ingestion_persists_real_snapshots` repete o fluxo num banco temporário com 5 ativos.

## 4. Agente principal com as novas ferramentas [API real]

`POST /agent` com `include_trace: true`. Os dados brutos estão em `docs/evidence/phase-4/agent-run1.jsonl` (1ª rodada) e `agent-run2.jsonl` (depois das correções da seção 4.2).

### 4.1 Seleção de ferramentas

| Pergunta | Ferramentas chamadas (entrada) | Status | Resposta (resumo) | Fontes |
|---|---|---|---|---|
| Qual é o preço atual do Bitcoin? | `get_market_data` (BTC) | `complete` | US$ 80.632,66, +4,88% em 24h, ATH US$ 126.173,18 (CoinPaprika, 00:44 UTC) | CoinPaprika `/tickers/btc-bitcoin` |
| Quais ativos da nossa base de dados tiveram alta de preço maior que 5% em 24 horas? | `query_market_database` (pergunta completa) | `complete` | ETH 7,27% e ADA 5,66%, "dados de snapshots, não cotações ao vivo" | SQLite local (snapshots coletados da CoinPaprika) |
| Explique como funciona proof of stake. | `search_knowledge_base` ("como funciona proof of stake") | `complete` | Validadores fazem stake como garantia; slashing; Ethereum desde 2022 | `ethereum-staking.md`, `blockchain-basics.md` |
| Por que a recompensa dos mineradores cai pela metade? (2ª rodada) | `search_knowledge_base` (pergunta completa) → reescrita aplicada (log: `rewrite_applied=True best=0.286`; execução anterior à remoção de ativos não citados) | `complete` | "reduzida pela metade a cada 210.000 blocos, um evento conhecido como halving…" | `bitcoin.md`, `blockchain-basics.md` |
| Compare Bitcoin e Ethereum usando suas métricas de mercado e explique a diferença entre proof of work e proof of stake. (2ª rodada) | `get_market_data` (BTC), `get_market_data` (ETH) | **`partial`** | Preços e variações reais; "a diferença conceitual … não foi encontrada na base de conhecimento local" | CoinPaprika (2×) |

O agente escolheu a ferramenta certa nas quatro perguntas de um só tipo. Na pergunta combinada, o `gemma4:e2b` não chamou `search_knowledge_base` nas duas rodadas. Isso continua sendo uma limitação, mas agora é declarada pelo sistema (4.2).

### 4.2 Problemas da 1ª rodada e correções

| Problema observado (1ª rodada) | Correção | Resultado (2ª rodada) |
|---|---|---|
| Pergunta combinada: só `get_market_data`; a explicação PoW × PoS veio do conhecimento do modelo, **sem documento**, e o status foi `complete` | Regra 7 do PTCF (conceito exige `search_knowledge_base` mesmo em pergunta mista) + guarda determinística em `assemble_report`: pedido conceitual sem trecho recuperado vira `partial` com incerteza explícita | O modelo continua sem buscar, mas a resposta passa a dizer que não há base documental, o status é `partial` e a incerteza é registrada |
| Halving: o agente enviou à ferramenta só "recompensa dos mineradores", sem "cai pela metade". A distância ficou abaixo do gatilho, a reescrita não rodou e a resposta disse que o texto "não detalha uma queda" | Descrição de `search_knowledge_base`: "Entrada: a pergunta conceitual completa, sem resumir" (com exemplo de outro tema) | Pergunta completa → reescrita → `bitcoin.md#1` em 1º → resposta correta citando o halving |
| "maior que 5%" foi removido da resposta (o 5 não estava nas observações) | A observação de `query_market_database` passa a incluir `params` (valores do filtro) | Resposta mantém "superior a 5%", sem incertezas espúrias |

Testes [offline] que cobrem as correções:
- `test_mixed_question_without_documents_is_partial`;
- `test_sql_filter_values_are_grounded`.

### 4.3 Distinção das fontes no `CryptoReport`

- **CoinPaprika ao vivo:** `data_sources[].provider = "CoinPaprika"`, `endpoint = /tickers/{id}`, `source_updated_at` da fonte.
- **Snapshots:** `provider = "SQLite local (snapshots coletados da CoinPaprika)"`, `endpoint = market_db`, `retrieved_at` = coleta mais recente. Os achados vêm prefixados com "Banco de snapshots (coleta mais recente …)".
- **Documentos:** `provider = "base de conhecimento local (Chroma)"`, `endpoint = crypto_knowledge`. Os achados citam título e arquivo.

## 5. Testes

| Suíte | Comando | Resultado |
|---|---|---|
| Offline completa (Fases 1–4) | `make test` | **312 passed**, 18 deselected (marcados `integration`/`network`), 0 skipped |
| Offline só Fase 4 | `tests/unit/test_phase4_*.py` | 52 testes, todos com dublês (LLM roteirizado, `MockTransport`, `FakeIndex`, SQLite temporário) |
| Integração Fase 4, 1ª execução | `pytest -m "integration or network" tests/integration/test_phase4_live.py` | **6 passed** (169,5 s) |
| Integração completa (Fases 1–4), 1ª execução | `pytest -m "integration or network"` | **16 passed, 2 failed** (493 s) → `regression-run1.log` |
| Integração Fases 3 e 4, após as correções | idem, só `test_phase3_live.py` e `test_phase4_live.py` | **7 passed** (595,5 s) → `regression-run2.log` |

As duas falhas da 1ª execução completa foram reais e são explicadas em `Report/fase-04-ferramentas-rag.md` (5.6):
- **Fase 3:** a CoinPaprika passou a devolver `percent_change_30d` real, e o teste exigia a mensagem de "30 dias indisponível";
- **Fase 4:** a reescrita variou, acrescentou "bitcoin ethereum", e o trecho do halving caiu para 3º.

As correções que vieram depois (`strip_unrequested_assets`, janela de 30d no Text-to-Query e ajuste dos dois testes) não tocam os caminhos das Fases 1 e 2. Os 11 testes de integração dessas fases passaram na 1ª execução completa e não foram reexecutados depois.

Testes que exigem serviços externos:
- `network`: CoinPaprika real;
- `integration`: Ollama com `gemma4:e2b` e `nomic-embed-text`, além da coleção `crypto_knowledge` existente.

Sem esses serviços, os testes são ignorados (`skip`) e não falham.

| Teste de integração da Fase 4 | Marcadores | O que exercita |
|---|---|---|
| `test_live_ingestion_persists_real_snapshots` | network | Coleta real → SQLite; deduplicação |
| `test_live_text_to_query_on_populated_database` | integration, network | Top-3 por market cap confere com o banco; 90 dias → `insufficient_coverage`; TVL → `unsupported` |
| `test_live_retrieval_uses_existing_collection_without_rewrite_when_suitable` | integration | Coleção existente, sem reescrita quando a pergunta é boa, metadados preservados |
| `test_live_query_rewriting_fixes_vocabulary_mismatch` | integration | Halving: 3º → top-2 com a reescrita |
| `test_live_rewrite_does_not_fabricate_evidence_for_absent_topic` | integration | Queima de moedas → `no_relevant_documents` |
| `test_live_agent_selects_new_tools` | integration, network | ReAct escolhe `query_market_database` e `search_knowledge_base` |
