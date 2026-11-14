---
title: "Crypto Intelligence Agent"
subtitle: "Relatório técnico das Fases 1 a 5"
author: "Gabriel Aguiar Aquino"
date: "13 de novembro de 2026"
lang: pt-BR
geometry: margin=2.2cm
toc: true
numbersections: true
header-includes:
  - \usepackage{float}
  - \floatplacement{figure}{H}
  - \usepackage{fvextra}
  - \DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\{\},fontsize=\scriptsize}
---

\newpage


<!-- Source: Report/relatorio-final/00-repositorio.md -->

# Repositório do código {.unnumbered}

Todo o código deste trabalho está no GitHub:

**<https://github.com/gabriel-q7/crypto-intelligent-agent>**

Vídeo de apresentação:

**<https://drive.google.com/file/d/1eSJtLnwlsB-GBLx4WHWoj_MGZ3NNTUzv/view?usp=sharing>**

| Diretório | Conteúdo |
|------------------------------|----------------------------------------------------------------|
| `app/` | Aplicação FastAPI: chains, agentes, ferramentas, memória, Text-to-Query, Retrieval Agent, verificação e avaliação |
| `ui/` | Interface Streamlit (só consome a API) |
| `tests/` | Testes offline (`tests/unit`, com LLM e API simulados) e de integração (`tests/integration`, com Ollama e CoinPaprika reais) |
| `knowledge/corpus/` | Corpus local de conceitos (sem preços) indexado no Chroma |
| `evaluation/` | Dataset versionado da avaliação da Fase 5 |
| `benchmarks/` | Perguntas e resultados do benchmark de modelos da Fase 1 |
| `docs/` | Documentação de referência do sistema e evidências brutas (`docs/evidence/`) |
| `Report/` | Diários técnicos por fase e capítulos comuns deste relatório |
| `scripts/` | Geração deste relatório e verificação automática dos números citados |

Tudo roda localmente com Docker Compose (`make up`). O relatório pode ser regenerado com `make report` (ou `./scripts/generate-report.sh`). Antes de gerar o PDF, o script confere os números citados contra os arquivos de evidência versionados.


\newpage


<!-- Source: Report/relatorio-final/01-uso-de-ia.md -->

# Declaração de uso de inteligência artificial {.unnumbered}

Este trabalho foi desenvolvido com o apoio de duas ferramentas de inteligência artificial generativa:

- **ChatGPT** (OpenAI), usado no **planejamento** das fases;
- **Claude Code** (Anthropic), usado como assistente de programação e de redação.

**Como as ferramentas foram utilizadas**

- **Planejamento (ChatGPT):** interpretação dos enunciados e organização das etapas de cada fase.
- **Implementação (Claude Code):** geração e revisão do código (chains, agentes, ferramentas, memória, Text-to-Query, Retrieval Agent, verificação, avaliação, interface e testes), a partir dos enunciados e das decisões do autor.
- **Investigação técnica (Claude Code):** spikes de bibliotecas (frameworks de memória, base vetorial, tool calling), checagem de avisos de depreciação e diagnóstico de falhas com o modelo e as APIs reais.
- **Relatório (Claude Code):** organização e redação dos diários de fase e deste relatório a partir das evidências registradas no repositório, além do script que consolida o relatório em PDF e confere os números.

**Responsabilidades do autor**

- Decisões de projeto, incluindo os desvios deliberados do enunciado (registrados no capítulo de decisões transversais).
- Execução do sistema, conferência das saídas e revisão crítica do código, das análises e do texto.
- Responsabilidade integral pelo conteúdo entregue.

**Integridade dos resultados**

Os números de desempenho, avaliação e testes citados vêm de execuções reais registradas no repositório (`benchmarks/results/`, `docs/evidence/`). As ferramentas de IA não produziram nem estimaram valores experimentais. As execuções com falha, inclusive as de infraestrutura, são relatadas, e não omitidas.


\newpage


<!-- Source: Report/relatorio-final/02-introducao.md -->

# Introdução

Este relatório descreve a construção do **Crypto Intelligence Agent**, um assistente de análise de criptoativos que roda inteiramente na máquina local: o modelo de linguagem é servido pelo Ollama em CPU, e os dados vêm de fontes reais (API da CoinPaprika, um banco SQLite de snapshots e um corpus de documentos indexado no Chroma).

O sistema foi construído em cinco fases incrementais. Cada fase preservou as anteriores, que continuam acessíveis pela mesma API.

| Fase | Tema | Entrega principal |
|-----:|----------------------------|--------------------------------------------------------------|
| 1 | Chain estruturada | Chain LCEL que transforma a pergunta num plano de análise em JSON validado, sem dados externos |
| 2 | Agente com ferramentas | Agente ReAct e chains roteadas com dados reais da CoinPaprika, base vetorial local e API HTTP |
| 3 | Memória conversacional | Sessões persistentes (SQLite), janela e resumo, memória episódica e semântica (Mem0 sobre Chroma) |
| 4 | Ferramentas externas e RAG | Banco de snapshots consultado por Text-to-Query com SQL validado; Retrieval Agent com Query Rewriting |
| 5 | Agente integrado e verificado | Agente com tool calling nativo, histórico persistente, verificação de respostas (NLI e coerência), avaliação no LangSmith e interface Streamlit |

## Princípio central

**Nenhum valor de mercado é inventado.** Esse requisito atravessa todas as fases e explica boa parte das decisões:

- na Fase 1, uma trava remove da resposta qualquer frase com valor monetário ou percentual, porque o modelo não tem dado atual;
- nas Fases 2 a 4, achados, fontes e horários são montados por código a partir das observações reais das ferramentas, e valores da resposta sem correspondência nos dados são removidos;
- na Fase 5, cada resposta é verificada contra a evidência da própria execução antes da entrega, com no máximo uma correção.

## Ambiente de execução

Todas as medições deste relatório foram feitas na mesma máquina: um notebook com AMD Ryzen 5 5500U (6 núcleos, 12 threads) e 22 GB de RAM, Linux (Manjaro), Docker Compose e inferência só em **CPU**, sem GPU dedicada. O uso de um modelo local e pequeno é deliberado: os dados que importam vêm das ferramentas, e não da memória do modelo (ver o capítulo de decisões transversais). O modelo padrão é o `gemma4:e2b` com raciocínio interno (*thinking*) desligado, escolhido pelo benchmark da Fase 1. Os embeddings usam `nomic-embed-text`. A aplicação roda em Python 3.12 dentro de containers.

Executar em CPU tem consequências que aparecem em todo o relatório: um turno do agente leva de dezenas de segundos a alguns minutos, e várias decisões (checagens determinísticas no lugar de chamadas ao LLM, reescrita de consulta só quando necessária) existem para conter essa latência.

## Organização do relatório

- **Arquitetura geral:** o sistema completo ao fim da Fase 5.
- **Fases 1 a 5:** um capítulo por fase, escrito como diário técnico durante o desenvolvimento (objetivo, escopo, solução, decisões, problemas, evidências, limitações, aprendizados e pendências). O capítulo da Fase 5 foi consolidado no fim do projeto a partir da documentação e das evidências da fase.
- **Decisões transversais:** as decisões que valem para o projeto todo, com destaque para a política de **não usar APIs depreciadas** e os desvios deliberados do enunciado.
- **Conclusão:** resultados consolidados, limitações e trabalhos futuros.

Os números citados vêm dos arquivos de evidência versionados (`benchmarks/results/` e `docs/evidence/`). Os diários usam datas absolutas, e as métricas trazem o modelo e o ambiente em que foram medidas.


\newpage


<!-- Source: Report/relatorio-final/03-arquitetura.md -->

# Arquitetura geral

Este capítulo descreve o sistema como ele está ao fim da Fase 5. Os capítulos das fases mostram como ele chegou até aqui.

## Visão de componentes

```text
                    Streamlit (ui/)  ── HTTP ──┐
                                               ▼
 ┌──────────────────────────── FastAPI (app/main.py, AppContainer) ─────────────────────────────┐
 │ Fase 1  /analyze[/batch|/stream]  → chain LCEL → plano JSON validado                         │
 │ Fase 2  /agent (ReAct legado)  /route (chains roteadas)  /knowledge/*                        │
 │ Fase 3  /conversations, /conversations/{id}/messages  → intenção → contexto → chains         │
 │ Fase 4  /market-data/*  (coleta, cobertura, Text-to-Query)   /knowledge/retrieve             │
 │ Fase 5  /conversations/{id}/agent → agente integrado → verificação → (1 correção)            │
 └───────────────┬──────────────────┬──────────────────┬──────────────────┬─────────────────────┘
                 ▼                  ▼                  ▼                  ▼
          Ollama (CPU)       CoinPaprika API     SQLite               Chroma (data/chroma)
          gemma4:e2b         (dados ao vivo)     conversations.sqlite3  crypto_knowledge (corpus)
          nomic-embed-text                       market.sqlite3         memórias Mem0 por sessão
                                                                         ▲
                                            LangSmith (traces e experimentos, opcional)
```

Os serviços do Docker Compose são `ollama`, `ollama-pull` (baixa os modelos uma vez), `app` (API) e `ui` (Streamlit). Os dados persistem em `./data` no host.

## Anatomia do agente

| Bloco | Fase 1 | Fase 2 | Fase 3 | Fase 4 | Fase 5 |
|-----------|-----------------|-------------|-------------|-------------|-------------|
| **Percepção** | `CryptoQuestion` (Pydantic) | Validação dos argumentos e extração de ativos | Intenção e entidades no contexto da conversa | Query Rewriting no vocabulário dos documentos | Argumentos tipados das ferramentas (tool calling) |
| **Raciocínio** | Prompt + `ChatOllama` → plano | ReAct e roteador com prompt PTCF | Resolução de continuação, correção e resultado anterior | O ReAct escolhe também banco, retrieval e cálculo; LLM gera SQL validado | `create_agent` com tool calling nativo; verificação e correção |
| **Ação** | — | CoinPaprika e busca semântica | Mesmas ferramentas, com contexto | SQL somente leitura, Retrieval Agent, cálculo | As mesmas ferramentas, combinadas numa execução |
| **Memória** | — | Janela limitada em processo | SQLite (janela + resumo) e Mem0 (episódica e semântica) | Sem mudança | Histórico persistente da Fase 3 lido por um adaptador |

## Fluxo de um turno do agente integrado

```text
POST /conversations/{id}/agent
 └─ trava por sessão → grava a pergunta (SQLite)
    → SessionHistoryAdapter: resumo + memórias Mem0 + janela (só leitura)
    → create_agent: LLM ⇄ ferramentas (API, SQL, RAG, cálculo); trajetória registrada
    → VerificationService: afirmações → evidência → checagem numérica / NLI → coerência lógica → status
    → [se necessário] 1 correção com as próprias observações → nova verificação → resposta cautelosa se falhar
    → grava a resposta, a análise e o episódio → manutenção em segundo plano (resumo, título, extração)
```

## Armazenamento

| Repositório | Conteúdo | Papel |
|------------------------------------|--------------------------------|--------------------------------|
| `data/conversations.sqlite3` | Sessões, mensagens, resumos, análises, contexto ativo | Fonte da verdade da conversa (Fase 3) |
| `data/market.sqlite3` | Snapshots reais da CoinPaprika | Dado de mercado histórico, consultado só por SQL validado (Fase 4) |
| Chroma `crypto_knowledge` | Trechos do corpus local com metadados | Base de conhecimento (Fases 2 e 4) |
| Chroma (Mem0) | Memórias episódicas e semânticas por sessão | Memória de longo prazo (Fase 3) |

Os dois bancos SQLite são separados por decisão da Fase 4: apagar ou recriar um não afeta o outro.

## Pilha e versões finais

