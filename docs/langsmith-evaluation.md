# LangSmith: rastreamento e avaliação (Fase 5)

## 1. Configuração

O SDK `langsmith` 0.14.17 já vem como dependência do `langchain-core`; não há dependência nova. As variáveis ficam no `.env` (ignorado pelo git) e entram no container por `env_file`:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<chave>          # nunca commitar
LANGSMITH_PROJECT=crypto-intelligence-agent
# LANGSMITH_ENDPOINT=...           # só para instância própria / região
```

Sem essas variáveis, a aplicação e a suíte offline funcionam normalmente: os decoradores `@traceable` não fazem nada e o teste de tracing é pulado.

## 2. O que é rastreado

Cada turno do agente integrado gera um trace com raiz em `integrated.turn`:

```
integrated.turn                      metadados: phase=5, engine=integrated_agent, session=<sha256[:12]>, model
├─ integrated.agent_run
│   └─ crypto_integrated_agent       (grafo create_agent: chamadas ao LLM, tool calls, ToolMessages)
│       ├─ ChatOllama (gemma4:e2b)
│       ├─ get_market_data / query_market_database / search_knowledge_base / ...   entrada e saída de cada ferramenta
├─ verification.verify
│   ├─ verification.claims_nli       (ChatOllama em modo JSON, por afirmação conceitual)
│   └─ verification.logic            (regras + ChatOllama de coerência)
└─ integrated.correction             (quando a política pede; mais uma execução do grafo)
    └─ verification.verify           (2ª verificação)
