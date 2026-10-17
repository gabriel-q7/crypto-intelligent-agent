# Fase 1 — Chain estruturada com LangChain e Ollama

| | |
|---|---|
| **Período** | 2026-10-17 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose 5.5.1, inferência em **CPU** (12 threads, sem GPU) |
| **Modelo** | `qwen2.5:7b` via `ollama/ollama:latest` (4,7 GB) |
| **Versões** | Python 3.12.15 (container), `langchain-core 1.6.12`, `langchain-ollama 1.1.0`, `pydantic 2.14.1`, `pytest 9.1.1` |

## 1. Objetivo

Construir uma chain de ponta a ponta que **interprete** perguntas sobre criptomoedas e produza um JSON estruturado e validado, **sem consultar APIs externas**.

O sistema não responde a pergunta do usuário. Ele produz um **plano de análise**: intenção, ativos, métricas necessárias, período, etapas de investigação, lacunas, premissas e confiança. Esse plano é o contrato de entrada das fases seguintes, quando ferramentas e APIs passarão a executar as etapas.

Critérios de conclusão:

- análise executada pela CLI, retornando JSON validado;
- mesma chain em duas composições (LCEL e `@chain`);
- demonstração de `invoke`, `batch` e `stream`;
- execução local com Docker Compose.

Todos foram atendidos (seção 6).

## 2. Escopo

**Dentro:** schemas Pydantic, prompts, `ChatOllama`, `JsonOutputParser` com validação, duas composições, interface Runnable, Docker Compose, testes unitários e de integração, documentação.

**Fora, por decisão de fase:** integrações com CoinPaprika, DeFiLlama e CoinGecko; memória persistente; RAG; ferramentas; planejamento autônomo. O objetivo era isolar e medir a qualidade da **interpretação** antes de acoplar dados reais.

## 3. Solução implementada

```
CryptoQuestion ──► build_prompt_input ──► ANALYSIS_PROMPT ──► ChatOllama ──► JsonOutputParser ──► validate_analysis ──► CryptoAnalysisRequest
 (Pydantic)        (PromptTemplate +      (ChatPromptTemplate:   (format=json,   (texto → dict,       (dict → modelo,
                    data de referência)    system + few-shot      temperature=0)  parciais no stream)  erros de domínio)
                                           + pergunta)
```

| Camada | Arquivo | Responsabilidade |
|---|---|---|
| Schemas | [app/schemas/](../app/schemas/) | `CryptoQuestion` (entrada) e `CryptoAnalysisRequest` (saída), com normalização de tickers e enum de intenção |
| Prompts | [app/prompts/analysis.py](../app/prompts/analysis.py) | Instruções de sistema, `PromptTemplate` da pergunta, exemplo few-shot, vocabulário de métricas |
| Chains | [app/chains/analysis.py](../app/chains/analysis.py) | `build_lcel_chain` (padrão), `build_imperative_chain` (`@chain`), `build_json_chain` (streaming) |
| Serviços | [app/services/](../app/services/) | Fábrica do `ChatOllama`, validação final, `run_analysis` / `run_batch` / `stream_analysis` com tradução de erros |
| Erros | [app/errors.py](../app/errors.py) | `AnalysisOutputError` (saída inválida, guarda `raw_output`) e `LLMUnavailableError` (Ollama inacessível) |
| Entrada | [scripts/](../scripts/) | `analyze.py` (CLI) e `demo_runnables.py` (invoke, batch, stream) |
| Infra | [compose.yaml](../compose.yaml), [Dockerfile](../Dockerfile) | Serviços `ollama`, `ollama-pull` (baixa o modelo uma vez) e `app`; volume `ollama_models` |

## 4. Decisões técnicas

| Decisão | Alternativas consideradas | Motivo |
|---|---|---|
| **LCEL como composição padrão** | `@chain` (imperativa) | A chain é linear. Só a LCEL propaga streaming etapa a etapa (o `@chain` emite um único chunk no fim), e ela permite reaproveitar subpartes (`build_json_chain`) e usar `batch`/`with_retry`/`with_fallbacks` em cada etapa. Detalhes em [docs/composition-decision.md](../docs/composition-decision.md). |
| `format="json"` no `ChatOllama` + `temperature=0` | Só instrução no prompt | Restringe a geração a JSON sintaticamente válido e reduz a variação entre execuções. Não garante aderência ao schema, por isso existe a validação Pydantic. |
| `JsonOutputParser` + validação Pydantic separada | `with_structured_output` / `PydanticOutputParser` | O `JsonOutputParser` emite objetos parciais durante o streaming (requisito da fase); a validação estrita fica isolada em um passo próprio, testável sem modelo. |
| Erros de domínio (`AnalysisOutputError`, `LLMUnavailableError`) | Propagar exceções do LangChain/httpx | O chamador não precisa conhecer exceções de infraestrutura; `raw_output` preserva a saída do modelo para diagnóstico. |
| `batch` com `return_exceptions=True` | Falhar o lote inteiro | Uma resposta inválida do modelo não descarta as outras. |
| Serviço `ollama-pull` no Compose | Baixar o modelo manualmente | Subida reprodutível com um comando; o volume persistente evita novo download. |
| `requirements.txt` único (inclui pytest) | Separar dependências de dev | Menos arquivos; os testes rodam no mesmo container da aplicação. |
| Validação toda em container Python 3.12 | venv local | O host tem apenas Python 3.14; o requisito é 3.12. |