| Pacote | Versão | Uso |
|----------------------------|---------|-----------------------------------------------------|
| `langchain-core` | 1.7.6 | Runnables, prompts, mensagens (era 1.6.12 até a Fase 4) |
| `langchain` | 1.4.9 | `create_agent` (Fase 5) |
| `langchain-ollama` | 1.1.0 | `ChatOllama` e `OllamaEmbeddings` |
| `langchain-classic` | 1.0.8 | Só as APIs legadas exigidas na Fase 2, isoladas em `app/legacy/` |
| `langchain-chroma` / `chromadb` | 1.1.0 / 1.5.9 | Base vetorial |
| `mem0ai` | 2.2.1 | Memória de longo prazo |
| `sqlglot` | 30.27.0 | Validação do SQL gerado pelo LLM |
| `fastapi` / `uvicorn` | 0.150.0 / 0.54.0 | API HTTP |
| `pydantic` | 2.14.1 | Schemas e validação |
| `langsmith` | 0.14.17 | Rastreamento e avaliação (dependência do `langchain-core`) |
| `streamlit` | 1.65.0 | Interface |

## Interface

A interface Streamlit (`ui/`) só fala com a API por HTTP (`ui/backend.py`). Nenhuma lógica de agente, memória ou verificação roda nela. A barra lateral lista as conversas com o título gerado pelo modelo após o primeiro turno. Cada resposta mostra o status da verificação, as afirmações sustentadas, neutras e contraditas, as ferramentas usadas e as fontes com horário.


\newpage


<!-- Source: Report/fase-01-chain-estruturada.md -->

# Fase 1 — Chain estruturada com LangChain e Ollama

| | |
|---|---|
| **Período** | 2026-10-17 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose 5.5.1, inferência em **CPU** (12 threads, sem GPU) |
| **Modelo** | `qwen2.5:7b` via `ollama/ollama:latest` (4,7 GB) |
| **Versões** | Python 3.12.15 (container), `langchain-core 1.6.12`, `langchain-ollama 1.1.0`, `pydantic 2.14.1`, `pytest 9.1.1` |

## Objetivo

Construir uma chain de ponta a ponta que **interprete** perguntas sobre criptomoedas e produza um JSON estruturado e validado, **sem consultar APIs externas**.

O sistema não responde a pergunta do usuário. Ele produz um **plano de análise**: intenção, ativos, métricas necessárias, período, etapas de investigação, lacunas, premissas e confiança. Esse plano é o contrato de entrada das fases seguintes, quando ferramentas e APIs passarão a executar as etapas.

Critérios de conclusão:

- análise executada pela CLI, retornando JSON validado;
- mesma chain em duas composições (LCEL e `@chain`);
- demonstração de `invoke`, `batch` e `stream`;
- execução local com Docker Compose.

Todos foram atendidos (seção 6).

## Escopo

**Dentro:** schemas Pydantic, prompts, `ChatOllama`, `JsonOutputParser` com validação, duas composições, interface Runnable, Docker Compose, testes unitários e de integração, documentação.

**Fora, por decisão de fase:** integrações com CoinPaprika, DeFiLlama e CoinGecko; memória persistente; RAG; ferramentas; planejamento autônomo. O objetivo era isolar e medir a qualidade da **interpretação** antes de acoplar dados reais.

## Solução implementada

```text
CryptoQuestion ──► build_prompt_input ──► ANALYSIS_PROMPT ──► ChatOllama ──► JsonOutputParser ──► validate_analysis ──► CryptoAnalysisRequest
 (Pydantic)        (PromptTemplate +      (ChatPromptTemplate:   (format=json,   (texto → dict,       (dict → modelo,
                    data de referência)    system + few-shot      temperature=0)  parciais no stream)  erros de domínio)
                                           + pergunta)
```

| Camada | Arquivo | Responsabilidade |
|---|---|---|
| Schemas | [app/schemas/](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/app/schemas) | `CryptoQuestion` (entrada) e `CryptoAnalysisRequest` (saída), com normalização de tickers e enum de intenção |
| Prompts | [app/prompts/analysis.py](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/app/prompts/analysis.py) | Instruções de sistema, `PromptTemplate` da pergunta, exemplo few-shot, vocabulário de métricas |
| Chains | [app/chains/analysis.py](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/app/chains/analysis.py) | `build_lcel_chain` (padrão), `build_imperative_chain` (`@chain`), `build_json_chain` (streaming) |
| Serviços | [app/services/](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/app/services) | Fábrica do `ChatOllama`, validação final, `run_analysis` / `run_batch` / `stream_analysis` com tradução de erros |
| Erros | [app/errors.py](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/app/errors.py) | `AnalysisOutputError` (saída inválida, guarda `raw_output`) e `LLMUnavailableError` (Ollama inacessível) |
| Entrada | [scripts/](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/scripts) | `analyze.py` (CLI) e `demo_runnables.py` (invoke, batch, stream) |
| Infra | [compose.yaml](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/compose.yaml), [Dockerfile](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/Dockerfile) | Serviços `ollama`, `ollama-pull` (baixa o modelo uma vez) e `app`; volume `ollama_models` |

## Decisões técnicas

| Decisão | Alternativas consideradas | Motivo |
|---|---|---|
| **LCEL como composição padrão** | `@chain` (imperativa) | A chain é linear. Só a LCEL propaga streaming etapa a etapa (o `@chain` emite um único chunk no fim), e ela permite reaproveitar subpartes (`build_json_chain`) e usar `batch`/`with_retry`/`with_fallbacks` em cada etapa. Detalhes em [docs/composition-decision.md](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/docs/composition-decision.md). |
| `format="json"` no `ChatOllama` + `temperature=0` | Só instrução no prompt | Restringe a geração a JSON sintaticamente válido e reduz a variação entre execuções. Não garante aderência ao schema, por isso existe a validação Pydantic. |
| `JsonOutputParser` + validação Pydantic separada | `with_structured_output` / `PydanticOutputParser` | O `JsonOutputParser` emite objetos parciais durante o streaming (requisito da fase); a validação estrita fica isolada em um passo próprio, testável sem modelo. |
| Erros de domínio (`AnalysisOutputError`, `LLMUnavailableError`) | Propagar exceções do LangChain/httpx | O chamador não precisa conhecer exceções de infraestrutura; `raw_output` preserva a saída do modelo para diagnóstico. |
| `batch` com `return_exceptions=True` | Falhar o lote inteiro | Uma resposta inválida do modelo não descarta as outras. |
| Serviço `ollama-pull` no Compose | Baixar o modelo manualmente | Subida reprodutível com um comando; o volume persistente evita novo download. |
| `requirements.txt` único (inclui pytest) | Separar dependências de dev | Menos arquivos; os testes rodam no mesmo container da aplicação. |
| Validação toda em container Python 3.12 | venv local | O host tem apenas Python 3.14; o requisito é 3.12. |

## Problemas encontrados e como foram resolvidos

### Import circular entre `app/chains` e `app/services`

- **Sintoma (identificado na revisão do código, antes da execução):** `app/chains` importa `app.services.validation`; se `app/services/__init__.py` reexportar `app.services.analysis`, que importa `app.chains`, o pacote é importado parcialmente.
- **Correção:** `app/services/__init__.py` sem reexports; quem usa importa o módulo diretamente.

### Aviso de permissão do cache do pytest no container

- **Sintoma:** `PytestCacheWarning: ... Permission denied: '/app/...'`.
- **Causa:** a aplicação roda como usuário sem privilégios (`appuser`), mas `/app` pertencia ao root.
- **Correção:** `COPY --chown=appuser:appuser` e `chown` do diretório no [Dockerfile](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/Dockerfile).

### Qualidade da saída do modelo — revisão iterativa do prompt

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

### O plano não respondia ao usuário — inclusão da resposta mínima

- **Sintoma (identificado na revisão das saídas):** "O que é staking de ETH?" retornava só o plano (`"Definir o que é staking de ETH"`, `"Explorar o processo..."`), sem nenhuma explicação. A pergunta é conceitual e o modelo sabe respondê-la sem dados externos.
- **Causa:** o schema da especificação só tinha campos de **plano**; o modelo não tinha onde responder.
- **Correção:** dois campos novos no `CryptoAnalysisRequest`:
  - `answer_status`: `answered` (conceito respondido com conhecimento do modelo), `requires_market_data` (depende de dados de mercado), `needs_clarification` (pergunta ambígua);
  - `answer`: resposta de 2 a 4 frases.
  - `investigation_steps` passou a poder ser vazio quando `answered`.
  - Segundo exemplo few-shot, conceitual ("Como funciona o halving do Bitcoin?"), mostrando que fatos de protocolo (210.000 blocos, 21 milhões de BTC) são permitidos e preços não.

Iterações com o modelo real (6 perguntas: staking, DEX, comparação, preço/previsão, TVL, risco):

| # | Mudança | Resultado | Problema revelado |
|---|---|---|---|
| 1 | Campos `answer_status`/`answer` no fim do JSON + exemplo conceitual | Staking e DEX respondidos | "BTC preço" e "Aave TVL" **falharam**: `investigation_steps` vazio com `requires_market_data`; "risco de SOL" classificado como conceito |
| 2 | `answer_status` movido para logo após `intent`; `answered` restrito a conceitos ("NÃO use para avaliar desempenho ou risco de um ativo específico") | Aave corrigido | "BTC preço" ainda sem etapas; SOL ainda `answered` |
| 3 | Etapa de coleta derivada de `requested_metrics` quando o modelo a omite | "BTC preço" corrigido | — |

Diagnóstico da iteração 1: modelos autorregressivos geram o JSON **na ordem dos campos**. Com `answer_status` no fim, o modelo escrevia `investigation_steps: []` — imitando o exemplo conceitual — antes de decidir que tipo de resposta daria. Colocar a decisão antes das consequências corrigiu o caso do Aave sem nenhuma regra nova.

Diagnóstico da iteração 3: a saída crua do modelo para "BTC vai subir amanhã? Qual o preço atual?" estava correta em tudo (`requested_metrics: ["price"]`, resposta "Não consigo prever [...] não tenho dados de mercado atualizados"), exceto pelas etapas omitidas. Rejeitar a análise inteira por uma omissão derivável era desproporcional; a etapa "Obter price de BTC" é gerada pelo código e registrada em `assumptions`, sem inventar nada.

### Por que a resposta não pode conter valores de mercado

Com um campo de texto livre, surgiu o risco de o modelo escrever números de mercado ("o staking rende 4% ao ano", "o BTC custa US$ 65.000"). Antes, o plano só citava **nomes** de métricas (`price_history`), nunca valores.

Motivos para bloquear:

1. **O modelo não tem dado atual.** O `qwen2.5:7b` conhece apenas o que viu até o fim do treinamento (2024). Preço, volume, TVL e rendimento mudam diariamente; um número do modelo estaria defasado em mais de um ano e seria lido pelo usuário como atual, já que não vem com data.
2. **Mesmo o dado antigo não é confiável.** Modelos pequenos não memorizam números com precisão: geram valores *plausíveis*. Sem fonte externa nesta fase, não há como verificar. O caso de "2026 é um erro, deve ser 2023" (5.3) já mostrou o modelo agindo a partir de uma visão desatualizada do mundo.
3. **Requisito da especificação:** "impedir a invenção de preços, volumes e dados atuais" e "não inventar dados de mercado".
4. **Prompt não é garantia.** Modelos 7B nem sempre obedecem instruções (5.3). A regra precisava de uma verificação determinística, assim como o formato tem o Pydantic.

Para o comportamento da trava havia três opções: rejeitar a análise, substituir a resposta ou remover a trava. A escolhida foi **substituir**, de forma localizada: apenas as frases com valor monetário ou percentual são removidas e um aviso padrão é acrescentado ("Nesta fase não tenho acesso a dados de mercado atualizados e respondo apenas com o conhecimento do modelo [...]"); o ocorrido é registrado em `assumptions`. Assim a parte conceitual da resposta é preservada.

O prompt também passou a exigir que, quando a pergunta depende de dados, a resposta **diga explicitamente** que o agente não tem dados atualizados nesta fase, em vez de apenas descrever o que será analisado.

Limites conhecidos da trava (heurística por expressão regular, não garantia): falso positivo em termos como "ataque de 51%"; falso negativo em valores sem símbolo de moeda ("65 mil", "65k").

### Benchmark de modelos: qwen2.5:7b × gemma4:e4b

**Objetivo:** medir o desempenho do modelo atual e compará-lo com o `gemma4:e4b`, com troca de modelo simples por variável de ambiente. O `mistral:7b` e o `gemma4:e2b` entraram depois na mesma comparação.

