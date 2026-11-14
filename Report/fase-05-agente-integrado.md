# Fase 5 — Agente integrado, verificação de respostas, avaliação e interface

| | |
|---|---|
| **Período** | 2026-11-05 a 2026-11-08 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose, Ollama, CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (thinking desligado; agente, verificação e juiz) e `nomic-embed-text` |
| **Versões novas** | `langchain` 1.4.9 (com LangGraph 1.2.20), `langchain-core` 1.6.12 → **1.7.6**, `langsmith` 0.14.17 (já era dependência), `streamlit` 1.65.0 |

> Este diário foi consolidado no fim do projeto (2026-11-08) a partir de `docs/agent-integration.md`, `docs/response-verification.md`, `docs/langsmith-evaluation.md`, das evidências em `docs/evidence/phase-5/` e `docs/evidence/final/` e do histórico do git. As fases anteriores foram escritas durante o desenvolvimento.

## 1. Objetivo

Unir as capacidades das Fases 1 a 4 num **único agente** que:

- escolhe e combina ferramentas de API (CoinPaprika), SQL (snapshots), RAG (Chroma) e cálculo;
- usa o histórico persistente da Fase 3;
- **verifica cada resposta antes da entrega**: consistência factual contra a evidência real (NLI) e coerência lógica, com no máximo uma correção;
- é rastreado e avaliado (LangSmith, dataset versionado, avaliadores de resposta e de trajetória);
- tem uma interface Streamlit que só consome a API.

## 2. Escopo

**Dentro:** agente com tool calling nativo, ferramentas estruturadas, adaptador de histórico, serviço de verificação, política de correção, dataset e avaliadores, runner local e LangSmith, interface Streamlit, testes e documentação.

**Adaptações do enunciado (2026-11-05):**

| Enunciado | Implementado | Motivo |
|----------------------------------|--------------------------------|----------------------|
| `create_openai_tools_agent` + `AgentExecutor` | `create_agent` (pacote `langchain`) | APIs do pacote legado `langchain-classic`; `create_agent` é o substituto oficial do LangChain 1.x |
| `RunnableWithMessageHistory` | `SessionHistoryAdapter` | Depreciado no `langchain-core` 1.7.6 ("Use LangGraph's built-in persistence instead") |
| NLI com Transformers (`mDeBERTa-v3-base-mnli-xnli`) | NLI com `gemma4:e2b` via Ollama | Sem torch e transformers (cerca de 2 GB, com 5,3 GB livres) e um único motor de inferência |

**Fora:** autenticação, CoinGecko e DeFiLlama.

## 3. Solução implementada

```
POST /conversations/{id}/agent
 └─ IntegratedAgentService (trace "integrated.turn")
     ├─ trava por sessão → grava a pergunta (SQLite)
     ├─ SessionHistoryAdapter: resumo + memórias Mem0 + janela (só leitura)
     ├─ create_agent: gemma4:e2b ⇄ 7 ferramentas estruturadas; trajetória registrada
     ├─ VerificationService: afirmações → evidência → checagem numérica | NLI → coerência → status
     ├─ [política] 1 correção com as próprias observações → 2ª verificação → resposta cautelosa se falhar
     └─ grava a resposta e a análise → manutenção da Fase 3 em segundo plano
```

| Módulo | Responsabilidade |
|---|---|
| `app/integrated/` | Agente (`create_agent`), ferramentas estruturadas que delegam às implementações das Fases 2 e 4, adaptador de histórico e o turno |
| `app/verification/` | Extração de afirmações, evidência, NLI, coerência lógica, política de correção |
| `app/evaluation/` | Alvo, avaliadores (resposta e trajetória) e runner local/LangSmith |
| `evaluation/phase5_dataset.json` | Dataset versionado (versão 3: 20 exemplos, 10 categorias) |
| `ui/` | Interface Streamlit e cliente HTTP |

Detalhes em `docs/agent-integration.md`, `docs/response-verification.md` e `docs/langsmith-evaluation.md`.

## 4. Decisões técnicas

| Decisão | Alternativas | Motivo |
|---|---|---|
| `create_agent` sem checkpointer | Checkpointer do LangGraph | O SQLite da Fase 3 continua a única fonte da verdade da conversa; o adaptador só lê e o serviço grava uma vez por turno, sem duplicação |
| Manter o `gemma4:e2b` | Trocar de modelo para ter tool calling | O spike mostrou tool calling nativo funcionando pelo Ollama (bind, seleção, execução e incorporação) |
| Ferramentas estruturadas que **delegam** | Reescrever as ferramentas | Nenhuma regra de negócio duplicada; o contrato de observação da Fase 2 é mantido |
| Extração de afirmações determinística (por frase) | Extração por LLM | Mais uma chamada de 5 a 15 s por turno em CPU, para respostas que já são curtas |
| Números checados por código, não pela NLI | Tudo pela NLI | Lição das Fases 1 a 4: o modelo de 2B erra comparações numéricas |
| Seleção de até 3 evidências por afirmação, por pontuação | Comparar todas com todas | Custo de NLI proporcional ao número de afirmações, não ao produto |
| `neutral` ≠ `contradiction`; NLI indisponível → `verification_failed` | Tratar falha como contradição | Falta de evidência não é evidência contrária; falha de infraestrutura não pode condenar a resposta |
| No máximo 1 correção, sem laço | Corrigir até passar | Custo previsível (cada correção é mais uma execução do agente) e resposta cautelosa explícita quando não passa |
| Juiz LLM = `gemma4:e2b` local, junto com avaliadores determinísticos | Juiz externo | Sem dependência de modelo pago; o viés de autoavaliação é compensado por `groundedness` e `factual_consistency` determinísticos |
| Interface só por HTTP | Chamar o agente direto do Streamlit | Uma única implementação de agente e memória; a UI não precisa de Ollama nem de banco |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Atualização do `langchain-core` exigida pelo `create_agent`

