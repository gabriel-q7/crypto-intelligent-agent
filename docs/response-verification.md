# Verificação de respostas — Fase 5

Toda resposta do agente integrado é verificada **antes** de ser entregue: consistência factual contra a evidência real de execução (NLI) e coerência lógica. A verificação pode pedir **uma** correção.

Código:
- `app/verification/claims.py`: extração de afirmações;
- `app/verification/evidence.py`: evidência e seleção;
- `app/verification/nli.py`: classificação;
- `app/verification/logic.py`: coerência lógica;
- `app/verification/policy.py`: status, correção e resposta cautelosa;
- `app/verification/service.py`: orquestração;
- `app/verification/schemas.py`: resultado estruturado.

## 1. Entrada e evidência

`VerificationService.verify(question, answer, observations)` recebe a pergunta, a resposta e as **observações reais** das ferramentas desta execução (JSON da CoinPaprika, linhas do SQLite, trechos do Chroma com metadados, resultado de cálculo, erros). Ela não recebe a resposta como evidência.

`build_evidence` converte cada observação em `EvidenceItem` com:
- texto legível (para a NLI);
- números (para a checagem determinística);
- entidades (ativos);
- proveniência: `provider`, `endpoint`, `source_updated_at` e `retrieved_at`, ou `chunk_id`/`source`/`title` dos documentos.

Falhas de ferramenta viram evidência do tipo `tool_error`. Elas servem para checar se a resposta admitiu a limitação, e nunca sustentam afirmações.

## 2. Extração de afirmações

A extração é determinística (`extract_claims`):
- a resposta é dividida em frases (e em partes, por `;`), com o markdown removido;
- ficam de fora: perguntas, frases com menos de 4 palavras, declarações de limitação ("não foi possível…", "não há dados…"), recomendações, especulação explícita e meta-texto;
- tipo: `numeric` (cita US$ ou %), `conclusion` (compara ou conclui) ou `conceptual`;
- cada afirmação guarda as entidades e o índice da frase original: a resposta original é preservada.

O limite é de 8 afirmações por resposta. A extração por LLM foi descartada: seria mais uma chamada de 5 a 15 s por turno em CPU, e as respostas do agente já são curtas (2 a 6 frases, pelo prompt).

## 3. Seleção de evidência

Para cada afirmação, `select_evidence` escolhe até 3 evidências (`VERIFICATION_EVIDENCE_PER_CLAIM`) por pontuação:
- +3 se o ativo coincide (−2 se a evidência é de outro ativo);
- +1 por palavra de conteúdo em comum;
- +1 se o tipo combina (afirmação numérica com API/SQL/cálculo; conceitual com documento).

Não há comparação de todas as afirmações com todas as evidências. Os ids das evidências usadas ficam no resultado, e o conjunto completo (`evidence`), com a proveniência, também.

## 4. Classificação: entailment, contradiction, neutral

| Rótulo | Significado |
|---|---|
| `entailment` | A evidência afirma o conteúdo da afirmação |
| `contradiction` | A evidência afirma algo incompatível |
| `neutral` | A evidência não basta para confirmar nem para negar. **Não é contradição** |

Duas etapas:

1. **Checagem numérica determinística** (afirmações `numeric`). Cada valor citado precisa estar nos números das evidências selecionadas (tolerância de 1%, a mesma de `verify_figures`; percentuais também pelo módulo).
   - Todos presentes → `entailment` (1,0).
   - Algum ausente, com evidência do **mesmo ativo** → `contradiction` (0,9): a fonte tem o dado, e ele é outro.
   - Algum ausente, sem evidência comparável → `neutral`.

   Modelos de 2B erram comparações numéricas (lição das Fases 1–4), então números não passam pelo LLM.
2. **NLI com LLM local** (afirmações sem valores). O `gemma4:e2b` via Ollama (`OLLAMA_MODEL`, modo JSON, temperatura 0) recebe só a hipótese e as evidências selecionadas e devolve `{label, confidence, rationale}`, validado por Pydantic. Rótulo fora do conjunto ou JSON inválido levam a `NLIUnavailableError`, que é falha de infraestrutura e **não** contradição.

Sem evidência selecionada, o resultado é `neutral` com `method=no_evidence`, sem chamar o LLM.

### 4.1 Por que não Transformers (desvio do enunciado)

O enunciado sugeria `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` via Transformers. Por decisão do usuário (2026-11-05), a NLI usa o `gemma4:e2b` já servido pelo Ollama:

| | Transformers + mDeBERTa | gemma4:e2b via Ollama (adotado) |
|---|---|---|
| Dependências novas | torch para CPU (≈ 0,7–0,9 GB instalado) + transformers + modelo (≈ 1 GB) | Nenhuma |
| Disco livre na máquina | 5,3 GB | — |
| Motor de inferência | Segundo motor, na mesma CPU | O mesmo do agente |
| Latência por par | Baixa (dezenas a centenas de ms, estimativa não medida) | 5–15 s por afirmação conceitual (medido) |
| Rótulos | Cabeça treinada em MNLI/XNLI (calibrada) | Gerados pelo LLM com instrução; confiança autodeclarada |
| Português | Multilíngue (XNLI) | Multilíngue |

Consequências registradas:
- a confiança não é uma probabilidade calibrada;
- a latência por turno é maior (por isso números vão para a checagem determinística e a extração não usa LLM);
- a qualidade foi medida com casos reais (teste `test_real_nli_labels`, 12 pares reais; ver [Report/fase-05-agente-integrado.md](../Report/fase-05-agente-integrado.md), seção 5.2) e não é presumida.