**Troca de modelo:** antes, `OLLAMA_MODEL` definido no shell não chegava ao container (o `env_file` não é sobrescrito pelo shell). O `compose.yaml` passou a interpolar `OLLAMA_MODEL` no serviço `app`, com prioridade shell > `.env` > padrão; como o `app` depende do `ollama-pull`, que usa a mesma variável, `OLLAMA_MODEL=gemma4:e4b docker compose run --rm app ...` baixa e usa o modelo novo. A CLI também ganhou `--model`.

**Ferramenta:** `scripts/benchmark.py` executa prompt → LLM → parser → validação separadamente para capturar as métricas do Ollama (`load_duration`, `prompt_eval_*`, `eval_*`), que a chain completa descarta, e verifica `answer_status`/`assets` contra o esperado em `benchmarks/questions.json`. `scripts/compare_benchmarks.py` gera a tabela comparativa.

**Problemas de medição encontrados e corrigidos:**

| Sintoma | Causa | Correção |
|---|---|---|
| "Prompt tok/s" de 609 e 1.904 (a medição isolada dava 23 e 38) | O aquecimento usava a mesma pergunta do 1º caso; a execução seguinte reaproveitou o prompt inteiro do cache do Ollama (0,3 s de leitura) e a média de razões amplificou esse ponto | Aquecimento com pergunta fora do conjunto; taxas calculadas como soma dos tokens ÷ soma dos tempos |
| Gemma gerando 1.232 tokens para um JSON de ~250 | `gemma4` tem **thinking** (raciocínio interno) ligado por padrão; os tokens contam no tempo mas não aparecem na saída | Nova variável `OLLAMA_REASONING` (vazio/true/false), opção `--reasoning` no benchmark e coluna "Thinking" na comparação |
| Benchmark completo estimado em ~1h40 | 3 repetições × 6 perguntas × 3 configurações em CPU | Com temperatura 0 as repetições do Qwen variaram < 2%; padrão reduzido para 1 repetição (`--runs` continua disponível) |

**Resultado:**


\begingroup\scriptsize

| Modelo | Thinking | Cold start (s) | Latência média (s) | Mediana (s) | p95 (s) | Tokens prompt | Prompt tok/s | Tokens saída | Saída tok/s | JSON válido | answer_status ok | assets ok |
|------------|-----------|------|------|------|------|------|------|------|------|------|-------|------|
| qwen2.5:7b | padrão (não suporta) | 7.0 | 71.0 | 65.8 | 90.5 | 2514 | 66.1 | 153 | 4.6 | 100% | 83% | 100% |
| gemma4:e4b | desligado | 8.2 | 83.1 | 83.9 | 87.3 | 2383 | 38.9 | 211 | 9.6 | 100% | **100%** | 100% |
| gemma4:e4b | ligado | 7.5 | 118.1 | 110.1 | 187.7 | 2385 | 38.8 | 554 | 9.8 | 100% | **100%** | 100% |
| mistral:7b | padrão (não suporta) | 16.5 | 141.9 | 140.8 | 163.0 | 2985 | 43.6 | 179 | 2.4 | **67%** | 100%¹ | 100%¹ |
| gemma4:e2b | desligado | 8.6 | **42.3** | **43.2** | **46.8** | 2383 | **75.4** | 178 | **16.7** | 100% | **100%** | 100% |

\endgroup


¹ Calculado só sobre as respostas válidas (4 de 6).

**mistral:7b (1 repetição):** o mais lento (geração 2,4 tok/s; o tokenizador gera ~2.985 tokens para o mesmo prompt, +19% que o Qwen) e o único com saídas inválidas — omitiu `confidence` em "Compare BTC e ETH" e `answer` em "TVL do Aave". O modo JSON do Ollama garante sintaxe, não a presença dos campos; a validação Pydantic rejeitou as duas saídas, como esperado. Nas respostas válidas, conteúdo correto e aviso de ausência de dados presente. Dados: `benchmarks/results/20261017T235946_mistral-7b.json`.

**gemma4:e2b (thinking desligado, 1 repetição):** o mais rápido em todas as métricas de tempo — latência média 42 s (−40% vs Qwen, −49% vs Gemma E4B), leitura do prompt 75 tok/s e geração 16,7 tok/s — com 100% de JSON válido, `answer_status` e `assets`. Respostas conceituais corretas (staking citando Proof-of-Stake) e aviso de ausência de dados em todas as perguntas de mercado; todas as etapas vieram do modelo (sem etapa derivada nem trava acionada). Ponto fraco: planos mais rasos — 1 etapa no TVL do Aave e `max_drawdown` ausente na comparação. Dados: `benchmarks/results/20261018T002057_gemma4-e2b_thinking-off.json`.

**Leitura dos resultados:**

- O **tempo é dominado pela leitura do prompt** (~2.400 tokens): ~54% do total no Qwen e ~73% no Gemma. Encurtar o prompt é a alavanca de desempenho mais forte para qualquer modelo.
- O Gemma **gera 2× mais rápido** mas **lê o prompt mais devagar**; no total, sem thinking, é 17% mais lento que o Qwen e mais estável (p95 87 s × 91 s).
- O **thinking não compensa** neste caso de uso: +42% de latência média, p95 2× maior, mesma acurácia.
- Em **qualidade**, o Gemma acertou 6/6 classificações e não repetiu o erro conceitual do Qwen ("nós de mineração").

**Ambiente após os testes:** por falta de disco (o `/` chegou a 100%, 7,9 MB livres), só o `qwen2.5:7b` foi mantido no volume do Ollama; `gemma4:e4b`, `gemma4:e2b` e `mistral:7b` foram removidos após o benchmark (resultados preservados em `benchmarks/results/`; para repetir: `--pull`). Também foi corrigido um bug do `--pull`: o download usava o cliente com timeout de inferência (300 s) e falhava em modelos de alguns GB.

**Decisão (2026-10-18):** o padrão do projeto passou a ser **`gemma4:e2b` com `OLLAMA_REASONING=false`** (`.env`, `.env.example`, `compose.yaml` e `app/config.py`). Foi o mais rápido (42 s por pergunta, −40% em relação ao Qwen), com acurácia máxima neste conjunto, e ocupa 4,6 GB. A ressalva são planos mais rasos que os do E4B, o que passa a importar quando as etapas forem executadas por ferramentas. O `mistral:7b` foi descartado (mais lento e 33% de saídas inválidas), o `gemma4:e4b` sem thinking ficou como alternativa de plano mais detalhado, e o thinking não compensou. Como o `ollama-pull` baixa o modelo definido em `OLLAMA_MODEL`, o `gemma4:e2b` volta ao volume na subida seguinte, e o `qwen2.5:7b` continua disponível como alternativa. A semântica de `OLLAMA_REASONING` ficou: ausente → `false` (padrão do projeto); vazio → padrão do modelo; `true`/`false` explícitos. No Compose isso exigiu `${OLLAMA_REASONING-false}` (sem `:`), porque `:-` trataria o valor vazio como ausente.

**Limitações do benchmark:** 6 perguntas e 1 repetição por modo do Gemma; acurácia medida só em `answer_status` e `assets` (correção do conteúdo de `answer` foi avaliada manualmente); apenas CPU.

## Evidências e métricas

Detalhes completos e saídas brutas: [docs/stage-1-evidence.md](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/docs/stage-1-evidence.md).

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

**Após a resposta mínima (5.4/5.5):** 80 testes unitários (0,7 s) e 5 de integração (409,8 s, incluindo o novo teste "pergunta conceitual é respondida"). O tempo maior vem do segundo exemplo few-shot e do texto adicional gerado em `answer`.

## Limitações conhecidas

1. **O streaming só existe antes da validação.** O Pydantic precisa do objeto completo; os objetos parciais não são validados e as chains com validação emitem um único chunk.
2. **Variação do modelo 7B após a revisão do prompt:**
   - `assets` oscila em perguntas conceituais ("O que é staking de ETH?": `[]` em uma execução, `["ETH"]` em outra);
   - a ressalva de que o agente não faz previsões nem sempre aparece;
   - `confidence` se concentra em 0.6 e 0.8; é autoavaliação, não uma medida calibrada.
3. **A não invenção de dados é garantida só pelo prompt.** Nada verifica automaticamente o texto livre (ex.: `investigation_steps`).
4. **Desempenho em CPU:** 30–110 s por chamada.
5. **Enum de intenção estrito:** uma intenção fora da lista é rejeitada; não há retry automático.
6. **Não validado:** execução fora do Docker (host sem Python 3.12) e uso de GPU.
7. **Erros conceituais em `answer` não são detectados.** Na resposta sobre staking, o modelo escreveu "depositar ETH em nós de mineração"; o Ethereum não usa mineração desde a migração para proof-of-stake (2022). A trava cobre valores de mercado, não a correção do conteúdo.
8. **Fronteira `answered` × `requires_market_data` oscila:** "risco de manter SOL" recebeu resposta qualitativa sem plano, apesar da regra.

## Aprendizados

- **Schema válido não é saída correta.** Formato e conteúdo precisam de verificações separadas; os testes unitários cobrem formato, mas qualidade semântica só aparece com o modelo real.
- **Em modelos pequenos, exemplo vale mais que regra.** O few-shot corrigiu o que regras explícitas não corrigiram, mas também introduziu vieses de cópia. O exemplo precisa ensinar o *processo* (extrair do texto), não só o *formato*.
- **Ajuste de prompt tem efeito colateral.** Cada correção alterou outros casos; sem um conjunto fixo de perguntas de regressão, as regressões teriam passado despercebidas.
- **O modelo não sabe que dia é hoje.** Contexto temporal precisa ser injetado; senão o modelo "corrige" o usuário com base no seu corte de treino.
- **Ambiguidade no prompt vira inconsistência na saída.** A instabilidade de `missing_information` vinha da redação da regra, não do modelo.
- **A ordem dos campos no schema é uma decisão de prompt.** O modelo gera o JSON em sequência; campos que condicionam outros (`answer_status` → `investigation_steps`) devem vir antes.
- **Nem toda falha de validação deve virar erro.** Omissões deriváveis (etapa de coleta) e conteúdo proibido localizado (uma frase com valor) podem ser corrigidos deterministicamente e registrados, preservando o restante da resposta.
- **Plano e resposta atendem públicos diferentes.** O plano serve às próximas etapas do sistema; o usuário precisa de uma resposta, mesmo que seja "não tenho esse dado nesta fase".

## Pendências e insumos para a próxima fase

- **Conjunto de regressão de prompt:** transformar as 5 perguntas em suíte de avaliação automatizada, com critérios por campo, para medir cada mudança de prompt ou modelo.
- **Checagem determinística de ativos:** extrair tickers do texto com o catálogo de ativos das APIs (CoinGecko/CoinPaprika), sem depender do modelo.
- **Mapeamento `requested_metrics` → ferramentas:** o vocabulário `METRIC_VOCABULARY` é o ponto de partida do contrato com as APIs.
- **Retry/fallback** (`with_retry`, `with_fallbacks`) para `AnalysisOutputError`.
- **Avaliar outros modelos e GPU:** comparar qualidade e latência (ex.: modelos maiores ou com melhor aderência a JSON).
- **Reduzir o prompt:** a leitura de ~2.400 tokens domina a latência; avaliar `format_instructions` mais enxuto (o JSON schema completo) e o custo/benefício de cada exemplo few-shot, medindo com o benchmark.
- **Revisar o modelo padrão** (`gemma4:e2b` sem thinking, adotado na seção 5.6) quando as etapas passarem a ser executadas, já que ele gera planos mais rasos.
- **Verificação de fatos conceituais:** com RAG ou fontes de referência, checar respostas `answered` (ex.: o erro "nós de mineração").
- **Substituir a trava heurística:** com dados reais disponíveis, valores em `answer` podem vir das ferramentas, com fonte e data, em vez de serem bloqueados.
- **Pergunta aberta:** `confidence` deve continuar sendo autoavaliação do modelo ou ser calculada a partir de sinais objetivos (ativos identificados, período presente, número de premissas)?


\newpage


<!-- Source: Report/fase-02-agente-ferramentas.md -->