## 5. Problemas encontrados e como foram resolvidos

### 5.1 Import circular entre `app/chains` e `app/services`

- **Sintoma (identificado na revisão do código, antes da execução):** `app/chains` importa `app.services.validation`; se `app/services/__init__.py` reexportar `app.services.analysis`, que importa `app.chains`, o pacote é importado parcialmente.
- **Correção:** `app/services/__init__.py` sem reexports; quem usa importa o módulo diretamente.

### 5.2 Aviso de permissão do cache do pytest no container

- **Sintoma:** `PytestCacheWarning: ... Permission denied: '/app/...'`.
- **Causa:** a aplicação roda como usuário sem privilégios (`appuser`), mas `/app` pertencia ao root.
- **Correção:** `COPY --chown=appuser:appuser` e `chown` do diretório no [Dockerfile](../Dockerfile).

### 5.3 Qualidade da saída do modelo — revisão iterativa do prompt

Problema principal da fase. A chain funcionava (JSON válido, schema respeitado), mas o **conteúdo** do plano tinha falhas. Pergunta de referência: `"Compare BTC e ETH" --assets BTC ETH --period "últimos 30 dias"`.

Saída inicial:

```json
{
  "requested_metrics": ["rendimento", "volatilidade"],
  "investigation_steps": ["calcular o rendimento médio dos últimos 30 dias", "calcular a volatilidade...", "comparar..."],
  "missing_information": [],
  "assumptions": ["moeda de cotação USD"],
  "confidence": 1.0
}
```

Diagnóstico:

| Campo | Falha | Causa raiz |
|---|---|---|
| `investigation_steps` | Os cálculos começavam sem nenhum passo para obter os dados | Nenhuma regra exigia uma etapa de coleta |
| `requested_metrics` | Termos vagos e instáveis entre execuções ("rendimento", "preço_atual") e sem o dado de base | Texto livre, sem vocabulário |
| `missing_information` | Dado de mercado aparecia às vezes aqui, às vezes em `requested_metrics` | Regra ambígua no prompt ("liste em requested_metrics e, se faltar, em missing_information") |
| `assumptions` | Critério de comparação inferido e não registrado | Nenhuma regra pedia o registro de critérios inferidos |
| `confidence` | 1.0 com critério inferido | Sem régua de calibração |

Cada iteração foi validada com 5 perguntas reais: comparação, preço atual/previsão, TVL do Aave, staking de ETH e risco de SOL.

| # | Mudança | Efeito na pergunta de referência | Efeito colateral |
|---|---|---|---|
| 1 | Regras novas: separação `requested_metrics` × `missing_information`, coleta como primeira etapa, régua de confiança, vocabulário `METRIC_VOCABULARY` | Etapa de coleta presente, critério registrado, confiança 0.8 | `price` em vez de `price_history`; moeda/granularidade ausentes |
| 2 | Exemplo few-shot de **risco** (SOL × AVAX, sem período) | `price_history` correto | **Regressão:** `assumptions` vazio e `confidence` 1.0 de novo |
| 3 | Exemplo few-shot de **comparação** com período informado (padrão mais comum) | Resolvido: 3 premissas, confiança 0.8 | `assets: []` em perguntas que citavam BTC/ETH só no texto |
| 4 | No exemplo, ativos citados só no texto (campo "Ativos informados" vazio) | Estável | Modelo "corrigiu" o período: *"período de 2026 é um erro, deve ser 2023"* |
| 5 | **Data de referência** injetada no prompt e regra para nunca alterar o período do usuário | Estável (3 execuções idênticas) | Ver limitações (seção 7) |

Diagnóstico das iterações 2 a 4:

- **Iteração 2:** o modelo segue mais o padrão do exemplo do que as regras abstratas. Um exemplo com outro perfil de pergunta (sem período) não ensinou o caso com período explícito.
- **Iteração 3:** o modelo aprendeu a **copiar** os ativos do campo estruturado do exemplo, em vez de extraí-los do texto.
- **Iteração 4:** sem saber a data atual, o modelo assume a data de corte do treino e trata 2026 como erro. É um risco direto ao requisito de "não inventar dados".

Saída final para a pergunta de referência:

```json
{
  "intent": "comparison",
  "assets": ["BTC", "ETH"],
  "requested_metrics": ["price_history", "return_pct", "volatility", "max_drawdown"],
  "period": "últimos 30 dias",
  "investigation_steps": [
    "Obter price_history diário de BTC e ETH em USD nos últimos 30 dias",
    "Calcular return_pct, volatility e max_drawdown de cada ativo",
    "Comparar desempenho e risco entre BTC e ETH"
  ],
  "missing_information": [],
  "assumptions": [
    "comparação por retorno e risco, pois o critério não foi informado",
    "cotação em USD",
    "preços de fechamento diários"
  ],
  "confidence": 0.8
}
```

