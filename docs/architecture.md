# Arquitetura — Crypto Intelligence Agent

## 1. Anatomia do agente

Um agente é composto por quatro blocos. A tabela mostra o que existe na Etapa 1 e o que fica para depois.

| Bloco | Papel | Etapa 1 |
|---|---|---|
| **Percepção** | Receber e normalizar a entrada | `CryptoQuestion` (Pydantic): pergunta, ativos, período, contexto |
| **Raciocínio** | Interpretar a pergunta e decidir o que investigar | `ChatPromptTemplate` + `ChatOllama` (`qwen2.5:7b`) |
| **Ação** | Agir no mundo (APIs, ferramentas) | **Não implementado** — a saída é apenas um plano |
| **Memória** | Guardar estado entre interações | **Não implementado** |

Fluxo atual:

```
CryptoQuestion
   │  build_prompt_input  (PromptTemplate renderiza o bloco da pergunta)
   ▼
ANALYSIS_PROMPT  (ChatPromptTemplate: system + exemplo few-shot + pergunta com data de referência e format_instructions)
   ▼
ChatOllama  (format="json", temperature=0)
   ▼
JsonOutputParser  (texto -> dict; suporta parciais no stream)
   ▼
validate_analysis  (dict -> CryptoAnalysisRequest, erros -> AnalysisOutputError)
```

Camadas do código:

- `app/schemas` — contratos de entrada e saída (Pydantic).
- `app/prompts` — instruções de sistema e templates.
- `app/chains` — composição da chain (LCEL padrão e `@chain`).
- `app/services` — fábrica do LLM, validação e fachada `run_analysis` / `run_batch` / `stream_analysis` com tradução de erros.
- `scripts` — pontos de entrada CLI.

## 2. Classificação atual: agente reativo

A Etapa 1 é um **agente reativo simples**: cada pergunta gera exatamente uma chamada ao modelo e uma resposta, sem estado, sem ciclo de observação e sem ferramentas. O modelo **não responde** a pergunta; ele a **interpreta** e produz um `CryptoAnalysisRequest` (intenção, ativos, métricas, etapas de investigação, lacunas, premissas, confiança).

Consequências:

- Determinístico o bastante para testes (temperatura 0, modo JSON).
- Não acessa dados de mercado; por isso o prompt proíbe inventar preços, volumes ou dados atuais, e essas necessidades vão para `requested_metrics` e `missing_information`.
- `investigation_steps` já é um plano — mas apenas descrito, não executado.

## 3. Evolução para arquitetura híbrida

A arquitetura híbrida combina uma **camada deliberativa** (planeja) com uma **camada reativa** (executa passos curtos e reage a resultados).

```
           ┌──────────────── camada deliberativa ────────────────┐
pergunta → │ chain da Etapa 1 → CryptoAnalysisRequest (plano)    │
           └──────────────────────────┬──────────────────────────┘
                                      ▼
           ┌──────────────── camada reativa ─────────────────────┐
           │ executor: para cada etapa → ferramenta (CoinGecko,  │
           │ CoinPaprika, DeFiLlama) → observação → próxima etapa│
           └──────────────────────────┬──────────────────────────┘
                                      ▼
                         síntese final com dados reais
```

O `CryptoAnalysisRequest` é o contrato entre as camadas: `requested_metrics` e `assets` indicam quais ferramentas chamar; `missing_information` indica quando perguntar ao usuário em vez de agir.

## 4. Planejamento hierárquico

Nas próximas etapas, o plano pode ser decomposto em níveis:

1. **Objetivo** — a intenção (`intent`), ex.: `comparison`.
2. **Subobjetivos** — cada item de `investigation_steps`, ex.: "obter série de preços de BTC e ETH".
3. **Ações primitivas** — chamadas concretas de ferramentas, ex.: `get_price_history(asset="BTC", days=30)`.

Cada nível é validado por schema próprio antes de descer ao seguinte, o que mantém o padrão desta etapa: o LLM propõe, o Pydantic valida.

## 5. Memória de curto e longo prazo (conceito)

| Tipo | O que guarda | Exemplo | Implementação futura possível |
|---|---|---|---|
| **Curto prazo** | Contexto da sessão atual | Perguntas anteriores, ativos já citados, resultados intermediários do executor | Histórico de mensagens no prompt (`MessagesPlaceholder`), estado do grafo |
| **Longo prazo** | Conhecimento entre sessões | Preferências do usuário (moeda de cotação, perfil de risco), análises passadas | Banco de dados / vetor store com recuperação (RAG) |

Na Etapa 1 **não há memória**: o campo `context` de `CryptoQuestion` é o ponto de entrada natural para injetar memória de curto prazo depois, sem mudar o contrato.