# Fase 2 — Agente com ferramentas, roteamento, memória e base vetorial

| | |
|---|---|
| **Período** | 2026-10-19 a 2026-10-26 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose 5.5.1, Ollama 0.40.6, inferência em CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (chat, thinking desligado, `num_ctx` 8192) e `nomic-embed-text` (embeddings) |
| **Versões** | `langchain-core` 1.6.12, `langchain-ollama` 1.1.0, `langchain-classic` 1.0.8, `langchain-chroma` 1.1.0, `chromadb` 1.5.9 (antes: `langchain-community` 0.4.2 + `faiss-cpu` 1.15.1), `fastapi` 0.150.0, `uvicorn` 0.54.0 |

## Objetivo

Evoluir a chain da Fase 1 para um agente que:
- escolhe ferramentas e consulta dados reais (CoinPaprika);
- roteia tarefas e mantém contexto de conversa;
- busca conceitos numa base local (FAISS);
- produz respostas validadas.

Restrições:
- usar as APIs legadas exigidas pelo enunciado (`initialize_agent`, `ZERO_SHOT_REACT_DESCRIPTION`, `RouterChain`) sem comprometer a Fase 1;
- **nunca inventar dado de mercado**.

Os 10 critérios de aceite foram atendidos (`docs/stage-2-evidence.md`, seção 1).

## Escopo

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

## Solução implementada

```text
POST /agent  → CryptoAgent → initialize_agent(ZERO_SHOT_REACT) → ferramentas (CoinPaprika, FAISS) ┐
POST /route  → classificador (LLM | RouterChain legado | palavras-chave) → RunnableBranch          ├→ grounding → CryptoReport
               ├ market_analysis: identificar → buscar → validar → analisar (sequencial)            │
               ├ asset_comparison: identificar → [ativos || global] → validar → comparar (paralela)  │
               ├ knowledge_retrieval: FAISS → resposta com base nos trechos                         │
               └ defi_analysis: indisponível                                                        ┘
POST /analyze, /analyze/batch, /analyze/stream → chain da Fase 1 (inalterada)
```

Detalhes por módulo em `docs/architecture.md`.

## Decisões técnicas

| Decisão | Alternativas | Motivo |
|---|---|---|
| APIs legadas via `langchain-classic`, isoladas em `app/legacy/` | Rebaixar para LangChain 0.x; usar só `create_agent` | No LangChain 1.x o legado saiu do pacote `langchain`. O `langchain-classic` 1.0.8 é compatível com o `langchain-core` 1.6.12 da Fase 1, sem rebaixar nada. Isolamento permite trocar depois |
| `RouterChain` só para **classificar** | `MultiPromptChain` completo | `MultiPromptChain` exige destinos `LLMChain`; os destinos do projeto são chains LCEL com API |
| Saída montada por **código** (grounding), não pelo LLM | Pedir ao ReAct um "Final Answer" em JSON | Modelo 2B + protocolo textual = JSON frágil. Fontes, horários e achados vindos das observações reais eliminam a chance de inventar esses campos |
| Verificação de valores da resposta contra as observações | Confiar no prompt | A Fase 1 mostrou que prompt não garante comportamento |
| Ferramentas de entrada única com normalização | Ferramentas estruturadas | O zero-shot só aceita uma string |
| `nomic-embed-text` via Ollama | sentence-transformers (torch) | Reaproveita o container, 274 MB, sem PyTorch (o disco esteve no limite na Fase 1) |
| Memória própria, limitada por turnos e caracteres, com marcação de dados históricos | `ConversationBufferWindowMemory` | O zero-shot não tem slot de memória e a janela não limita tamanho |
| `OLLAMA_NUM_CTX=8192` | Padrão 4096 | O prompt do agente (cerca de 1.700 tokens antes das observações) mais histórico e observações passaria de 4096, com truncamento silencioso |
| Aplicação HTTP (FastAPI) | Scripts avulsos; CLI única | Entrada única, com as dependências criadas uma vez (seção 5.5) |
| Retry só para falhas transitórias | Retry geral | Resposta inválida com temperatura 0 tende a se repetir |

## Problemas encontrados e como foram resolvidos

### Compatibilidade das APIs legadas

- **Investigação:** instalação limpa em `python:3.12-slim` com as versões da Fase 1 fixadas.
- **Resultado:** `initialize_agent`, `AgentType`, `LLMRouterChain`, `RouterOutputParser` e `ConversationBufferWindowMemory` importam de `langchain_classic`; FAISS de `langchain_community`.
- **Achado posterior:** o `langchain-community` emite aviso de **descontinuação** ("is being sunset"), e não existe `langchain-faiss` no PyPI. A dependência ficou restrita a `app/knowledge/store.py` e foi registrada como risco.

### Comportamento real da CoinPaprika

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

### Primeira execução real do agente

O `gemma4:e2b` seguiu o protocolo ReAct logo na primeira execução:
- chamou `get_market_data("BTC")`, e a ferramenta resolveu o símbolo para `btc-bitcoin`;
- respondeu com o valor real, a fonte e o horário;
- a verificação de grounding confirmou os números.

Tempo total: 48 s (30 s só para o modelo ler o prompt de cerca de 1.700 tokens).

### Comparações erradas do modelo: três iterações

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

### Formato da aplicação: scripts → CLI única (descartada) → HTTP

A primeira versão seguiu o "executar por scripts Python" da Fase 1, com um script por tarefa em `scripts/`. Na revisão da estrutura, a quantidade de `__init__.py` levantou a dúvida se seria efeito dos scripts. Não era: os `__init__.py` são padrão de pacote Python e existem em qualquer formato, e só o `scripts/__init__.py` vinha dessa escolha. O problema real eram 8 pontos de entrada com inicialização repetida, sem entrada única e sem instalação.

A primeira alternativa, uma CLI única com subcomandos, foi descartada antes da implementação em favor de um **serviço HTTP com FastAPI**:

- `app/main.py` com contêiner de dependências criado no ciclo de vida da aplicação;
- 10 endpoints;
- `scripts/` removido;
- o benchmark virou módulo de manutenção (`python -m app.services.benchmark_runner`), por levar de 10 a 40 min.

Na migração, a primeira versão de `/analyze/stream` acumulava os parciais e só enviava no fim. Foi corrigida para enviar cada parcial assim que gerado.

### Exemplos do Swagger

O Swagger mostrava `"question": "string"`. Foram criados exemplos reais e nomeados por cenário (`app/api/examples.py`). Um teste valida cada exemplo contra o modelo da requisição, para que não fiquem inválidos se os modelos mudarem.

### Retry de LLM

As etapas JSON das chains ganharam `with_retry` para falhas de conexão com o Ollama (duas tentativas). Erros de validação não são repetidos. Ollama inacessível após o retry responde `503`. O agente ReAct não pode usar o wrapper, porque o legado exige `BaseLanguageModel`; nele, a falha vira `CryptoReport` `failed`.

### Troca de FAISS por Chroma

- **Decisão (2026-10-26):** usar Chroma como base vetorial no lugar do FAISS. O enunciado pedia FAISS; a mudança é um desvio deliberado e registrado.
- **Registro completo da decisão:** [docs/vector-store-decision.md](https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main/docs/vector-store-decision.md) (contexto, problemas do FAISS, motivação, alternativas, consequências, validação e como reverter).
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

## Evidências e métricas

Detalhes em `docs/stage-2-evidence.md`.

| Verificação | Resultado |
|---|---|
| Testes offline | 196 passed (~4 s) antes da troca para Chroma; 199 depois (seção 5.8) |
| Integração + rede | 11 passed (340 s) |
| `GET /health/coinpaprika` | 7/7 |
| Agente ReAct (ETH) | `complete`, 32 s, valores e horários reais |
| Continuação na mesma sessão | Nova consulta à API, sem reaproveitar valor antigo |
| Comparação de 3 ativos (paralela) | `complete`, 67 s, conclusão correta |
| `RouterChain` legado | Classificou corretamente |
| Rota DeFi | `unavailable` explícito |

## Limitações conhecidas

Ver `docs/reliability-decisions.md` (seção 6) e `docs/stage-2-evidence.md` (seção 7). As principais:
- a verificação confere valores, não conclusões, fora da rota de comparação;
- percentuais são comparados em módulo;
- a latência em CPU é de 15 a 70 s;
- a memória vive só no processo;
- na pergunta "Esse ativo está caro?", o agente não pediu o ativo como no exemplo few-shot.

## Aprendizados

- **Investigar a API real antes de codificar** evitou suposições erradas: o 301 do `/search`, o 402 do histórico e o `retry-after` de dias mudaram o design de retries.
- **Montar a saída por código** (fontes, horários, achados, status) é mais confiável que pedir JSON a um modelo pequeno em protocolo textual.
- **Ordenação e comparação não devem ser delegadas a modelos pequenos.** Nem com o ranking no prompt o modelo acertou.
- **Janela de contexto é requisito funcional:** o padrão de 4.096 tokens truncaria o agente em silêncio.
- **Isolar o legado** reduziu o custo de cumprir o enunciado. A troca para LangGraph ou `create_agent` afeta só `app/legacy/`.
- **Exemplos de documentação também são código:** sem teste, eles ficam inválidos.

## Pendências e insumos para a próxima fase

- Integrar DeFiLlama (rota `defi_analysis`) e CoinGecko (séries históricas, que o plano gratuito da CoinPaprika não oferece).
- Migrar o agente para LangGraph ou `create_agent` com tool calling nativo, com ferramentas de argumentos tipados e retry no LLM.
- Executar o plano da Fase 1 (`requested_metrics` → ferramentas), o planejamento hierárquico.
- Memória persistente (sessões) e de longo prazo.
- Avaliar se o few-shot de "informação ausente" precisa de reforço, ou de uma checagem determinística de "sem ativo".


\newpage


<!-- Source: Report/fase-03-memoria-conversacional.md -->

# Fase 3 — Memória conversacional e raciocínio contextual

| | |
|---|---|
| **Período** | 2026-10-28 a 2026-10-29 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose, Ollama, CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (thinking desligado) e `nomic-embed-text` |
| **Versões novas** | `mem0ai` 2.2.1 (mantidos: `langchain-core` 1.6.12, `chromadb` 1.5.9, `fastapi` 0.150.0) |

## Objetivo

Transformar a aplicação num assistente conversacional que:
- mantém o contexto entre mensagens e entre sessões, com persistência em SQLite e memória semântica no Chroma;
- interpreta continuações, correções e referências a resultados anteriores;
- não apresenta dado antigo como atual;
- preserva as Fases 1 e 2.

## Escopo e adaptações do enunciado

O enunciado foi **adaptado** em dois pontos, definidos antes da implementação:

1. **Nada depreciado.** O `ConversationBufferMemory` (exigido na seção 5 do enunciado) está depreciado desde o LangChain 0.3.1, com remoção na 2.0, e **não foi usado**. Histórico e janela são próprios, sobre SQLite.
2. **Framework de memória especializado.** A seção 12 do enunciado pedia só comparar frameworks; o projeto adotou um. O primeiro candidato foi o LangMem; depois dos spikes, o escolhido foi o Mem0 (seção 5.1).

Mantido do enunciado:
- API existente;
- LangChain na orquestração;
- SQLite como fonte da verdade e Chroma para memória semântica;
- 7 intenções;
- 8 cenários;
- documentação pedida.

Fora do escopo: CoinGecko e DeFiLlama (DeFi continua `unavailable`) e autenticação.

## Solução implementada

```text
POST /conversations/{id}/messages
 → trava por sessão → SQLite (mensagem) → intenção (regras + LLM) → resolver (contexto ativo, análises)
 → contexto (janela + resumo + memórias Mem0 da sessão, com orçamento) → chains LCEL da Fase 2 ou comparação com resultado anterior
 → SQLite (resposta, análise, contexto ativo) + episódio no Mem0 → segundo plano: resumo, reindexação, extração semântica
```

Módulos novos:
- `app/persistence/` (SQLite);
- `app/memory/{short_term,long_term}.py`;
- `app/intent/` (schemas, classificador, resolver);
- `app/conversation/` (`context_builder`, serviço);
- `app/api/conversation_routes.py`.