- **Sintoma:** o `langchain` 1.4.9 exige `langchain-core` 1.7.6; o projeto estava fixado em 1.6.12 desde a Fase 1.
- **Correção:** atualização do `langchain-core`.
- **Evidência:** a suíte offline das Fases 1 a 4 passou sem mudança depois da atualização (312 testes).

### 5.2 NLI com modelo de 2B: neutro classificado como contradição

- **Sintoma (1ª execução da integração, `docs/evidence/phase-5/integration-run1.log`):** "O Proof of Stake foi inventado em 2012 pela equipe do Peercoin", sem apoio nem negação na evidência, saiu como `contradiction`. O teste exigia acerto exato em 3 casos e falhou.
- **Causa:** o rótulo e a confiança são gerados pelo LLM, sem cabeça de classificação calibrada; afirmações "fora da evidência" tendem a ser lidas como "contra a evidência".
- **Correção:** a medição passou a usar 12 pares reais (4 por rótulo, sobre PoS e halving), com critério de pelo menos 10 acertos e no máximo 1 neutro classificado como contradição. A limitação foi documentada: a confiança não é probabilidade calibrada.

### 5.3 Pergunta sobre o próprio histórico sem resposta

- **Sintoma (mesma execução):** em "De qual ativo eu falei antes nesta conversa?", o agente respondeu que não havia histórico, embora as duas perguntas anteriores fossem sobre ETH. A verificação devolveu `verified` com 0 afirmações.
- **Leitura:** o histórico chegou ao agente (a pergunta seguinte, "volume de 24 horas desse mesmo ativo", resolveu ETH corretamente, inclusive após o reinício). O modelo de 2B não usou o histórico para responder uma pergunta **sobre** a conversa.
- **Limitação registrada:** a verificação confere afirmações factuais; uma recusa sem afirmações passa como `verified`. A avaliação mede a memória por outros meios (`memory-followup`, `memory-restart`).
- **Reprodução (integração final, 2026-11-08):** a mesma pergunta falhou de novo ("Não há nenhum ativo mencionado em sua solicitação anterior"). Desta vez a frase foi extraída como afirmação, ficou `neutral`, a verificação deu `unsupported`, a correção não resolveu e saiu a resposta cautelosa com aviso. A falha do modelo persiste; a diferença é que agora ela aparece como não verificada em vez de passar como `verified`.

### 5.4 Resposta cautelosa marcando a própria limitação

- **Sintoma (avaliação de base, `docs/evidence/phase-5/eval-local-baseline.json`):** em `ambiguous-01` e `error-coverage`, frases como "por favor, indique o ativo de seu interesse" receberam o sufixo "(não confirmado pelas fontes consultadas)".
- **Causa:** declarações de limitação, pedidos ao usuário e frases que só dizem de onde veio o dado eram extraídos como afirmações; sem evidência que os "sustentasse", ficavam `neutral` e a resposta cautelosa os marcava.
- **Efeito:** respostas corretas ficaram confusas, e a relevância medida caiu (`error-coverage`: 0,2).
- **Correção:** a extração passou a ignorar esses três tipos de frase (`NON_FACTUAL` e `PROVENANCE_ONLY` em `app/verification/claims.py`). Na avaliação final, os dois exemplos saem `verified` e sem marcação.

### 5.5 Avaliação contaminada por falhas de infraestrutura

- **Sintoma:** na execução `local-after-fixes` (`docs/evidence/phase-5/eval-local-after-fixes.json`), 8 dos 19 exemplos terminaram com `ConnectError('[Errno -2] Name or service not known')` ou `RemoteProtocolError`: o alvo perdeu o Ollama no meio da execução. As médias caíram (por exemplo, `tool_selection` de 0,929 para 0,5).
- **Causa:** o runner atribuía nota a exemplos que nem executaram; com saída vazia, os avaliadores davam 0.
- **Correção (2026-11-08):** `_summarize` exclui das médias os exemplos com erro e informa `n_scored` e `errored` (teste `test_summary_excludes_examples_that_failed_to_run`). A execução `local-after-fixes` não é usada como resultado; a avaliação final foi repetida (seção 6).

### 5.6 Interface: retomar conversa pelo id

- **Sintoma (2026-11-08, uso da interface):** a barra lateral só permitia retomar uma conversa digitando o `session_id` (32 caracteres hexadecimais).
- **Correção:**
  - endpoint `GET /conversations` (mais recentes primeiro);
  - título gerado pelo modelo após o 1º turno, na manutenção em segundo plano (sem latência extra), com a primeira pergunta truncada como alternativa;
  - lista clicável na barra lateral, atualizada a cada 15 s;
  - botão "Deploy" do Streamlit oculto (`client.toolbarMode = "minimal"`).
- **Problema encontrado no teste real:** o título caiu sempre na alternativa. O LLM da aplicação roda em modo JSON (`format="json"`) e não devolve texto livre. O prompt passou a pedir `{"title": ...}`, e o parser aceita JSON ou texto. Na repetição, uma conversa sobre ETH recebeu "Preço e Consenso do Ethereum".

### 5.7 Parâmetro depreciado na interface