## 6. Evidências e métricas

Detalhes completos e saídas brutas: [docs/stage-1-evidence.md](../docs/stage-1-evidence.md).

| Verificação | Comando | Resultado |
|---|---|---|
| Testes unitários (LLM falso, sem modelo) | `docker compose run --rm --no-deps app pytest` | **63 passed** em ~0,6 s |
| Testes de integração (Ollama + `qwen2.5:7b`) | `docker compose run --rm app pytest -m integration` | **4 passed**: 173,9 s com o prompt inicial; 255,5 s com o prompt final |
| CLI fim a fim | `docker compose run --rm app` | JSON validado; ~110 s na 1ª chamada (inclui carga do modelo) |
| `invoke` LCEL × `@chain` | `python -m scripts.demo_runnables invoke` | Saídas **idênticas** para a mesma pergunta |
| `batch` (3 perguntas, concorrência 2) | `python -m scripts.demo_runnables batch` | 3/3 válidas |
| `stream` | `python -m scripts.demo_runnables stream` | 53 objetos parciais; o objeto final validado é igual ao do `invoke` |
| Não inventar dados | "BTC vai subir amanhã? Qual o preço atual?" | Nenhum número de mercado; o preço vira métrica a buscar |

Cobertura dos testes unitários: schemas (normalização, obrigatórios, limites), prompts (tipos, regras anti-invenção, escape do JSON schema, few-shot válido, data de referência), parsing e tradução de erros, **as duas composições com o mesmo conjunto de testes**, equivalência LCEL × `@chain`, streaming parcial, isolamento de falhas no `batch`, configuração por variáveis de ambiente e `LLMUnavailableError` com o Ollama inacessível.

**Custo do few-shot:** a integração passou de 174 s para 256 s (+47%) por causa do prompt mais longo, inferido em CPU.

## 7. Limitações conhecidas

1. **O streaming só existe antes da validação.** O Pydantic precisa do objeto completo; os objetos parciais não são validados e as chains com validação emitem um único chunk.
2. **Variação do modelo 7B após a revisão do prompt:**
   - `assets` oscila em perguntas conceituais ("O que é staking de ETH?": `[]` em uma execução, `["ETH"]` em outra);
   - a ressalva de que o agente não faz previsões nem sempre aparece;
   - `confidence` se concentra em 0.6 e 0.8; é autoavaliação, não uma medida calibrada.
3. **A não invenção de dados é garantida só pelo prompt.** Nada verifica automaticamente o texto livre (ex.: `investigation_steps`).
4. **Desempenho em CPU:** 30–110 s por chamada.
5. **Enum de intenção estrito:** uma intenção fora da lista é rejeitada; não há retry automático.
6. **Não validado:** execução fora do Docker (host sem Python 3.12) e uso de GPU.

## 8. Aprendizados

- **Schema válido não é saída correta.** Formato e conteúdo precisam de verificações separadas; os testes unitários cobrem formato, mas qualidade semântica só aparece com o modelo real.
- **Em modelos pequenos, exemplo vale mais que regra.** O few-shot corrigiu o que regras explícitas não corrigiram, mas também introduziu vieses de cópia. O exemplo precisa ensinar o *processo* (extrair do texto), não só o *formato*.
- **Ajuste de prompt tem efeito colateral.** Cada correção alterou outros casos; sem um conjunto fixo de perguntas de regressão, as regressões teriam passado despercebidas.
- **O modelo não sabe que dia é hoje.** Contexto temporal precisa ser injetado; senão o modelo "corrige" o usuário com base no seu corte de treino.
- **Ambiguidade no prompt vira inconsistência na saída.** A instabilidade de `missing_information` vinha da redação da regra, não do modelo.

## 9. Pendências e insumos para a próxima fase

- **Conjunto de regressão de prompt:** transformar as 5 perguntas em suíte de avaliação automatizada, com critérios por campo, para medir cada mudança de prompt ou modelo.
- **Checagem determinística de ativos:** extrair tickers do texto com o catálogo de ativos das APIs (CoinGecko/CoinPaprika), sem depender do modelo.
- **Mapeamento `requested_metrics` → ferramentas:** o vocabulário `METRIC_VOCABULARY` é o ponto de partida do contrato com as APIs.
- **Retry/fallback** (`with_retry`, `with_fallbacks`) para `AnalysisOutputError`.
- **Avaliar outros modelos e GPU:** comparar qualidade e latência (ex.: modelos maiores ou com melhor aderência a JSON).
- **Pergunta aberta:** `confidence` deve continuar sendo autoavaliação do modelo ou ser calculada a partir de sinais objetivos (ativos identificados, período presente, número de premissas)?