Detalhes em `docs/architecture.md` (seção 8) e `docs/memory-strategy.md`.

## Decisões técnicas

| Decisão | Alternativas | Motivo |
|---|---|---|
| Não usar `ConversationBufferMemory` | Usar isolado em `app/legacy/` (como o ReAct da Fase 2) | Política de não usar APIs depreciadas: remoção na 2.0; `InMemoryChatMessageHistory` também depreciado |
| Mem0 para a memória longa | LangMem, Letta, código próprio | Spikes (seção 5.1): único sem depreciação, Chroma e SQLite nativos, isolamento por `run_id` testado |
| Resumo e janela em código próprio (LCEL) | `summarize_messages` do LangMem | LangMem descartado (depreciação na dependência); o Mem0 não resume conversa |
| Fatos que sobrevivem ao resumo extraídos dos **metadados** | Deixar tudo para o LLM | Lição das Fases 1 e 2: modelo pequeno erra e omite. Resultados anteriores, correções e entidades não podem depender dele |
| Regras determinísticas prevalecem sobre o LLM na intenção | Só LLM | Período, correção, ativos e referência são detectáveis por regra com mais confiabilidade que o e2b |
| Episódios com `infer=False` e extração semântica em segundo plano | Extração em todo turno | Extração do Mem0 leva de 68 a 89 s; episódios precisam ser exatos |
| Filtro anti-valores de mercado na memória | Confiar no prompt | No spike, o Mem0 guardou "ETH a US$ 2.329,72" como fato |
| `MemoryBackend` (interface) + `LazyMemoryBackend` | Mem0 direto | Testes offline sem rede; API sobe sem Ollama; troca de backend localizada |
| Conclusões numéricas (diferença atual × anterior) feitas pelo código | Pedir ao LLM | Lição da Fase 2: o modelo inverte comparações |

## Problemas encontrados e como foram resolvidos

### Escolha do framework de memória: três spikes

| Candidato | Achado | Decisão |
|---|---|---|
| **LangMem 0.0.30** | Dependências compatíveis; `summarize_messages` funcionou (22 s); **`trustcall` importa `langgraph.constants.Send`, depreciado no LangGraph 1.0** (o aviso aparece em qualquer uso); 1 de 3 extrações inventou memória | Descartado pela política de não usar APIs depreciadas; avaliação passou para Letta e Mem0 |
| **Letta** | Documentação oficial: a imagem Docker "não é mais mantida nem suportada"; Postgres + pgvector; laço de agente próprio | Descartado sem execução |
| **Mem0 2.2.1** | Sem depreciação; Chroma e SQLite; isolamento por `run_id` (ok); extração **lenta** (68–89 s) e **guardou preço como fato** | **Escolhido**, com mitigações (seção 4) |

Registro completo: `docs/memory-decision.md`.

### 30 dias da CoinPaprika

O plano gratuito devolve `percent_change_30d = 0` (e `1y = 0`) para todos os ativos, verificado com BTC, ETH e SOL. Na Fase 2 esse 0 chegava ao modelo como dado. Agora:
- 0 exato vira "indisponível";
- pedidos de 30 dias recebem limitação explícita (`partial`), com as variações reais de 24h e 7d.

### Ajustes de regras e testes

- "Qual o preço atual?" e "Considere os últimos 30 dias" **sem contexto** caíam em "pesquisa geral". Corrigido: viram pedido de esclarecimento.
- **Referência ambígua** a resultado anterior: o contexto ativo resolvia sozinho. Agora só resolve com referência explícita ("os dois", "them"); sem ela, pergunta.
- **Teste de depreciação** com `importlib.reload` recriava classes de exceção e quebrava outros testes. Movido para um subprocesso limpo.

### Problemas revelados pela execução real

| Sintoma | Correção |
|---|---|
| Declaração de preferência rodou de novo a análise DeFi | Preferência só é confirmada e enviada à extração |
| Resumos acumulavam na memória longa (v1, v2, v3) | `replace_episode`: um resumo por sessão no Chroma |

## Evidências e métricas

Detalhes em `docs/conversation-evaluation.md`.

| Verificação | Resultado |
|---|---|
| Testes offline (Fases 1–3) | **260 passed** |
| Integração Fase 3 (Ollama + Mem0 + CoinPaprika reais) | 1 passed, 537 s (A, D, B, preferência, reinício, C, isolamento) |
| Integração completa (Fases 1–3) | **12 passed** em 702 s (5 Fase 1 + 6 Fase 2 + 1 Fase 3), sem regressão |
| API real com `docker compose restart app` | Sessão retomada (8 mensagens em ordem, contexto ativo restaurado); continuação, resultado anterior e isolamento corretos; sessão inexistente → 404 |
| Memória semântica real | "O usuário prefere que as análises… focadas em risco" recuperada com score 0,617; nenhum valor de mercado gravado |
| Tempo por turno (CPU) | 7 s (resultado anterior) a cerca de 70 s; 8 s para confirmar uma preferência |

## Limitações conhecidas

- **Disputa pelo Ollama:** a extração em segundo plano compete com as requisições ("Analise SOL" levou 177 s).
- **"Resultado anterior"** pega a análise mais recente com os mesmos ativos, o que pode ser a do turno anterior (diferença de 0%).
- **Memórias semânticas** às vezes gravadas em inglês pelo Mem0.
- **Sem autenticação**; `session_id` não é credencial.
- **Travas por sessão** valem por processo, não entre vários workers.
- **Contagem de tokens aproximada**, porque o Ollama não expõe o tokenizer.

## Aprendizados

- **Depreciação também vem de dependências transitivas:** o LangMem em si não usa nada depreciado, mas o `trustcall` sim. Medir no spike, com a pilha real, evitou adotar algo com prazo de remoção.
- **Frameworks de memória pressupõem modelos fortes.** Com um modelo de 2B em CPU, extração por LLM é lenta e precisa de filtros determinísticos. O que é crítico (episódios, resultados anteriores) não deve depender dela.
- **A memória pode reintroduzir dado desatualizado.** Um preço guardado como "fato" seria reapresentado depois; a política de retenção precisa proibir isso explicitamente.
- **Regras primeiro, LLM depois,** na interpretação de continuações: período, correção e ativos citados são detectados com mais confiabilidade por regras do que pelo modelo pequeno.
- **Testar com a API real e reinício real** revelou dois comportamentos (preferência e resumos acumulados) que os testes com dublês não pegaram.

## Pendências e insumos para a próxima fase

- **Fila ou prioridade para o Ollama**, para a manutenção em segundo plano não atrasar as respostas (ou extração só em horários ociosos).
- **Heurística de "resultado anterior"**, distinguindo "o de agora há pouco" de "o de antes".
- **Autenticação e `user_id` real** no Mem0, se houver mais de um usuário.
- **Migração do agente da Fase 2** para LangGraph (`create_agent` com checkpointing), que unificaria `/agent` e `/conversations`.
- **DeFiLlama e CoinGecko:** as intenções `defi_analysis` e `historical_analysis` já preservam o contexto para quando houver fonte.


\newpage


<!-- Source: Report/fase-04-ferramentas-rag.md -->

# Fase 4 — Ferramentas externas e integração com o RAG existente

| | |
|---|---|
| **Período** | 2026-11-04 (horário local; as execuções reais estão em UTC, 2026-11-05 00:30–01:30) |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose, Ollama, CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (thinking desligado) e `nomic-embed-text` |
| **Versões novas** | `sqlglot` 30.27.0. Mantidos: `langchain-core` 1.6.12, `langchain-classic` 1.0.8, `chromadb` 1.5.9, `mem0ai` 2.2.1, `fastapi` 0.150.0 |

## Objetivo

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

## Escopo

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

## Solução implementada

```text
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

## Decisões técnicas

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

## Problemas encontrados e como foram resolvidos

### Primeira escolha de exemplo few-shot contaminava o experimento

- **Sintoma:** o exemplo do prompt de reescrita era justamente "recompensa cai pela metade → halving", um dos casos avaliados.
- **Correção:** o exemplo foi trocado por um tema fora do corpus ("rug pull"), antes das medições finais.
- **Efeito:** sem o exemplo, o modelo não escreve mais "halving", mas a reescrita ainda melhora o ranking (`docs/phase-4-evidence.md`, seção 1.2). O resultado registrado é o honesto, sem a ajuda do exemplo.

### Política de escolha entre busca original e reescrita (três versões)

| Versão | Regra | Falha observada com dados reais |
|---|---|---|
| 1 | Fica a busca com o melhor trecho mais próximo | "Queima de moedas" (fora do corpus): a reescrita acrescentou termos genéricos e levou a distância de 0,437 para 0,183 em trechos **irrelevantes**. Isso fabricaria evidência |
| 2 | Relevância = distância à **pergunta original** (piso de 0,42; ranking da reescrita) | Funcionou no halving, mas em "impermanent loss" os trechos de DeFi trazidos pela reescrita ficaram **mais longe** da pergunta (0,425–0,455) que trechos errados (0,387) e foram descartados. Usar essa distância para **comparar** as buscas falha por construção: a original é ranqueada por ela |
| 3 (final) | Distância à consulta usada ≤ 0,38 + **palavra âncora** da reescrita presente no trecho (prefixo, sem termos genéricos) | Barra a evidência fabricada (queima) e aceita a tradução de vocabulário (halving). Limitações na seção 7 |

### Erros semânticos do gerador SQL

| Sintoma (execução real) | Causa | Correção |
|---|---|---|
| `... WHERE symbol IN ('ADA', ..., 'USDT')` sem filtro pedido, **omitindo XRP** no ranking por market cap | A lista de símbolos estava no prompt, e o modelo a copiou como filtro | Prompt passa só a quantidade de ativos e a janela; regra "só filtre por symbol quando a pergunta citar o ativo" |
| `SELECT AVG(price_usd) FROM market_snapshots WHERE symbol = 'BTC'` | `market_snapshots` não tem `symbol` (exige JOIN). **O validador recusou** antes da execução (`invalid_query`) | View `snapshot_history` com `symbol`/`name`, mais um exemplo de média histórica |

Depois das correções, a bateria final de 9 perguntas teve 9 status corretos (`docs/phase-4-evidence.md`, seção 2.1).

### CTE recursiva negada pelo authorizer com erro genérico

- **Sintoma:** o teste de timeout dava `execution_error` em vez de `timeout`.
- **Causa:** o SQLite pede autorização da ação `SQLITE_RECURSIVE`, e "not authorized" chega como `DatabaseError`, não como `OperationalError`.
- **Correção:** a ação recursiva (só leitura) passou a ser permitida, e "not authorized" é tratado em qualquer `DatabaseError`.

### Agente real: pergunta combinada sem documento e entrada resumida

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

### Regressão completa: duas falhas reais

1ª execução da suíte de integração completa: **16 passed, 2 failed** (`docs/evidence/phase-4/regression-run1.log`).

- **Fase 3 (`test_live_multi_turn_restart_and_isolation`):**
  - **Causa:** mudança da fonte, não do código. Em 2026-11-05 a CoinPaprika passou a devolver `percent_change_30d` real (BTC -6,00%), e a resposta trouxe "Variação 30d" em vez da limitação "30 dias", que o teste exigia.
  - **Correção:** o teste aceita os dois comportamentos corretos; no Text-to-Query, 30 dias passou a ser janela do provedor (`PROVIDER_WINDOWS_DAYS = {1, 7, 30}`); a documentação que dizia "30d sempre NULL" foi corrigida.
- **Fase 4 (halving):**
  - **Causa:** com o mesmo prompt e temperatura 0, o Ollama gerou outra reescrita, acrescentando "bitcoin ethereum". O trecho de Ethereum subiu para 1º.
  - **Correção:** `strip_unrequested_assets` remove da reescrita os ativos que a pergunta não cita, e o teste passa a exigir melhora para o top-2.
  - **Medição:** em 5 execuções seguidas, o trecho ficou em 1º quatro vezes e em 2º uma vez.

### Testes rodavam o código antigo da imagem

- **Sintoma:** `ModuleNotFoundError` para os módulos novos.
- **Causa:** a imagem copia o código no build, e o Compose só monta `./data`.
- **Correção:** durante o desenvolvimento, os testes rodaram com `-v $PWD:/app`. A imagem final foi reconstruída.

## Evidências e métricas

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

## Limitações conhecidas

- **Chunking existente:**
  - `risks.md#12` só tem o título e aparece em muitas buscas;
  - `defi-metrics.md#6` dilui a frase sobre perda impermanente.

  Reescrever a consulta não corrige isso.