- **Sintoma:** a auditoria final de depreciação encontrou `use_container_width` nos botões da interface, depreciado no Streamlit 1.65.
- **Correção:** `width="stretch"`. Detalhes da política no capítulo de decisões transversais.

### 5.8 Pedido de recomendação: recusa sem consultar os dados

- **Sintoma (uso real na interface, 2026-11-08 19:56 UTC):** em "qual deve investir: sol ou ether?", o agente respondeu "Não tenho dados de mercado atuais [...] para comparar SOL e ETH" sem chamar nenhuma ferramenta (`tools=[]` na resposta e na correção). Na interface, isso pareceu indisponibilidade da CoinPaprika.
- **Investigação:** a CoinPaprika estava funcionando (`/health/coinpaprika` 7/7, cota mensal com 19.833 de 20.000 chamadas, nenhum erro no log). A documentação dela não menciona restrição por dia da semana nem janela de manutenção. A causa era o agente.
- **Causa:** a regra "sem recomendações de investimento" do prompt. Diante de um pedido de recomendação, o modelo de 2B recusou a pergunta inteira, inclusive a parte que ele podia atender (os dados), e afirmou não ter dados sem tentar buscá-los.
- **Correção em três camadas:**
  1. **Prompt (regra 6):** um pedido de recomendação ou previsão sobre ativos citados não dispensa a consulta; o agente busca os dados, compara e declina só a recomendação.
  2. **Guarda determinística (`skipped_lookup` em `app/integrated/service.py`):** se a pergunta cita ativos (`find_assets`, da Fase 3), nenhuma ferramenta foi chamada e a resposta recusa ou devolve a pergunta, a correção única é acionada com a instrução de consultar cada ativo. Só o prompt não bastou: com temperatura 0, "Qual devo investir: SOL ou ETH?" falhava sempre, enquanto "qual deve investir: sol ou ether?" passava.
  3. **Resposta final vazia:** numa das tentativas, o agente chamou as ferramentas e devolveu texto vazio, que a verificação aprovava (sem afirmações) e virava "Não foi possível gerar uma resposta". Resposta vazia agora também aciona a correção única.
- **Avaliação:** novo exemplo `advice-01` no dataset (versão 3, 20 exemplos), exigindo `get_market_data` para SOL e ETH e a recusa da recomendação.
- **Evidência (`docs/evidence/final/advice-probe.jsonl`):** depois das três camadas, 6 de 6 execuções (3 por formulação) consultaram SOL e ETH, apresentaram os dados e declinaram a recomendação. Testes offline: `test_refusal_without_lookup_of_cited_assets_is_corrected`, `test_skipped_lookup_only_flags_refusals_with_cited_assets` e `test_empty_final_answer_uses_the_single_correction`.
- **Aprendizado:** uma regra de "não faça X" num modelo pequeno tende a virar "não faça nada". A restrição precisa dizer o que **continua** sendo feito, e a falha mais provável precisa de uma checagem por código.

### 5.9 Comparação entre redes respondida só com preço

- **Sintoma (uso real, 2026-11-12):** em "faça uma comparação sobre solana vs ethereum", o agente chamou só `get_market_data` e respondeu com preço, rank e volume de SOL e ETH. A pergunta era sobre as **redes**: contratos inteligentes, consenso, finalidade.
- **Causas:** (1) a base de conhecimento não tinha nada sobre Solana, e Ethereum tinha uma frase sobre EVM; pela regra 4, sem trecho recuperado o agente não pode fazer afirmação conceitual; (2) o prompt só mandava para `search_knowledge_base` perguntas do tipo "o que é / como funciona / diferença entre", e "comparação X vs Y" caía nos dados de mercado; (3) o limite de 2 a 6 frases não comportava tecnologia + mercado.
- **Correção:**
  1. **Corpus:** `solana.md` (PoH, Tower BFT, programas em Rust/SBF, modelo de contas, Sealevel, níveis de confirmação e finalidade, Alpenglow, trade-offs) e `comparacao-l1.md` (Ethereum × Solana por eixo, em seções de prosa menores que o chunk de 800 caracteres, com um resumo inicial que já cita finalidade); `ethereum-staking.md` ganhou as seções "Contratos inteligentes" e "Finalidade" (Gasper, Casper FFG, ~12,8 min). Sem preços (teste `test_corpus_has_no_market_prices`).
  2. **Prompt e descrições:** comparação entre redes → `search_knowledge_base` **e** `get_market_data` de cada ativo; resposta em tecnologia + mercado, até 12 frases. A descrição de `search_knowledge_base` e o prompt do Query Rewriting citam Solana, contratos inteligentes e finalidade.
  3. **Guarda determinística (`skipped_concepts` em `app/integrated/service.py`):** pergunta com "vs/compar/diferen", dois ou mais ativos e sem `search_knowledge_base` aciona a correção única, pedindo a busca conceitual. Comparações só de mercado ("compare o preço de BTC e ETH") ficam de fora.
- **Primeira tentativa do corpus:** com a tabela comparativa, o chunk mais próximo da pergunta era só o título da seção (a tabela passava de 800 caracteres e foi separada). A tabela virou prosa por eixo.
- **Avaliação:** o dataset da Fase 5 não mudou (limite de 15 a 20 exemplos e números da versão 3 já reportados); o caso fica nos testes `test_skipped_concepts_flags_network_comparisons_without_knowledge_base` e `test_network_comparison_without_knowledge_base_is_corrected`.
- **Evidência (`docs/evidence/final/network-comparison-probe.jsonl`, 2026-11-12):** recuperação com distância 0,105 no resumo de `comparacao-l1.md`. Seis turnos reais, 3 por formulação: **6 de 6 comparações** consultaram `search_knowledge_base` e explicaram consenso e contratos inteligentes. Com "faça uma comparação sobre solana vs ethereum", as 3 execuções também buscaram SOL e ETH em `get_market_data`, mas nenhuma citou a finalidade; a verificação terminou 1 `partially_verified` (2 afirmações `neutral`) e 2 `verification_failed` (erro do verificador numa afirmação), de 120,6 s a 206,2 s. Com "Qual a diferença entre Ethereum e Solana?", as 3 citaram a finalidade, não buscaram dados de mercado (a pergunta não pede) e terminaram com 3 `verified`, de 54,2 s a 83,2 s.

