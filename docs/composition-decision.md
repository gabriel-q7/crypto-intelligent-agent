# Decisão de composição: LCEL vs `@chain`

A mesma chain existe de duas formas em [`app/chains/analysis.py`](../app/chains/analysis.py). Ambas recebem `CryptoQuestion | dict` e retornam `CryptoAnalysisRequest`; os testes (`tests/unit/test_chains.py`) rodam o mesmo conjunto contra as duas e verificam que o resultado é idêntico.

## LCEL (padrão)

```python
build_json_chain(llm) = (
    RunnableLambda(build_prompt_input) | ANALYSIS_PROMPT | llm | JsonOutputParser()
)
build_lcel_chain(llm) = build_json_chain(llm) | RunnableLambda(validate_analysis)
```

## Imperativa (`@chain`)

```python
@chain
def crypto_analysis_imperative(question):
    prompt_input = build_prompt_input(question)
    messages = ANALYSIS_PROMPT.invoke(prompt_input)
    message = llm.invoke(messages)
    data = parser.invoke(message)
    return validate_analysis(data)
```

## Comparação

| Critério | LCEL (`\|`) | `@chain` |
|---|---|---|
| Legibilidade do fluxo linear | Pipeline explícito, um passo por etapa | Código Python comum, fácil para quem não conhece LangChain |
| Streaming de tokens/parciais | **Sim**: cada etapa repassa chunks (`JsonOutputParser` emite dicts parciais) | **Não**: a função só retorna no fim; `stream` emite um único chunk |
| `batch` / `async` | Nativos e otimizados por etapa | Herdados do `Runnable`, mas a função inteira roda por item |
| Reuso de subpartes | `build_json_chain` é reaproveitada no streaming | Exige extrair funções manualmente |
| Tracing / callbacks | Cada etapa aparece como nó próprio | Aparece como um nó com filhos (chamadas internas a runnables são rastreadas) |
| Lógica condicional, laços, try/except | Exige `RunnableBranch`, `RunnableLambda` etc. | Natural (if/for/try) |
| Inspeção (`get_graph`, schemas de entrada/saída) | Completa | Limitada |

## Decisão

**LCEL é o padrão** (`DEFAULT_COMPOSITION = "lcel"`), porque:

1. A chain da Etapa 1 é **linear** — o caso ideal para LCEL; `@chain` não traz ganho de expressividade aqui.
2. **Streaming** é requisito: só a versão LCEL emite JSON parcial; `@chain` bloqueia até o fim.
3. **Composição incremental**: `build_json_chain` é a base do streaming e a validação é só mais um `|`. As próximas etapas (ferramentas, executor) podem ser acopladas como novos runnables.
4. `batch`, `async`, retries (`with_retry`) e fallbacks (`with_fallbacks`) vêm prontos por etapa.

`@chain` permanece disponível (`--composition imperative`) e é a escolha indicada quando o fluxo ganhar **lógica imperativa** (laços de ferramenta, decisões condicionais complexas, tratamento de erro passo a passo) — e pode ser combinado com LCEL, já que o resultado também é um `Runnable`.