- **Distância não é relevância:** trechos genéricos ficam perto de perguntas fracas, e os limiares são empíricos (corpus de 18 trechos).
- **A palavra âncora é heurística:** palavras de domínio ("liquidez", "perda") deixam passar trechos só parcialmente relevantes (caso impermanent loss).
- **Histórico de mercado depende de coletas reais:** perguntas de 90 dias ou mais dão `insufficient_coverage`.
- **Saída do LLM não é estritamente determinística** em CPU, mesmo com temperatura 0: a reescrita variou entre execuções. Os testes de integração usam critérios de melhora, não igualdade.
- **O SQL do `gemma4:e2b` é correto em filtros e rankings simples;** consultas temporais complexas são frágeis. A validação garante segurança, não semântica.

## Aprendizados

- **Medir antes de calibrar:** os limiares e a política final só saíram das distâncias reais. As duas políticas "óbvias" (menor distância; distância à pergunta original) falharam em casos concretos.
- **Reescrita também introduz risco:** termos genéricos acrescentados pelo LLM criam relevância aparente. A transformação de consulta precisa de uma checagem de que a evidência tem a ver com o conceito.
- **Few-shot pode contaminar a avaliação:** um exemplo igual ao caso de teste mascara o comportamento real do modelo.
- **Parser e authorizer se complementam:** cada um pegou casos que o outro não pegaria (função conhecida pelo parser, mas negada pelo SQLite; coluna inexistente recusada antes de chegar ao banco).
- **Contexto demais atrapalha o modelo pequeno:** a lista de ativos no prompt virou filtro indevido.
- **Fontes externas mudam:** a premissa "30d = 0 no plano gratuito", verificada na Fase 3, deixou de valer uma semana depois. Testes de integração precisam aceitar os dois comportamentos legítimos, e a documentação deve datar esse tipo de fato.

## Pendências e insumos para a próxima fase

- **Chunking por item de lista** em `risks.md` e uma seção própria para perda impermanente, se uma fase futura permitir mexer na ingestão.
- **Coleta agendada** para formar histórico (hoje é manual).
- **Cobertura por ativo** no Text-to-Query.
- **Migração do ReAct legado** para tool calling nativo (LangGraph), com ferramentas de entrada estruturada no lugar de texto único.
- **DeFiLlama/CoinGecko:** `unsupported` no Text-to-Query e `unavailable` nas rotas DeFi indicam onde entram.


\newpage


<!-- Source: Report/fase-05-agente-integrado.md -->

# Fase 5 — Agente integrado, verificação de respostas, avaliação e interface

| | |
|---|---|
| **Período** | 2026-11-05 a 2026-11-08 |
| **Status** | Concluída |
| **Ambiente** | Linux (Manjaro), Docker Compose, Ollama, CPU (12 threads) |
| **Modelos** | `gemma4:e2b` (thinking desligado; agente, verificação e juiz) e `nomic-embed-text` |
| **Versões novas** | `langchain` 1.4.9 (com LangGraph 1.2.20), `langchain-core` 1.6.12 → **1.7.6**, `langsmith` 0.14.17 (já era dependência), `streamlit` 1.65.0 |

> Este diário foi consolidado no fim do projeto (2026-11-08) a partir de `docs/agent-integration.md`, `docs/response-verification.md`, `docs/langsmith-evaluation.md`, das evidências em `docs/evidence/phase-5/` e `docs/evidence/final/` e do histórico do git. As fases anteriores foram escritas durante o desenvolvimento.

## Objetivo

Unir as capacidades das Fases 1 a 4 num **único agente** que:

- escolhe e combina ferramentas de API (CoinPaprika), SQL (snapshots), RAG (Chroma) e cálculo;
- usa o histórico persistente da Fase 3;
- **verifica cada resposta antes da entrega**: consistência factual contra a evidência real (NLI) e coerência lógica, com no máximo uma correção;
- é rastreado e avaliado (LangSmith, dataset versionado, avaliadores de resposta e de trajetória);
- tem uma interface Streamlit que só consome a API.

## Escopo

**Dentro:** agente com tool calling nativo, ferramentas estruturadas, adaptador de histórico, serviço de verificação, política de correção, dataset e avaliadores, runner local e LangSmith, interface Streamlit, testes e documentação.

**Adaptações do enunciado (2026-11-05):**

| Enunciado | Implementado | Motivo |
|----------------------------------|--------------------------------|----------------------|
| `create_openai_tools_agent` + `AgentExecutor` | `create_agent` (pacote `langchain`) | APIs do pacote legado `langchain-classic`; `create_agent` é o substituto oficial do LangChain 1.x |
| `RunnableWithMessageHistory` | `SessionHistoryAdapter` | Depreciado no `langchain-core` 1.7.6 ("Use LangGraph's built-in persistence instead") |
| NLI com Transformers (`mDeBERTa-v3-base-mnli-xnli`) | NLI com `gemma4:e2b` via Ollama | Sem torch e transformers (cerca de 2 GB, com 5,3 GB livres) e um único motor de inferência |

**Fora:** autenticação, CoinGecko e DeFiLlama.

## Solução implementada