### 5.10 Redes fora da base: perfil da CoinPaprika e consultas feitas pelo código

- **Motivação:** a seção 5.9 resolve Ethereum × Solana escrevendo documentos, o que não escala: cada rede nova exigiria um Markdown. Alternativas avaliadas: perfil da CoinPaprika (já integrada), Wikipedia por ferramenta, ingestão automática de documentação oficial, cache das buscas externas no Chroma e busca web. Escolhida primeiro a mais barata: o perfil da CoinPaprika.
- **1ª tentativa — ferramenta à disposição do modelo:** `get_asset_profile` lê `/coins/{id}` (consenso, código aberto, início, descrição, whitepaper, site, repositório) para qualquer moeda, e o prompt passou a indicá-la nas comparações. No teste real (`docs/evidence/final/asset-profile-probe.jsonl`, 2026-11-13), piorou: em "cardano vs avalanche" o modelo chamou só a base, que não cobre as redes, e respondeu que não havia informação (`unsupported`); em "solana vs ethereum" chamou perfil e mercado, **deixou de consultar a base**, leu "finalidade" como "propósito" e, mesmo com a correção pedindo a base, não a chamou.
- **Dado defasado na fonte:** a CoinPaprika ainda informa `hash_algorithm: Ethash` para o ETH, o algoritmo do PoW anterior ao The Merge, e `org_structure` é opinião ("Semi-centralized"). Os dois campos foram retirados; ficaram consenso, descrição e links.
- **Correção — o código escolhe as ferramentas:** quando a pergunta compara redes (`network_comparison`: "vs/compar/diferen", dois ou mais ativos e não só de mercado), `IntegratedAgentService._prefetch` executa `search_knowledge_base` com a pergunta, `get_asset_profile` e `get_market_data` de cada rede, e injeta as chamadas no grafo como se o modelo as tivesse feito (`prefetch_messages`: `AIMessage.tool_calls` + `ToolMessage`), seguidas de uma instrução de redação em duas partes (tecnologia e mercado; o que não estiver nas observações, "não encontrado nas fontes"). O modelo só redige. As consultas entram na trajetória, na evidência da verificação e nas fontes da resposta. Fora de comparação entre redes, nada muda: o modelo continua escolhendo as ferramentas. O perfil só é ligado no agente integrado (`profile=True`); os agentes ReAct das Fases 2 e 4 ficaram como estavam.
- **Evidência (`docs/evidence/final/network-comparison-prefetch-probe.jsonl`, 2026-11-13):** 3 turnos reais, todos com as 5 consultas (base, 2 perfis, 2 mercados) e **3 de 3 `complete` e `verified`**, de 102,6 s a 158,1 s:
  - "faça uma comparação sobre solana vs ethereum": consenso, contratos inteligentes, escalabilidade e mercado, sem correção; a finalidade aparece como título, mas os tempos não;
  - "faça uma comparação sobre cardano vs avalanche" (fora da base): consenso PoS, Ouroboros (Cardano) e finalidade abaixo de um segundo (Avalanche) vindos do perfil, mais mercado; passou por uma correção;
  - "Qual a diferença entre Ethereum e Solana?": consenso, contratos, finalidade (12,8 minutos × menos de 1 segundo / 13 segundos), escalabilidade e mercado, sem correção.
- **Testes offline:** `test_network_comparison_tools_are_called_by_code_and_model_only_writes`, `test_prefetch_skips_questions_that_are_not_network_comparisons`, `test_asset_profile_tool_is_opt_in`, `test_asset_profile_returns_technical_fields_without_prices`, `test_profile_becomes_portuguese_evidence_and_finding`.
- **Aprendizado:** acrescentar ferramenta a um modelo de 2B não acrescenta capacidade; muda o roteamento, e para pior. Quando a pergunta tem forma conhecida e as consultas necessárias são sempre as mesmas, o código deve fazê-las e o modelo deve só redigir.

### 5.11 O que os traces do LangSmith mostraram

O rastreamento no LangSmith foi ligado em 2026-11-12 (projeto `crypto-intelligence-agent`). Os traces foram exportados por `scripts/langsmith_export.py` para `docs/evidence/langsmith/` (`turns.jsonl` e `summary.json`); as métricas estão na seção 6.3. A leitura dos traces encontrou quatro problemas que os logs e a avaliação local não mostravam:

