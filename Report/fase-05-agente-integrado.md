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

**Adaptado por decisão do autor (2026-11-05):**

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
| `evaluation/phase5_dataset.json` | Dataset versionado (19 exemplos, 9 categorias) |
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

- **Sintoma (2026-11-08, pedido do autor):** a barra lateral só permitia retomar uma conversa digitando o `session_id` (32 caracteres hexadecimais).
- **Correção:**
  - endpoint `GET /conversations` (mais recentes primeiro);
  - título gerado pelo modelo após o 1º turno, na manutenção em segundo plano (sem latência extra), com a primeira pergunta truncada como alternativa;
  - lista clicável na barra lateral, atualizada a cada 15 s;
  - botão "Deploy" do Streamlit oculto (`client.toolbarMode = "minimal"`).
- **Problema encontrado no teste real:** o título caiu sempre na alternativa. O LLM da aplicação roda em modo JSON (`format="json"`) e não devolve texto livre. O prompt passou a pedir `{"title": ...}`, e o parser aceita JSON ou texto. Na repetição, uma conversa sobre ETH recebeu "Preço e Consenso do Ethereum".

### 5.7 Parâmetro depreciado na interface

- **Sintoma:** a auditoria final de depreciação encontrou `use_container_width` nos botões da interface, depreciado no Streamlit 1.65.
- **Correção:** `width="stretch"`. Detalhes da política no capítulo de decisões transversais.

## 6. Evidências e métricas

| Verificação | Resultado |
|---|---|
| Spike de tool calling (`gemma4:e2b`, 2026-11-05) | `bind_tools` seleciona `get_price(BTC)` em 10,4 s; `create_agent` incorpora o resultado em 8,3 s; pergunta dupla chama as duas ferramentas; histórico resolve "meu favorito" |
| Integração da Fase 5, 1ª execução | 5 passed, 1 failed (NLI, seção 5.2), 1 skipped (LangSmith não configurado), em 665,73 s |
| Avaliação de base (`local-baseline`, 19 exemplos, 2026-11-06) | Média de trajetória 0,927; média de resposta 0,869; 0 exemplos com erro |
| Avaliação final (`local-final`, 2026-11-08) | Ver a seção 6.1 |
| Integração final da Fase 5 (2026-11-08) | 6 passed, 1 skipped; ver a seção 6.2 |
| Testes offline (Fases 1–5) | Ver o capítulo de decisões transversais (auditoria de depreciação) |

### 6.1 Avaliação final

Execução local do dataset versão 2 (19 exemplos) em 2026-11-08, com o `gemma4:e2b` como agente, verificador e juiz (`docs/evidence/phase-5/eval-local-final.json`). Os **19 de 19 exemplos** executaram, sem erro de infraestrutura.

| Média | Final | Base (2026-11-06) |
|---|---|---|
| Trajetória (7 avaliadores) | **0,958** | 0,927 |
| Resposta (6 avaliadores) | **0,978** | 0,869 |

| Avaliador | Final | Base |
|---|---|---|
| `groundedness` | 1,000 | 1,000 |
| `factual_consistency` | 1,000 | 0,803 |
| `logical_coherence` | 1,000 | 1,000 |
| `uncertainty` | 1,000 | 0,929 |
| `relevance` | 0,986 | 0,857 |
| `correctness` | 0,879 | 0,686 |
| `tool_selection` | 0,929 | 0,929 |
| `required_tools` / `no_unnecessary_tools` / `tool_combination` / `tool_inputs` | 1,000 | 1,000 |
| `evidence_used` | 0,889 | 0,727 |
| `error_handling` | 0,750 | 0,750 |

Leitura:

- **Nenhum valor inventado:** `groundedness` 1,0 nas duas execuções; todo valor em US$ ou % das respostas existe nas observações reais.
- **Verificação:** os 14 exemplos do agente terminaram `verified` (na base: 8 `verified`, 3 `partially_verified`, 3 `unsupported`). Os 5 exemplos de verificação plantada (contradição, sem suporte, sustentada, cálculo errado e sentido invertido) receberam o status esperado nas duas execuções. Duas respostas passaram pela correção (`ambiguous-01` e `error-not-found`) e foram aprovadas na 2ª verificação.
- **Comparação com a base:** entre as duas execuções mudaram o código da verificação (seção 5.4) e o dataset (versão 1 → 2). A melhora não pode ser atribuída a uma só causa.
- **Falha que persiste:** em `error-not-found` ("preço da moeda XYZQWE"), o agente pediu esclarecimento sem consultar a API, em vez de buscar o ativo e informar que ele não existe (`tool_selection` 0, `error_handling` 0,5).
- **Viés do juiz:** em `memory-restart`, o juiz tirou nota porque "a data da fonte é futura". O modelo julga 2026 pelo seu corte de treinamento, o mesmo efeito visto na Fase 1 (o modelo "corrigindo" o período do usuário). É mais um motivo para não depender só do juiz.
- **Latência:** de 14,0 s a 88,4 s por exemplo do agente, incluindo a verificação.

O resumo foi recalculado pelo `scripts/verify_report_numbers.py` a partir das linhas do arquivo, com a regra da seção 5.5, e coincide com o salvo.

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


## 7. Limitações conhecidas

- **NLI por LLM de 2B:** confiança autodeclarada, sensível ao texto da evidência e lenta (10 a 40 s de verificação em respostas com várias afirmações conceituais).
- **Checagem numérica por presença:** confere se o número existe na evidência do mesmo ativo, não se foi atribuído à métrica certa.
- **Seleção de evidência lexical:** paráfrases sem palavras em comum podem ficar `neutral`.
- **Uma frase, uma afirmação:** uma frase com dois fatos vira uma afirmação só.
- **Recusas passam como `verified`** (seção 5.3).
- **LangSmith não exercitado neste ambiente:** o rastreamento e o runner estão implementados e testados offline, mas as variáveis `LANGSMITH_*` não foram configuradas; o teste de trace foi pulado e os resultados são da execução local equivalente.
- **Juiz com viés de autoavaliação:** o mesmo modelo responde e julga; as notas do juiz são indício, não prova.
- **Latência:** um turno leva de 14 s a quase 3 min em CPU; cada correção é mais uma execução do agente.

## 8. Aprendizados

- **Verificar exige separar tipos de afirmação.** Números por código, conceitos por NLI, coerência por regras e só o que sobra pelo LLM. Um verificador único por LLM herdaria os mesmos erros do agente.
- **"Sem evidência" e "contra a evidência" são coisas diferentes,** e o modelo pequeno confunde as duas. O desenho de status (`neutral`, `unsupported`, `verification_failed`) existe para não punir a resposta pela limitação da verificação.
- **Avaliação precisa distinguir falha do sistema de falha da infraestrutura.** Médias com exemplos que nem rodaram contam uma história falsa.
- **Depreciação continua aparecendo no fim:** até a interface tinha um parâmetro depreciado. A auditoria com todos os avisos visíveis deve fazer parte da entrega.
- **Modo JSON é configuração global do LLM.** Uma tarefa nova de texto livre (o título) quebrou em silêncio; o teste com o modelo real revelou o que o dublê não revelaria.

## 9. Pendências

- Configurar o LangSmith e repetir a avaliação como experimento rastreado.
- Extração de afirmações com divisão de fatos compostos e checagem de atribuição de métrica.
- Não marcar declarações de limitação na resposta cautelosa.
- NLI com cabeça treinada (Transformers) se houver disco, mantendo a mesma interface `classify`.
- Autenticação e dono por sessão antes de qualquer uso com mais de um usuário.