```text
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

## Decisões técnicas

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

## Problemas encontrados e como foram resolvidos

### Atualização do `langchain-core` exigida pelo `create_agent`

- **Sintoma:** o `langchain` 1.4.9 exige `langchain-core` 1.7.6; o projeto estava fixado em 1.6.12 desde a Fase 1.
- **Correção:** atualização do `langchain-core`.
- **Evidência:** a suíte offline das Fases 1 a 4 passou sem mudança depois da atualização (312 testes).

### NLI com modelo de 2B: neutro classificado como contradição

- **Sintoma (1ª execução da integração, `docs/evidence/phase-5/integration-run1.log`):** "O Proof of Stake foi inventado em 2012 pela equipe do Peercoin", sem apoio nem negação na evidência, saiu como `contradiction`. O teste exigia acerto exato em 3 casos e falhou.
- **Causa:** o rótulo e a confiança são gerados pelo LLM, sem cabeça de classificação calibrada; afirmações "fora da evidência" tendem a ser lidas como "contra a evidência".
- **Correção:** a medição passou a usar 12 pares reais (4 por rótulo, sobre PoS e halving), com critério de pelo menos 10 acertos e no máximo 1 neutro classificado como contradição. A limitação foi documentada: a confiança não é probabilidade calibrada.

### Pergunta sobre o próprio histórico sem resposta

- **Sintoma (mesma execução):** em "De qual ativo eu falei antes nesta conversa?", o agente respondeu que não havia histórico, embora as duas perguntas anteriores fossem sobre ETH. A verificação devolveu `verified` com 0 afirmações.
- **Leitura:** o histórico chegou ao agente (a pergunta seguinte, "volume de 24 horas desse mesmo ativo", resolveu ETH corretamente, inclusive após o reinício). O modelo de 2B não usou o histórico para responder uma pergunta **sobre** a conversa.
- **Limitação registrada:** a verificação confere afirmações factuais; uma recusa sem afirmações passa como `verified`. A avaliação mede a memória por outros meios (`memory-followup`, `memory-restart`).
- **Reprodução (integração final, 2026-11-08):** a mesma pergunta falhou de novo ("Não há nenhum ativo mencionado em sua solicitação anterior"). Desta vez a frase foi extraída como afirmação, ficou `neutral`, a verificação deu `unsupported`, a correção não resolveu e saiu a resposta cautelosa com aviso. A falha do modelo persiste; a diferença é que agora ela aparece como não verificada em vez de passar como `verified`.

### Resposta cautelosa marcando a própria limitação

- **Sintoma (avaliação de base, `docs/evidence/phase-5/eval-local-baseline.json`):** em `ambiguous-01` e `error-coverage`, frases como "por favor, indique o ativo de seu interesse" receberam o sufixo "(não confirmado pelas fontes consultadas)".
- **Causa:** declarações de limitação, pedidos ao usuário e frases que só dizem de onde veio o dado eram extraídos como afirmações; sem evidência que os "sustentasse", ficavam `neutral` e a resposta cautelosa os marcava.
- **Efeito:** respostas corretas ficaram confusas, e a relevância medida caiu (`error-coverage`: 0,2).
- **Correção:** a extração passou a ignorar esses três tipos de frase (`NON_FACTUAL` e `PROVENANCE_ONLY` em `app/verification/claims.py`). Na avaliação final, os dois exemplos saem `verified` e sem marcação.

### Avaliação contaminada por falhas de infraestrutura

- **Sintoma:** na execução `local-after-fixes` (`docs/evidence/phase-5/eval-local-after-fixes.json`), 8 dos 19 exemplos terminaram com `ConnectError('[Errno -2] Name or service not known')` ou `RemoteProtocolError`: o alvo perdeu o Ollama no meio da execução. As médias caíram (por exemplo, `tool_selection` de 0,929 para 0,5).
- **Causa:** o runner atribuía nota a exemplos que nem executaram; com saída vazia, os avaliadores davam 0.
- **Correção (2026-11-08):** `_summarize` exclui das médias os exemplos com erro e informa `n_scored` e `errored` (teste `test_summary_excludes_examples_that_failed_to_run`). A execução `local-after-fixes` não é usada como resultado; a avaliação final foi repetida (seção 6).

### Interface: retomar conversa pelo id

- **Sintoma (2026-11-08, uso da interface):** a barra lateral só permitia retomar uma conversa digitando o `session_id` (32 caracteres hexadecimais).
- **Correção:**
  - endpoint `GET /conversations` (mais recentes primeiro);
  - título gerado pelo modelo após o 1º turno, na manutenção em segundo plano (sem latência extra), com a primeira pergunta truncada como alternativa;
  - lista clicável na barra lateral, atualizada a cada 15 s;
  - botão "Deploy" do Streamlit oculto (`client.toolbarMode = "minimal"`).
- **Problema encontrado no teste real:** o título caiu sempre na alternativa. O LLM da aplicação roda em modo JSON (`format="json"`) e não devolve texto livre. O prompt passou a pedir `{"title": ...}`, e o parser aceita JSON ou texto. Na repetição, uma conversa sobre ETH recebeu "Preço e Consenso do Ethereum".

### Parâmetro depreciado na interface

- **Sintoma:** a auditoria final de depreciação encontrou `use_container_width` nos botões da interface, depreciado no Streamlit 1.65.
- **Correção:** `width="stretch"`. Detalhes da política no capítulo de decisões transversais.

### Pedido de recomendação: recusa sem consultar os dados

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

### Comparação entre redes respondida só com preço

- **Sintoma (uso real, 2026-11-12):** em "faça uma comparação sobre solana vs ethereum", o agente chamou só `get_market_data` e respondeu com preço, rank e volume de SOL e ETH. A pergunta era sobre as **redes**: contratos inteligentes, consenso, finalidade.
- **Causas:** (1) a base de conhecimento não tinha nada sobre Solana, e Ethereum tinha uma frase sobre EVM; pela regra 4, sem trecho recuperado o agente não pode fazer afirmação conceitual; (2) o prompt só mandava para `search_knowledge_base` perguntas do tipo "o que é / como funciona / diferença entre", e "comparação X vs Y" caía nos dados de mercado; (3) o limite de 2 a 6 frases não comportava tecnologia + mercado.
- **Correção:**
  1. **Corpus:** `solana.md` (PoH, Tower BFT, programas em Rust/SBF, modelo de contas, Sealevel, níveis de confirmação e finalidade, Alpenglow, trade-offs) e `comparacao-l1.md` (Ethereum × Solana por eixo, em seções de prosa menores que o chunk de 800 caracteres, com um resumo inicial que já cita finalidade); `ethereum-staking.md` ganhou as seções "Contratos inteligentes" e "Finalidade" (Gasper, Casper FFG, ~12,8 min). Sem preços (teste `test_corpus_has_no_market_prices`).
  2. **Prompt e descrições:** comparação entre redes → `search_knowledge_base` **e** `get_market_data` de cada ativo; resposta em tecnologia + mercado, até 12 frases. A descrição de `search_knowledge_base` e o prompt do Query Rewriting citam Solana, contratos inteligentes e finalidade.
  3. **Guarda determinística (`skipped_concepts` em `app/integrated/service.py`):** pergunta com "vs/compar/diferen", dois ou mais ativos e sem `search_knowledge_base` aciona a correção única, pedindo a busca conceitual. Comparações só de mercado ("compare o preço de BTC e ETH") ficam de fora.
- **Primeira tentativa do corpus:** com a tabela comparativa, o chunk mais próximo da pergunta era só o título da seção (a tabela passava de 800 caracteres e foi separada). A tabela virou prosa por eixo.
- **Avaliação:** o dataset da Fase 5 não mudou (limite de 15 a 20 exemplos e números da versão 3 já reportados); o caso fica nos testes `test_skipped_concepts_flags_network_comparisons_without_knowledge_base` e `test_network_comparison_without_knowledge_base_is_corrected`.
- **Evidência (`docs/evidence/final/network-comparison-probe.jsonl`, 2026-11-12):** recuperação com distância 0,105 no resumo de `comparacao-l1.md`. Seis turnos reais, 3 por formulação: **6 de 6 comparações** consultaram `search_knowledge_base` e explicaram consenso e contratos inteligentes. Com "faça uma comparação sobre solana vs ethereum", as 3 execuções também buscaram SOL e ETH em `get_market_data`, mas nenhuma citou a finalidade; a verificação terminou 1 `partially_verified` (2 afirmações `neutral`) e 2 `verification_failed` (erro do verificador numa afirmação), de 120,6 s a 206,2 s. Com "Qual a diferença entre Ethereum e Solana?", as 3 citaram a finalidade, não buscaram dados de mercado (a pergunta não pede) e terminaram com 3 `verified`, de 54,2 s a 83,2 s.

### Redes fora da base: perfil da CoinPaprika e consultas feitas pelo código

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

### O que os traces do LangSmith mostraram

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

### Histórico de preço sem fallback para a API

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

## Evidências e métricas

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

### Avaliação final

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

### Integração final

Execução de `tests/integration/test_phase5_live.py` com Ollama, CoinPaprika, SQLite, Chroma e Mem0 reais em 2026-11-08 (`docs/evidence/final/integration-phase5.log`): **6 passed, 1 skipped** em 403,56 s. O teste pulado é o de trace do LangSmith (variáveis não configuradas).

| Teste | Resultado |
|---|---|
| Tool calling: bind, seleção, execução e incorporação | Aprovado |
| API, SQL, RAG e combinações (api+sql, api+rag) | Aprovado |
| Vários turnos, isolamento, reinício e Mem0 | Aprovado (com a falha de conteúdo da seção 5.3 na pergunta sobre o próprio histórico) |
| NLI com 12 pares reais | Aprovado: acurácia 11/12; 4/4 `entailment`, 4/4 `contradiction`, 3/4 `neutral` (1 neutro classificado como contradição) |
| Correção única de resposta adulterada (preço de US$ 1.000.000) | Aprovado: o valor falso sai da resposta corrigida |
| Cliente Streamlit contra a API real | Aprovado |


### Rastreamento no LangSmith (2026-11-12 a 2026-11-13)

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

## Limitações conhecidas

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

## Aprendizados

- **Verificar exige separar tipos de afirmação.** Números por código, conceitos por NLI, coerência por regras e só o que sobra pelo LLM. Um verificador único por LLM herdaria os mesmos erros do agente.
- **"Sem evidência" e "contra a evidência" são coisas diferentes,** e o modelo pequeno confunde as duas. O desenho de status (`neutral`, `unsupported`, `verification_failed`) existe para não punir a resposta pela limitação da verificação.
- **Avaliação precisa distinguir falha do sistema de falha da infraestrutura.** Médias com exemplos que nem rodaram contam uma história falsa.
- **Depreciação continua aparecendo no fim:** até a interface tinha um parâmetro depreciado. A auditoria com todos os avisos visíveis deve fazer parte da entrega.
- **O corpus define o que o agente sabe explicar.** Com a regra "conceito só a partir dos trechos", uma lacuna na base não vira resposta errada, vira resposta incompleta (só preço). O formato do documento também importa: uma tabela maior que o chunk separou o título do conteúdo, e o trecho mais próximo da pergunta ficou vazio.
- **Modo JSON é configuração global do LLM.** Uma tarefa nova de texto livre (o título) quebrou em silêncio; o teste com o modelo real revelou o que o dublê não revelaria.

## Pendências

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


\newpage


<!-- Source: Report/relatorio-final/09-decisoes-transversais.md -->

# Decisões transversais

Este capítulo reúne as decisões que valem para o projeto inteiro. Os detalhes de cada uma estão nos capítulos das fases e nos documentos de `docs/`.

## Política de não usar APIs depreciadas

### A diretriz

A partir da Fase 3, o projeto adotou uma regra explícita: **nenhum código novo usa classe ou função marcada como depreciada**, mesmo quando o enunciado da fase a pede pelo nome. Quando um spike ou uma checagem encontrava um aviso de depreciação, a implementação parava e o caso era decidido antes de seguir. Em todos os casos a decisão foi trocar pela API atual equivalente e registrar o desvio.

Os motivos:

1. **Prazo de remoção conhecido.** Os avisos encontrados trazem a versão de remoção (LangChain 2.0, LangGraph 2.0). Código novo com essas APIs nasceria com data de expiração.
2. **A própria biblioteca indica o substituto.** Os avisos apontam para `create_agent` e para a persistência do LangGraph. Seguir a recomendação mantém o projeto no caminho que a biblioteca vai continuar mantendo.
3. **Avisos escondem problemas.** Uma execução que imprime avisos em todo import treina a equipe a ignorá-los. Com zero avisos, qualquer aviso novo é sinal de que algo mudou.

### O que foi trocado

| Fase | Pedido pelo enunciado ou candidato | Situação verificada | Substituto adotado |
|---:|------------------------------------|------------------------------|-------------------------|
| 2 | FAISS via `langchain-community` | Pacote em descontinuação ("is being sunset"), sem `langchain-faiss` no PyPI; índice persistido com pickle, exigindo desserialização insegura | Chroma embutido via `langchain-chroma`, pacote dedicado |
| 3 | `ConversationBufferMemory` | Depreciado desde o LangChain 0.3.1, remoção na 2.0 | Histórico próprio em SQLite + janela e resumo incremental próprios |
| 3 | `InMemoryChatMessageHistory` | Depreciado no `langchain-core` 1.6.4, remoção na 2.0 | O mesmo histórico em SQLite |
| 3 | LangMem (framework de memória) | A dependência obrigatória `trustcall` importa `Send` de `langgraph.constants`, depreciado no LangGraph 1.0; o aviso aparece em qualquer uso | Mem0 2.2.1, sem nenhum aviso nos spikes |
| 5 | `create_openai_tools_agent` + `AgentExecutor` | Sem aviso, mas pertencem ao pacote legado `langchain-classic` | `create_agent` do pacote `langchain` 1.4.9 (grafo LangGraph) |
| 5 | `RunnableWithMessageHistory` | Aviso no `langchain-core` 1.7.6: "Use LangGraph's built-in persistence instead"; `BaseChatMessageHistory` também depreciado | `SessionHistoryAdapter`, que lê o SQLite e o Mem0 da Fase 3 |
| 5 | Streamlit `use_container_width` | Depreciado no Streamlit 1.65 ("will be removed in a future release") | `width="stretch"` |

O caso do LangMem mostrou que a depreciação também vem de **dependências transitivas**: o pacote em si não usa nada depreciado, mas uma dependência obrigatória usa. Por isso a checagem foi feita com a pilha real instalada, e não só pela documentação.

### Como a regra foi verificada

- **Spikes com avisos visíveis:** cada candidato foi importado e executado com `warnings.simplefilter("always")` no ambiente da imagem antes da adoção.
- **Teste automático:** `test_phase3_modules_use_no_deprecated_langchain_apis` importa os módulos da Fase 3 num interpretador limpo, com `LangChainDeprecationWarning` tratado como erro. Rodar num subprocesso foi necessário porque recarregar módulos no mesmo processo recriava classes de exceção e quebrava outros testes.
- **Auditoria final (2026-11-13, refeita após as seções 5.9 a 5.11 da Fase 5):** a suíte offline completa rodou com `-W always::DeprecationWarning -W always::PendingDeprecationWarning -W always::FutureWarning`: **391 testes aprovados e nenhum aviso** (`docs/evidence/final/pytest-deprecation-audit.log`).

### A exceção: APIs legadas exigidas na Fase 2

O enunciado da Fase 2 exigia `initialize_agent`, `ZERO_SHOT_REACT_DESCRIPTION` e `RouterChain`. No LangChain 1.x essas APIs saíram do pacote `langchain` e vivem no `langchain-classic`. A política ainda não existia, e elas foram usadas como o enunciado exigia, com duas salvaguardas:

- ficam **isoladas** em `app/legacy/`, consumidas por interfaces modernas (o `CryptoAgent` cuida de memória, rastreamento e montagem da saída; o `RouterChain` só classifica);
- atendem só os endpoints da Fase 2 (`POST /agent` e `POST /route?router=legacy`).

Na Fase 5 o agente principal migrou para `create_agent`. O código legado continua no repositório só para preservar a Fase 2, e removê-lo afeta apenas `app/legacy/`.

## Desvios deliberados do enunciado

Cada desvio tem o motivo registrado no capítulo da fase correspondente.

| Fase | Enunciado | Implementado | Motivo |
|---:|------------------------------------|---------------------------|------------------------------|
| 2 | Scripts Python | Aplicação HTTP (FastAPI) | Uma entrada única com dependências criadas uma vez, no lugar de oito scripts com inicialização repetida |
| 2 | FAISS | Chroma | Descontinuação do `langchain-community`, pickle inseguro, sem atualização incremental nem filtro por metadados |
| 3 | `ConversationBufferMemory` | SQLite + janela + resumo próprios | Depreciado |
| 3 | Só comparar frameworks de memória | Usar um (Mem0) | Framework mantido para a memória de longo prazo, no lugar de código próprio; LangMem descartado por depreciação transitiva, Letta por imagem Docker sem suporte |
| 5 | `create_openai_tools_agent` + `AgentExecutor` | `create_agent` | Pacote legado; `create_agent` é o substituto oficial |
| 5 | `RunnableWithMessageHistory` | `SessionHistoryAdapter` | Depreciado |
| 5 | NLI com Transformers (`mDeBERTa-v3-base-mnli-xnli`) | NLI com `gemma4:e2b` via Ollama | Cerca de 2 GB a mais (torch, transformers e modelo) com 5,3 GB livres em disco, e um segundo motor de inferência na mesma CPU |

## Modelo local (Ollama) em CPU

### Por que um modelo local e pequeno

A escolha de servir o modelo localmente pelo Ollama, em vez de usar uma API comercial, partiu da natureza do problema:

1. **O conhecimento que importa vem das ferramentas.** Uma análise de mercado cripto depende de preços, volumes e capitalizações que mudam a cada minuto. Nenhum modelo, por maior que seja, tem esses valores no treinamento. A qualidade da resposta depende de o agente **escolher a ferramenta certa, passar os argumentos certos e usar o resultado sem inventar**, e não da memória do modelo. Por isso um modelo pequeno é suficiente, desde que siga o protocolo de ferramentas. O projeto serviu justamente para medir esse potencial: até onde um modelo local leve consegue orquestrar ferramentas reais.
2. **Privacidade.** Perguntas, histórico de conversa e memórias de longo prazo ficam na máquina. Só as consultas à CoinPaprika (símbolos de ativos) saem para a internet; o LangSmith é opcional e desligado por padrão. Quando ligado, perguntas e respostas vão para o LangSmith; a suíte offline não envia traces (desligados no `conftest` desde 2026-11-13, Fase 5, seção 5.11).
3. **Avaliar o estado dos modelos locais.** Outro objetivo era verificar a qualidade que modelos abertos já alcançam num computador pessoal, sem GPU dedicada.
4. **Custo e reprodutibilidade.** Sem cobrança por token e sem dependência de disponibilidade de um provedor; qualquer pessoa reproduz o ambiente com `make up`.

### Hardware

Todas as medições foram feitas num notebook com **AMD Ryzen 5 5500U** (6 núcleos, 12 threads), 22 GB de RAM e **sem GPU dedicada**. A inferência roda só na CPU.

### Escolha do modelo: benchmark

O benchmark da Fase 1 (`benchmarks/results/`) comparou cinco configurações nas mesmas 6 perguntas, medindo latência, validade do JSON e acerto da classificação:

| Modelo | Thinking | Latência média (s) | JSON válido | Classificação correta |
|---------------|------------|-------------------:|------------:|----------------------:|
| `qwen2.5:7b` | — | 71,0 | 100% | 83% |
| `gemma4:e4b` | desligado | 83,1 | 100% | 100% |
| `gemma4:e4b` | ligado | 118,1 | 100% | 100% |
| `mistral:7b` | — | 141,9 | 67% | 100%¹ |
| `gemma4:e2b` | desligado | **42,3** | 100% | 100% |

¹ Só sobre as respostas válidas.

O **`gemma4:e2b` (família Gemma 4) com *thinking* desligado** foi o mais rápido (cerca de 40% menos latência que o `qwen2.5:7b`), com JSON sempre válido e acerto máximo nas perguntas do conjunto. Ele virou o padrão em 2026-10-18 e foi mantido até o fim. Na Fase 5, o mesmo modelo provou suportar tool calling nativo pelo Ollama, o que permitiu migrar o agente sem trocar de modelo.

### Resultado

Considerando o hardware, o desempenho foi razoável: na avaliação final da Fase 5, o modelo escolheu a categoria de ferramenta certa em 14 dos 15 exemplos do agente (`tool_selection` 0,933), combinou API, SQL e documentos na mesma resposta e não inventou valores de mercado (`groundedness` 1,0 nas avaliações da Fase 5). Um turno completo do agente integrado, com verificação, levou de 10 s a 104 s na avaliação final; respostas com correção ou várias ferramentas chegaram a quase 3 min em execuções anteriores.

O preço de um modelo de 2B em CPU aparece em todas as fases: latência de dezenas de segundos por chamada e erros de raciocínio numérico. As duas decisões seguintes são a resposta a isso.

## Código determinístico onde o modelo pequeno falha

Uma lição repetida em todas as fases: **o que pode ser calculado por código não deve ser pedido ao modelo**.

| Fase | O modelo errou | Passou a ser feito por código |
|-----:|----------------------------------------------|-------------------------------------------------------------|
| 1 | Omitia a etapa de coleta de dados | Etapa derivada de `requested_metrics` e registrada em `assumptions` |
| 1 | Escrevia valores de mercado sem fonte | Trava que remove frases com valores e acrescenta aviso |
| 2 | Invertia comparações, mesmo com o ranking no prompt | Conclusão da rota de comparação montada por código |
| 2–4 | Inventava fontes e horários | Achados, fontes e horários extraídos das observações (grounding) |
| 3 | Instável para detectar período, correção e ativos | Regras determinísticas prevalecem sobre o LLM na intenção |
| 4 | Errava JOIN e copiava filtros indevidos | View `snapshot_history`, período por regra, SQL validado e parametrizado |
| 5 | Comparações numéricas na verificação | Checagem numérica determinística; o LLM só julga afirmações conceituais |
| 5 | Recusava pedido de recomendação sem consultar os dados dos ativos citados | Guarda `skipped_lookup` aciona a correção para consultar cada ativo |

## Segurança

- **SQL gerado por LLM** passa por duas camadas: parser (`sqlglot`) antes da execução e conexão somente leitura com `set_authorizer` e `progress_handler` durante. Os literais viram parâmetros.
- **Sem pickle:** a troca para o Chroma eliminou o `allow_dangerous_deserialization`.
- **Segredos:** chaves ficam no `.env` (fora do git); os logs redigem a chave da CoinPaprika; os traces do LangSmith recebem só um hash do `session_id`.
- **Sem autenticação:** o `session_id` não é credencial, e a listagem de conversas expõe todos os ids. Isso é aceitável apenas no uso local de um único usuário e está registrado como limitação.

## Estratégia de testes

- **Testes offline** (padrão do `pytest`): LLM falso, API simulada e bancos temporários. Rodam em segundos e cobrem contratos, regras determinísticas, segurança do SQL e fluxos completos da API.
- **Testes de integração** (opt-in com `-m integration` e `-m network`): Ollama, CoinPaprika, Chroma e Mem0 reais. Como a saída do modelo varia mesmo com temperatura 0, esses testes usam critérios de comportamento (ferramenta certa, melhora de ranking, status esperado), e não igualdade de texto.
- **Avaliação (Fase 5):** dataset versionado (20 exemplos na versão final), avaliadores de resposta e de trajetória, execução local ou no LangSmith.


\newpage


<!-- Source: Report/relatorio-final/10-conclusao.md -->

# Conclusão

## O que foi entregue

Ao fim das cinco fases, o Crypto Intelligence Agent é uma aplicação local completa:

- uma API HTTP que preserva cada fase (plano estruturado, agente ReAct, chains roteadas, conversa com memória, Text-to-Query, Retrieval Agent e agente integrado);
- um agente com tool calling nativo que combina dados ao vivo, snapshots em SQLite, documentos no Chroma e cálculo determinístico na mesma resposta;
- memória persistente por sessão (SQLite como fonte da verdade, Mem0 para memória episódica e semântica), que sobrevive a reinícios;
- verificação de cada resposta contra a evidência da própria execução, com status explícito e no máximo uma correção;
- avaliação reproduzível com dataset versionado e avaliadores de resposta e de trajetória;
- uma interface Streamlit com lista de conversas tituladas pelo modelo e detalhes da verificação em cada resposta.

## Resultados consolidados

| Fase | Medição | Resultado |
|-----:|------------------------------------------------|------------------------------------------------------|
| 1 | Benchmark de modelos em CPU (6 perguntas) | `gemma4:e2b` sem thinking: 42,3 s de latência média, 100% de JSON válido |
| 2 | Agente ReAct com dados reais | Valores, fontes e horários reais; comparação de 3 ativos com conclusão correta montada por código |
| 3 | Integração com Ollama, Mem0 e CoinPaprika reais | Continuação, correção, resultado anterior, reinício e isolamento entre sessões corretos |
| 4 | Text-to-Query real (9 perguntas) | 9 status corretos, nenhum SQL inseguro executado |
| 4 | Diagnóstico de recuperação (halving) | Trecho relevante do 3º para o 1º lugar com Query Rewriting (MRR 0,33 → 1,0) |
| 5 | Avaliação final do agente integrado | 20 de 20 exemplos executados; média de trajetória 0,962 e de resposta 0,951; `groundedness` 1,0; 14 dos 15 exemplos do agente terminaram `verified` |
| 1–5 | Suíte offline com todos os avisos visíveis | 391 testes aprovados, nenhum aviso de depreciação |

## O que funcionou

- **Ferramentas no lugar de memória do modelo.** Com os dados vindo das ferramentas, um modelo de 2B em CPU foi suficiente para orquestrar API, SQL e documentos. A hipótese que motivou o uso de um modelo local pequeno se confirmou.
- **Código determinístico nos pontos fracos do modelo.** Comparações, rankings, períodos, checagem de números e montagem de fontes saíram do LLM e passaram a ser feitos por código. Isso eliminou classes inteiras de erro observadas nas primeiras fases.
- **Separar "sem evidência" de "contra a evidência".** O desenho de status da verificação permite entregar respostas parcialmente sustentadas com transparência, em vez de rejeitá-las ou aprová-las por inteiro.
- **APIs atuais a partir da Fase 3.** A política de não usar nada depreciado evitou retrabalho: a migração do agente para `create_agent` na Fase 5 só tocou `app/integrated/`, e a auditoria final terminou sem avisos.

## Limitações

- **Latência em CPU:** um turno completo leva de 10 s a quase 3 min.
- **Verificação com modelo de 2B:** a NLI tem confiança autodeclarada e confunde "sem evidência" com "contradição" em parte dos casos; a checagem numérica confere presença de valor, não a métrica a que ele foi atribuído.
- **Juiz e agente são o mesmo modelo:** as notas do juiz são indício, compensado por avaliadores determinísticos.
- **Dados:** o plano gratuito da CoinPaprika não oferece série histórica; o histórico local depende de coletas manuais.
- **Profundidade desigual nas comparações entre redes:** a base local cobre a fundo Bitcoin, Ethereum e Solana; as demais redes contam só com o perfil da CoinPaprika (consenso e descrição curta).
- **Sem autenticação:** adequado só ao uso local de um usuário.
- **LangSmith:** o rastreamento de uso real foi analisado (13 turnos; a verificação ocupa 48% do tempo do turno na mediana) e revelou defeitos que a avaliação agregada não mostrava, já corrigidos; o dataset e os experimentos ainda não foram rodados no LangSmith.

## Trabalhos futuros

1. NLI com modelo de classificação dedicado (mesma interface `classify`), se houver disco disponível.
2. Coleta agendada de snapshots e uma fonte de séries históricas (CoinGecko) para perguntas de longo prazo.
3. Extração de afirmações com divisão de fatos compostos e checagem da métrica atribuída a cada número.
4. Remoção do código legado da Fase 2 (`app/legacy/`), já substituído pelo agente integrado.
5. Autenticação e dono por sessão antes de qualquer uso multiusuário.
6. Fonte de referência externa (Wikipedia ou documentação oficial) com cache no Chroma para aprofundar redes fora da base, e a comparação entre redes no dataset de avaliação.
7. Experimentos no LangSmith para comparar versões de prompt e de modelo ao longo do tempo.


\newpage


<!-- Source: Report/relatorio-final/99-referencias.md -->

# Referências {.unnumbered}

**Artigos**

- LEWIS, P. et al. Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS*, 2020.
- MA, X. et al. Query Rewriting for Retrieval-Augmented Large Language Models. *EMNLP*, 2023.
- NUSSBAUM, Z. et al. Nomic Embed: Training a Reproducible Long Context Text Embedder. *arXiv:2402.01613*, 2024.
- WILLIAMS, A.; NANGIA, N.; BOWMAN, S. A Broad-Coverage Challenge Corpus for Sentence Understanding through Inference. *NAACL*, 2018.
- YAO, S. et al. ReAct: Synergizing Reasoning and Acting in Language Models. *ICLR*, 2023.

**Modelos**

- Google DeepMind. Gemma 4 (`gemma4:e2b`, `gemma4:e4b`), via biblioteca de modelos do Ollama: <https://ollama.com/library/gemma4>.
- Qwen Team. Qwen2.5 (`qwen2.5:7b`): <https://ollama.com/library/qwen2.5>.
- Mistral AI. Mistral 7B (`mistral:7b`): <https://ollama.com/library/mistral>.
- Nomic AI. `nomic-embed-text`: <https://ollama.com/library/nomic-embed-text>.
- LAURER, M. `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` (sugerido pelo enunciado, não adotado): <https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli>.

**Fonte de dados**

- CoinPaprika API: <https://api.coinpaprika.com/>.

**Bibliotecas e documentação**

- LangChain (`langchain`, `langchain-core`, `langchain-classic`, `langchain-ollama`, `langchain-chroma`): <https://docs.langchain.com/>.
- LangGraph: <https://docs.langchain.com/oss/python/langgraph/overview>.
- LangSmith: <https://docs.smith.langchain.com/>.
- Ollama: <https://ollama.com/>.
- Chroma: <https://docs.trychroma.com/>.
- Mem0: <https://docs.mem0.ai/>.
- sqlglot: <https://github.com/tobymao/sqlglot>.
- SQLite (`sqlite3.Connection.set_authorizer`, modo somente leitura por URI): <https://docs.python.org/3/library/sqlite3.html>.
- FastAPI: <https://fastapi.tiangolo.com/>.
- Pydantic: <https://docs.pydantic.dev/>.
- Streamlit: <https://docs.streamlit.io/>.


\newpage