1. **A suíte offline também gera traces.** O `.env` liga `LANGSMITH_TRACING=true` e o `docker compose run app pytest` carrega o `.env`. Dos **2.334 traces raiz** do projeto, só **13 são turnos reais** do agente; 100 dos 113 `integrated.turn` e 528 traces de dublês (`FakeListChatModel`, `RoutingFakeChat`) vêm dos testes. A taxa de erro do projeto (12,4%) mede, na maior parte, os erros que os testes provocam de propósito. O conteúdo dos testes também sai da máquina.
2. **Todas as `verification_failed` têm a mesma causa (3 de 3).** O verificador de coerência (LLM em modo JSON) escreveu uma descrição com mais de 400 caracteres; o schema `CoherenceOutput` (`max_length=400`) rejeitou a saída inteira e a verificação terminou como "não pôde ser concluída". A falha é do limite de validação, não do modelo: o problema apontado se perde por causa do tamanho do texto.
3. **"solana vs bitcoin" (uso real, 2026-11-13): erro factual aprovado pela NLI.** A busca na base trouxe só trechos de `comparacao-l1.md` (Ethereum × Solana, distâncias 0,200 a 0,226), e o modelo respondeu que "ambas utilizam Proof of Stake", falando do Ethereum no lugar do Bitcoin. A NLI marcou essa frase como `entailment`, citando os trechos sobre Ethereum e Solana, embora o perfil do BTC na evidência dissesse `Proof of Work`. A NLI confere se a frase é sustentada por algum trecho, mas não se o trecho fala dos mesmos ativos.
4. **Falso positivo da regra de sentido.** No mesmo turno, a regra `direction_mismatch` leu "visando **alta** velocidade" como "diz que SOL subiu" (variação 24h de −5,08%). A regra procura palavras de alta e queda na frase sem confirmar que elas se referem ao preço.

- **Aprendizado:** a avaliação agregada (seção 6.1) e as amostras de teste real (seções 5.9 e 5.10) não revelaram nenhum desses quatro problemas; o trace por etapa revelou. Também ficou claro que o rastreamento precisa ser desligado nos testes offline, ou as métricas do projeto deixam de medir o sistema.

**Correções (2026-11-13):**
1. **Testes sem rastreamento:** `tests/conftest.py` chama `langsmith.configure(enabled=False)` no `pytest_configure`, a não ser que a execução selecione integração ou rede (`-m "integration or network"`). O interruptor é global e vale também para as threads do `TestClient`. Conferido no LangSmith: uma execução completa da suíte offline criou 0 traces raiz. Teste `test_offline_suite_does_not_send_traces_to_langsmith`.
2. **Coerência truncada, não rejeitada:** um validador corta a descrição em 400 caracteres em vez de recusar a saída inteira. Teste `test_long_coherence_description_is_truncated_not_rejected`.
3. **Comparação "solana vs bitcoin"**, em quatro partes:
   - a pré-consulta busca a base também **por rede** ("Bitcoin: consenso, contratos inteligentes, finalidade e escalabilidade"); com isso o `bitcoin.md` passou a vir (distância 0,213), ao lado do documento comparativo;
   - a seleção de evidência garante, para cada ativo da afirmação, ao menos uma evidência que fale dele; se nenhuma falar, a afirmação sai `neutral` sem chamar o LLM;
   - "ambas/ambos/as duas/os dois" sem ativo na frase recebem os ativos da pergunta;
   - **regra de consenso (`consensus_check`):** a família do consenso (PoS × PoW) atribuída a cada ativo é conferida contra o `proof_type` do perfil da CoinPaprika, sem LLM. Divergência vira `contradiction`; se o consenso confere e a NLI ainda diz `contradiction` (caso real: "Solana usa DPoS" contra o trecho "Proof of Stake"), o rótulo cai para `neutral`. Na primeira rodada após as três primeiras partes, a NLI ainda aprovou "Ambas as redes utilizam Proof of Stake" com o perfil do BTC na evidência; a regra foi a resposta a isso.
   - a instrução de redação pede para falar só das redes comparadas.
4. **Regras de coerência com contexto:** a regra de sentido só considera "alta/queda" em frase sobre preço ou variação; a regra de cálculo ignora frases com valores de métricas diferentes (preço e market cap), um 5º falso positivo, da mesma família, encontrado no teste das correções ("cardano vs avalanche" saiu `contradicted` por isso).

**Resultado** (`docs/evidence/final/langsmith-fixes-probe.jsonl`, 2026-11-13; 1 execução por pergunta, com as 7 consultas fixas):

| Pergunta | Antes (traces da seção 6.3) | Depois |
|---|---|---|
| solana vs bitcoin | "ambas usam Proof of Stake", `verification_failed` | consenso correto (PoW × DPoS); `partially_verified` |
| solana vs ethereum | 4 de 6 execuções sem `verified` | `complete` e `verified`, sem correção |
| cardano vs avalanche | `unsupported`; depois `verified` com correção | `complete` e `verified`, sem correção |

O que sobrou em "solana vs bitcoin" é redação do modelo de 2B: em contratos inteligentes ele ainda descreve o Ethereum, e lê "finalidade" como "propósito" ("a finalidade do Bitcoin é a segurança"); essa frase saiu `neutral` e a resposta a marca como não confirmada. Testes offline: 391, todos aprovados.

### 5.12 Histórico de preço sem fallback para a API