```

Dados sensíveis:
- o `session_id` real não vai para os metadados, só um hash curto;
- a chave da CoinPaprika (se existir) já é redigida nos logs (Fase 2) e não passa pelas ferramentas;
- o conteúdo das perguntas e respostas vai para o LangSmith, porque é o objeto da avaliação.

Tags: `phase5`, mais `evaluation` (runner) ou `integration-test` (testes).

## 3. Dataset

O arquivo [`evaluation/phase5_dataset.json`](../evaluation/phase5_dataset.json) é a fonte versionada (`crypto-agent-phase5`, versão 3, **20 exemplos**). O LangSmith recebe uma cópia: `make eval-upload` cria o dataset `crypto-agent-phase5-v<versão>` (hoje `crypto-agent-phase5-v3`). O id de cada exemplo é um UUID5 do id local, então reenviar o mesmo dataset não duplica nada.

| Categoria | Exemplos | O que exercita |
|---|---|---|
| `api` | api-01, api-02 | Preço atual; dominância (visão global) |
| `sql` | sql-01, sql-02 | Ranking por market cap; filtro de 7 dias no banco |
| `rag` | rag-01, rag-02 | PoS; halving com vocabulário diferente do corpus (caso da Fase 4) |
| `api+sql` | api-sql-01 | Preço ao vivo × média dos snapshots |
| `api+rag` | api-rag-01, api-rag-02 | Preço + consenso; comparação BTC × ETH + PoW × PoS (falhava no ReAct da Fase 4) |
| `factual_verification` | verify-contradiction, verify-unsupported, verify-supported | Afirmação plantada contradita, sem suporte e sustentada (valor real preenchido) |
| `logical_verification` | logic-calculation, logic-direction | Cálculo errado; sentido invertido (subiu × caiu) com o valor real |
| `memory` | memory-followup, memory-restart | Follow-up pelo histórico; contexto depois de "reinício" |
| `error_handling` | error-not-found, error-coverage, ambiguous-01 | Ativo inexistente; cobertura insuficiente (90 dias); pergunta ambígua |
| `advice` | advice-01 | Pedido de recomendação com ativos citados: consulta os dados e declina a recomendação (versão 3, caso real da interface) |

Cada exemplo traz em `outputs` só o comportamento esperado, sem texto exato:
- `expected_categories`, `required_tools`, `forbidden_tools` e `expected_assets`;
- `expected_error_kinds` e `uncertainty_expected`;
- `answer_must_include_any`, a lista de traços mínimos;
- `criteria` (critério em linguagem natural);
- para os casos de verificação: `expected_verification`, `expected_labels` e `expected_findings`.

Os exemplos de verificação (`mode=verify`) executam as ferramentas **de verdade** para obter a evidência. Os marcadores da resposta plantada, como `{BTC.price_usd_fmt}`, são preenchidos com os valores reais do momento: o caso "sustentado" é sustentado de fato, e o "sentido invertido" usa o valor real com o sinal trocado.

Os exemplos de memória gravam a preparação (`setup`) no SQLite e, em `memory-restart`, criam um novo serviço e um novo `ConversationStore` sobre o mesmo arquivo antes da pergunta.

## 4. Avaliadores

Implementação: `app/evaluation/evaluators.py`. Assinatura LangSmith `(inputs, outputs, reference_outputs) → {key, score, comment}`. Avaliador que não se aplica ao exemplo devolve `score=None` e não entra na média.

**Resposta**

| Chave | Tipo | Definição |
|---|---|---|
| `correctness` | LLM-juiz + regra | Nota do juiz para "cumpre o critério"; limitada a 0,5 se faltar todos os traços de `answer_must_include_any` |
| `relevance` | LLM-juiz | A resposta trata da pergunta |
| `groundedness` | Determinístico | Fração dos valores (US$/%) da resposta que existem nas observações reais (tolerância de 1%) |
| `factual_consistency` | Determinístico | Agente: verified = 1, partially_verified = 0,5, unsupported = 0,25, demais = 0. Casos de verificação: status e rótulos esperados |
| `logical_coherence` | Determinístico | Agente: sem erro lógico na resposta final. Casos lógicos: o achado esperado foi detectado |
| `uncertainty` | Determinístico | Comunica limitação quando esperado; sem ressalva desnecessária quando não |

**Trajetória** (independente da resposta, a partir das chamadas reais)

| Chave | Definição |
|---|---|
| `tool_selection` | Recall das categorias esperadas (api/sql/rag); sem categoria esperada → 1 se nenhuma ferramenta foi chamada |
| `required_tools` | Fração das ferramentas obrigatórias chamadas |
| `no_unnecessary_tools` | 1 se nenhuma ferramenta proibida foi chamada |
| `tool_combination` | Exemplos multi-categoria: todas as categorias na mesma execução |
| `tool_inputs` | Fração dos ativos esperados presentes nos argumentos das chamadas |
| `error_handling` | 0,5 se o erro esperado ocorreu + 0,5 se a resposta comunicou a limitação |
| `evidence_used` | A resposta cita algum valor que veio das ferramentas |

**Juiz:** `gemma4:e2b` local, o mesmo modelo do agente, em modo JSON, para não depender de modelo externo. Limitações:
- ele julga a resposta do próprio modelo, então há viés de autoavaliação;
- é um modelo de 2B, com notas pouco calibradas;
- não confere números; por isso existem `groundedness` e `factual_consistency` determinísticos.

A nota do juiz é um indício, não prova de correção.

## 5. Execução

```bash
make eval-upload                       # dataset no LangSmith
make eval PREFIX=fase5-inicial         # experimento (alvo real: Ollama, CoinPaprika, SQLite, Chroma, Mem0)
make eval-local PREFIX=local-baseline  # mesmo fluxo sem LangSmith
```

O alvo (`app/evaluation/target.py`) usa o `AppContainer` real, cria uma sessão nova por exemplo e roda com `max_concurrency=1`, porque o Ollama em CPU não paraleliza bem. Os resultados vão para `docs/evidence/phase-5/eval-<prefixo>.json`, com nota por exemplo, médias por avaliador e por categoria e, no modo LangSmith, nome e URL do experimento e `run_id`/`trace_id` de cada exemplo.

## 6. Resultados

Ver o diário da Fase 5 ([Report/fase-05-agente-integrado.md](../Report/fase-05-agente-integrado.md), seções 5.5 e 6). Arquivos: `docs/evidence/phase-5/eval-local-baseline.json` (dataset v1), `eval-local-final.json` (v2), `eval-local-final-v3.json` (v3, resultado final), `eval-local-advice-01.json` (reavaliação do `advice-01`) e `eval-local-after-fixes.json` (este último com 8 exemplos interrompidos por falha de infraestrutura; não usar como resultado).

Os exemplos que falham ao executar (Ollama fora, DNS, timeout) ficam fora das médias e aparecem em `summary.errored`; `summary.n_scored` informa quantos foram avaliados.

## 7. Exportação e análise dos traces

`scripts/langsmith_export.py` lê o projeto `LANGSMITH_PROJECT` pela API atual do SDK (`client.runs.query`, assíncrona; `list_runs` está depreciado) e grava em `docs/evidence/langsmith/`:

- `turns.jsonl`: um turno real por linha, com pergunta, status, verificação, correção, ferramentas, latência por etapa (`prefetch_s`, `agent_s`, `verification_s`, `nli_s`, `coherence_s`, `correction_s`, `tool_s`), chamadas ao LLM e tokens (do agente e da verificação), erros do verificador e a resposta;
- `summary.json`: agregados (mínimo, mediana, média, máximo) e o inventário do projeto.

```bash
docker compose run --rm --no-deps -v ./scripts:/app/scripts -v ./docs:/app/docs app python scripts/langsmith_export.py
```

"Turno real" é um trace `integrated.turn` com pelo menos uma chamada `llm` ao `ChatOllama`. Os turnos da suíte offline (dublês de LLM) ficam fora da análise e só entram na contagem do inventário. Cada ferramenta aparece duas vezes no trace (a estruturada da Fase 5 delega à de entrada única); a exportação conta só a externa.

Resultados e defeitos encontrados: diário da Fase 5, seções 5.11 e 6.3.

**Atenção:** com `LANGSMITH_TRACING=true` no `.env`, o `make test` também envia traces, porque o container carrega o `.env`. Para não misturar testes com uso real, desligue o tracing na suíte offline (pendência registrada na Fase 5).