A interface (`LLMNLIClassifier.classify(claim, evidence) → ClaimVerification`) permite trocar por um classificador Transformers sem mexer no resto.

## 5. Coerência lógica (separada da NLI)

**Regras determinísticas** (`rule_findings`), sobre os valores estruturados das observações:

| Achado | Severidade | Detecta |
|---|---|---|
| `direction_mismatch` | error | "X subiu N%" quando a variação real é negativa (ou o contrário). A verificação de números das Fases 2–4 compara o módulo e não pegava isso |
| `numeric_comparison` | error | "A maior/menor que B" numa métrica cujos valores dizem o contrário |
| `calculation` | error | Dois valores em US$ e um percentual que não corresponde a (b−a)/a |
| `period_mismatch` | warning | A pergunta pede 24h/7d/30d e a resposta usa só outra janela sem explicar |
| `ignored_limitation` | error | Houve falha de ferramenta ou cobertura insuficiente e a resposta não admite limitação alguma |

**LLM** (`llm_findings`, `gemma4:e2b`): contradição interna entre frases (`internal_contradiction`, error) e conclusão que não decorre do que foi dito (`unsupported_conclusion`, warning). Os achados do LLM que não são contradição interna ficam como warning, porque o modelo gera falsos positivos (observado: "falta justificativa para o preço"). Pode ser desligado com `VERIFICATION_COHERENCE_LLM=false`.

## 6. Status geral

| Status | Quando |
|---|---|
| `verified` | Toda afirmação `entailment` e nenhum erro lógico (ou nada factual foi afirmado) |
| `partially_verified` | Parte sustentada e parte `neutral`, sem contradição. Também quando há limitação ignorada e o resto está sustentado |
| `unsupported` | Nenhuma afirmação sustentada (todas `neutral`), sem contradição |
| `contradicted` | Alguma `contradiction` ou erro lógico de sentido, comparação, cálculo ou contradição interna |
| `verification_failed` | A verificação não rodou (LLM fora do ar, saída inválida). A resposta é entregue com o aviso "não foi verificada", **nunca** como verificada nem como contradita |

`ignored_limitation` não leva a `contradicted`: afirmar algo sem dados é falta de sustentação, não contradição com a evidência.

## 7. Política de correção (no máximo 1)

| Resultado | Comportamento |
|---|---|
| `verified` | Entrega normal |
| `partially_verified` / `unsupported` | 1 correção: qualificar ou remover o que não foi sustentado |
| `contradicted` | 1 correção com a afirmação, o motivo e a evidência que a contradiz |
| `verification_failed` | Sem correção; entrega com o aviso de "não verificada" |
| Resposta final vazia (qualquer status, exceto `verification_failed`) | 1 correção: escrever a resposta a partir das observações já obtidas |
| Recusa sem consulta: a pergunta cita ativos (`find_assets`), nenhuma ferramenta foi chamada e a resposta recusa ou devolve a pergunta (`skipped_lookup`) | 1 correção: chamar `get_market_data` para cada ativo citado; em pedido de recomendação, apresentar os dados e declinar só a recomendação |

As duas últimas linhas existem porque uma resposta sem afirmações sai `verified` e não acionaria a correção. Origem: caso real de 2026-11-08 ("Qual devo investir: SOL ou ETH?"), descrito no diário da Fase 5, seção 5.8.

Mecânica (`IntegratedAgentService`):
1. o feedback estruturado (`correction_instructions`) vira uma mensagem para o agente;
2. o agente recebe junto as próprias chamadas e observações do 1º passo, então **não repete** chamadas à API ou ao banco quando a evidência ainda vale;
3. se ele chamar outra ferramenta, a chamada aparece na trajetória e a observação entra na evidência da 2ª verificação;
4. a resposta corrigida é verificada de novo; `corrected_passed = (status == "verified")`;
5. se não passou: `cautious_answer` remove as frases contraditas, marca as não sustentadas com "(não confirmado pelas fontes consultadas)" e acrescenta um aviso com as pendências.

Não há segunda correção, porque `MAX_CORRECTIONS = 1` e não existe laço. O teste `test_failed_correction_yields_cautious_answer_and_no_second_retry` confere exatamente 2 verificações.

As duas respostas ficam registradas: `AgentReply.original_answer`, `verification.original_status`, `correction_attempted` e `corrected_passed`.

## 8. Limitações conhecidas

- **NLI por LLM de 2B:** confiança autodeclarada (não calibrada), sensível ao texto da evidência e lenta em CPU. Em respostas com várias afirmações conceituais, a verificação leva de 10 a 40 s.
- **Extração por frase:** uma frase com dois fatos vira uma afirmação só; se um deles não for sustentado, a frase inteira fica `neutral`.
- **Checagem numérica por presença de valor:** ela confere se o número existe na evidência do mesmo ativo, não se ele foi atribuído à métrica certa (um "volume" citado com o valor do market cap passaria).
- **Seleção de evidência lexical:** paráfrases sem palavras em comum podem ficar sem evidência (`neutral`).
- **Coerência via LLM:** gera warnings espúrios; só `internal_contradiction` pesa no status.
- **Custo:** cada correção é mais uma execução do agente (10–60 s) e mais uma verificação.