- **Sintoma (uso real, 2026-11-13):** "me informe o preço do SOL nos dias da última semana" → "O banco de dados local de snapshots não retornou registros para o preço do SOL nos últimos 7 dias." O agente só tinha `query_market_database` para dados passados; com o banco sem registros do período, a resposta parou na falta de dados.
- **Causa:** o cliente da CoinPaprika tratava todo histórico como bloqueado no plano gratuito. Isso vale para `/coins/{id}/ohlcv/historical` (HTTP 402), mas não para `/tickers/{id}/historical`: verificado em 2026-11-13, o plano gratuito devolve um ponto por dia (00:00 UTC) no último ano; antes disso, 402 ("Getting daily historical data before ... is not allowed in this plan").
- **Correção:**
  - `CoinPaprikaClient.get_price_history(coin_id, days)`: intervalo `1d` até 31 dias e `7d` acima, limite de 364 dias (com o início em 00:00 UTC, 365 já cai fora da janela do plano);
  - ferramenta `get_price_history` (categoria `api`, só no agente integrado), argumentos `asset` e `days` (padrão 7). A saída traz só data e preço: na 1ª execução real, com volume e market cap junto, o modelo de 2B embaralhou os dígitos ("US$ 103,468,952,90" no lugar de 1.034.689.529);
  - prompt: preço em dias passados → `get_price_history`; banco local sem registros → chamar `get_price_history` em seguida;
  - garantia no código (`skipped_history`): pergunta sobre dias passados ("última semana", "últimos N dias", "ontem", "histórico") com ativo citado e sem `get_price_history` bem-sucedido usa a correção única, com o período extraído da pergunta (`history_days`);
  - evidência e achados: um item por dia ("SOL em 2026-11-10 (00:00 UTC, histórico CoinPaprika): preço US$ 84,54").
- **Evidência:** a mesma pergunta, com o modelo real, passou a chamar `get_price_history` direto e listar os 8 pontos (2026-11-07 a 2026-11-14, US$ 89,82 a US$ 78,14), `complete` e sem correção. Testes: `test_empty_database_falls_back_to_price_history`, `test_skipped_history_flags_past_prices_without_price_history`, `test_price_history_uses_free_tickers_historical_daily` e outros 5 (401 testes offline passando).
- **Aprendizado:** "o plano gratuito não tem histórico" era verdade para um endpoint e virou regra para todos. Quando uma fonte falha, vale testar os endpoints vizinhos antes de declarar o dado indisponível.

## 6. Evidências e métricas

| Verificação | Resultado |
|---|---|
| Spike de tool calling (`gemma4:e2b`, 2026-11-05) | `bind_tools` seleciona `get_price(BTC)` em 10,4 s; `create_agent` incorpora o resultado em 8,3 s; pergunta dupla chama as duas ferramentas; histórico resolve "meu favorito" |
| Integração da Fase 5, 1ª execução | 5 passed, 1 failed (NLI, seção 5.2), 1 skipped (LangSmith não configurado), em 665,73 s |
| Avaliação de base (`local-baseline`, 19 exemplos, 2026-11-06) | Média de trajetória 0,927; média de resposta 0,869; 0 exemplos com erro |
| Avaliação final (`local-final-v3`, 2026-11-08) | Ver a seção 6.1 |
| Rastreamento no LangSmith (2026-11-12 a 2026-11-13) | 13 turnos reais exportados; ver a seção 6.3 |
| Integração final da Fase 5 (2026-11-08) | 6 passed, 1 skipped; ver a seção 6.2 |
| Testes offline (Fases 1–5) | Ver o capítulo de decisões transversais (auditoria de depreciação) |
| Comparação entre redes com consultas feitas pelo código (2026-11-13, seção 5.10) | 3 turnos reais (Solana × Ethereum, Cardano × Avalanche, "diferença entre"): 3 de 3 `complete` e `verified` |
| Comparação entre redes (2026-11-12, seção 5.9) | 6 turnos reais: todos usaram a base de conhecimento; 3 verificados; a finalidade só apareceu na formulação "diferença entre" |

### 6.1 Avaliação final

Três execuções locais completas, todas com o `gemma4:e2b` como agente, verificador e juiz:

| Execução | Data | Dataset | Código | Arquivo |
|---|---|---|---|---|
| Base | 2026-11-06 | versão 1, 19 exemplos | antes das correções da seção 5.4 | `eval-local-baseline.json` |
| Intermediária | 2026-11-08 | versão 2, 19 exemplos | com a seção 5.4 | `eval-local-final.json` |
| **Final** | 2026-11-08 | versão 3, 20 exemplos | com as seções 5.4 a 5.8 | `eval-local-final-v3.json` |

Na execução final, os **20 de 20 exemplos** executaram, sem erro de infraestrutura.

| Média | Final | Intermediária | Base |
|---|---|---|---|
| Trajetória (7 avaliadores) | **0,962** | 0,958 | 0,927 |
| Resposta (6 avaliadores) | **0,951** | 0,978 | 0,869 |

| Avaliador | Final | Intermediária | Base |
|------------------------------|--------|--------|--------|
| `groundedness` | 1,000 | 1,000 | 1,000 |
| `factual_consistency` | 0,975 | 1,000 | 0,803 |
| `logical_coherence` | 1,000 | 1,000 | 1,000 |
| `uncertainty` | 0,967 | 1,000 | 0,929 |
| `relevance` | 0,913 | 0,986 | 0,857 |
| `correctness` | 0,853 | 0,879 | 0,686 |
| `tool_selection` | 0,933 | 0,929 | 0,929 |
| `required_tools` / `no_unnecessary_tools` / `tool_combination` / `tool_inputs` | 1,000 | 1,000 | 1,000 |
| `evidence_used` | 0,900 | 0,889 | 0,727 |
| `error_handling` | 0,750 | 0,750 | 0,750 |

Leitura:

- **Nenhum valor inventado:** `groundedness` 1,0 nas três execuções; todo valor em US$ ou % das respostas existe nas observações reais.
- **Verificação:** na execução final, os 15 exemplos do agente terminaram com 14 `verified`, 1 `partially_verified` (na base: 8 `verified`, 3 `partially_verified`, 3 `unsupported`). Os 5 exemplos de verificação plantada (contradição, sem suporte, sustentada, cálculo errado e sentido invertido) receberam o status esperado nas três execuções. Passaram pela correção: `advice-01`, `ambiguous-01` e `api-rag-02`.
- **A média de resposta caiu da intermediária para a final** (0,978 → 0,951) por dois exemplos:
  - `api-rag-02` saiu `partially_verified`: a afirmação "O Ethereum utiliza este mecanismo [PoS]" ficou `neutral` (não confirmada pela evidência), e a resposta cautelosa removeu os nomes dos ativos das linhas de métricas ("Preço Atual: $74,565.00" sem dizer que é o BTC). Na intermediária, o mesmo exemplo saiu `verified`: é variação do modelo entre execuções, e o defeito da resposta cautelosa fica registrado como limitação.
  - `advice-01` (novo) teve `correctness` e `relevance` 0,5: a ferramenta foi chamada para SOL e ETH, mas a resposta corrigida perdeu a frase de que não faz recomendação. A ressalva passou a ser garantida por código (seção 5.8). Reavaliado depois disso (`eval-local-advice-01.json`), o exemplo teve nota 1,0 em todos os avaliadores aplicáveis.
- **Comparações entre execuções:** código e dataset mudaram juntos. As diferenças não podem ser atribuídas a uma só causa, e um único exemplo mudando de status move as médias.
- **Falha que persiste:** em `error-not-found` ("preço da moeda XYZQWE"), o agente pede esclarecimento sem consultar a API, em vez de buscar o ativo e informar que ele não existe (`tool_selection` 0, `error_handling` 0,5). A guarda da seção 5.8 não cobre o caso, porque o nome não é um ativo conhecido.
- **Viés do juiz:** na execução intermediária, o juiz tirou nota de `memory-restart` porque "a data da fonte é futura". O modelo julga 2026 pelo seu corte de treinamento, o mesmo efeito visto na Fase 1 (o modelo "corrigindo" o período do usuário). É mais um motivo para não depender só do juiz.
- **Latência:** de 10,2 s a 104,0 s por exemplo do agente na execução final, incluindo a verificação e, quando houve, a correção.

Os resumos foram recalculados pelo `scripts/verify_report_numbers.py` a partir das linhas de cada arquivo, com a regra da seção 5.5, e coincidem com os salvos.

### 6.2 Integração final

Execução de `tests/integration/test_phase5_live.py` com Ollama, CoinPaprika, SQLite, Chroma e Mem0 reais em 2026-11-08 (`docs/evidence/final/integration-phase5.log`): **6 passed, 1 skipped** em 403,56 s. O teste pulado é o de trace do LangSmith (variáveis não configuradas).

| Teste | Resultado |
|---|---|
| Tool calling: bind, seleção, execução e incorporação | Aprovado |
| API, SQL, RAG e combinações (api+sql, api+rag) | Aprovado |
| Vários turnos, isolamento, reinício e Mem0 | Aprovado (com a falha de conteúdo da seção 5.3 na pergunta sobre o próprio histórico) |
| NLI com 12 pares reais | Aprovado: acurácia 11/12; 4/4 `entailment`, 4/4 `contradiction`, 3/4 `neutral` (1 neutro classificado como contradição) |
| Correção única de resposta adulterada (preço de US$ 1.000.000) | Aprovado: o valor falso sai da resposta corrigida |
| Cliente Streamlit contra a API real | Aprovado |


### 6.3 Rastreamento no LangSmith (2026-11-12 a 2026-11-13)

Exportação de `docs/evidence/langsmith/summary.json` (`scripts/langsmith_export.py`): **13 turnos reais** do agente integrado com `gemma4:e2b` em CPU, todos perguntas de comparação entre redes (seções 5.9 e 5.10). É uma amostra pequena e de um só tipo de pergunta; os números descrevem esse tipo de turno, não o agente em geral.

| Etapa (por turno) | Mediana | Mínimo | Máximo |
|---|---|---|---|
| Turno completo | 120,3 s | 54,2 s | 206,2 s |
| Agente (`integrated.agent_run`, inclui a reexecução da correção) | 60,4 s | 20,4 s | 96,5 s |
| Verificação (`verification.verify`) | 53,1 s | 33,6 s | 108,0 s |
| — NLI das afirmações conceituais | 48,0 s | 28,3 s | 101,0 s |
| — Coerência (regras + LLM) | 7,8 s | 2,6 s | 23,5 s |
| Ferramentas (CoinPaprika, Chroma) | 0,9 s | 0,0 s | 10,1 s |
| Pré-consulta das comparações (4 turnos) | 1,9 s | 0,8 s | 10,1 s |
| Correção (5 turnos) | 30,0 s | 9,2 s | 40,2 s |

| Custo (por turno) | Mediana | Mínimo | Máximo |
|---|---|---|---|
| Chamadas ao LLM | 10 | 8 | 20 |
| Tokens (total) | 12.589 | 10.151 | 24.537 |
| Tokens do agente | 4.719 | 3.804 | 11.418 |
| Tokens da verificação | 8.090 | 4.770 | 15.583 |

Leitura:
- **A verificação custa tanto quanto a resposta:** mediana de **48% do tempo do turno** (de 37% a 67%) e mais tokens que o próprio agente. Quase todo esse tempo é a NLI, uma chamada ao LLM por afirmação conceitual; comparações entre redes têm muitas afirmações conceituais.
- **As ferramentas não são o gargalo:** menos de 1 s na mediana; o tempo está no LLM local.
- **A correção é frequente nessas perguntas:** 5 de 13 turnos passaram pela correção única, cada uma com mais 30 s na mediana.
- **Resultado:** 7 `verified`, 2 `partially_verified`, 1 `unsupported` (Cardano × Avalanche antes da seção 5.10) e 3 `verification_failed`, todas pelo limite de 400 caracteres da seção 5.11.
- **O que não foi exercitado:** o dataset e os experimentos (`make eval-upload`, `make eval`) não foram criados no LangSmith; a avaliação da seção 6.1 continua sendo a execução local equivalente.

## 7. Limitações conhecidas

- **NLI por LLM de 2B:** confiança autodeclarada, sensível ao texto da evidência e lenta (10 a 40 s de verificação em respostas com várias afirmações conceituais).
- **Checagem numérica por presença:** confere se o número existe na evidência do mesmo ativo, não se foi atribuído à métrica certa.
- **Seleção de evidência lexical:** paráfrases sem palavras em comum podem ficar `neutral`.
- **Uma frase, uma afirmação:** uma frase com dois fatos vira uma afirmação só.
- **Recusas passam como `verified`** (seção 5.3).
- **Resposta cautelosa perde rótulos:** ao recompor a resposta por frases, linhas de métricas em lista perdem o nome do ativo a que se referem (`api-rag-02` na avaliação final).
- **Comparação entre redes com profundidade desigual:** redes descritas na base local (Bitcoin, Ethereum e Solana) têm contratos, execução e finalidade; para as demais, só o perfil da CoinPaprika (consenso e uma descrição curta, em inglês, às vezes defasada). O conteúdo da base (por exemplo, a Alpenglow da Solana) envelhece e precisa de revisão manual. Na resposta real, os tempos de finalidade nem sempre aparecem, embora estejam nos trechos recuperados.
- **Consultas fixas custam latência:** toda comparação entre redes faz 5 consultas (base, perfis e mercado), mesmo quando a pergunta não pede mercado.
- **Ativo inexistente sem consulta:** em "moeda XYZQWE", o agente pede esclarecimento sem consultar a API (seção 6.1).
- **LangSmith exercitado só no rastreamento:** os traces de uso real foram analisados (seções 5.11 e 6.3), mas o dataset e os experimentos não foram criados no LangSmith; a avaliação da seção 6.1 vem da execução local equivalente. Os traces da seção 6.3 são anteriores às correções da seção 5.11.
- **Checagem de ativo e de consenso parcial:** a evidência precisa citar cada ativo da afirmação e o consenso PoS × PoW é conferido por regra, mas o restante do conteúdo continua com a NLI de 2B, que pode aprovar uma frase sobre A a partir de um trecho que fala de A e de outra rede (seção 5.11).
- **Redação do modelo de 2B em comparações:** mesmo com a instrução, ele usa trechos de outra rede (Ethereum no lugar do Bitcoin) e confunde "finalidade" com "propósito".
- **Juiz com viés de autoavaliação:** o mesmo modelo responde e julga; as notas do juiz são indício, não prova.
- **Latência:** um turno leva de 10 s a quase 3 min em CPU; cada correção é mais uma execução do agente.

## 8. Aprendizados

- **Verificar exige separar tipos de afirmação.** Números por código, conceitos por NLI, coerência por regras e só o que sobra pelo LLM. Um verificador único por LLM herdaria os mesmos erros do agente.
- **"Sem evidência" e "contra a evidência" são coisas diferentes,** e o modelo pequeno confunde as duas. O desenho de status (`neutral`, `unsupported`, `verification_failed`) existe para não punir a resposta pela limitação da verificação.
- **Avaliação precisa distinguir falha do sistema de falha da infraestrutura.** Médias com exemplos que nem rodaram contam uma história falsa.
- **Depreciação continua aparecendo no fim:** até a interface tinha um parâmetro depreciado. A auditoria com todos os avisos visíveis deve fazer parte da entrega.
- **O corpus define o que o agente sabe explicar.** Com a regra "conceito só a partir dos trechos", uma lacuna na base não vira resposta errada, vira resposta incompleta (só preço). O formato do documento também importa: uma tabela maior que o chunk separou o título do conteúdo, e o trecho mais próximo da pergunta ficou vazio.
- **Modo JSON é configuração global do LLM.** Uma tarefa nova de texto livre (o título) quebrou em silêncio; o teste com o modelo real revelou o que o dublê não revelaria.

## 9. Pendências

- Repetir a exportação do LangSmith (`scripts/langsmith_export.py`) depois de um período de uso com as correções da seção 5.11, para medir o efeito em mais de uma execução por pergunta.
- Filtrar os trechos da base por rede antes da redação (só os que citam as redes comparadas), já que a instrução sozinha não impede o modelo de usar o Ethereum no lugar do Bitcoin.
- Criar o dataset e rodar a avaliação como experimento no LangSmith (`make eval-upload`, `make eval`).
- Extração de afirmações com divisão de fatos compostos e checagem de atribuição de métrica.
- Resposta cautelosa que preserve a estrutura da resposta (listas e rótulos de ativo).
- Consultar a busca de ativos (`search_crypto_asset`) quando a pergunta citar um nome desconhecido, antes de pedir esclarecimento.
- Incluir a comparação entre redes (seção 5.9) no dataset numa nova versão e repetir a avaliação completa.
- Aprofundar redes fora da base sem escrever documentos: ferramenta de referência (Wikipedia ou documentação oficial) com cache no Chroma, usando as mesmas consultas fixas da seção 5.10.
- NLI com cabeça treinada (Transformers) se houver disco, mantendo a mesma interface `classify`.
- Autenticação e dono por sessão antes de qualquer uso com mais de um usuário.
