# Design de prompts — Fase 2

Implementação: [app/prompts/ptcf.py](../app/prompts/ptcf.py). Os prompts da Fase 1 (`app/prompts/analysis.py`) não foram alterados.

## PTCF reutilizável

| Seção | Conteúdo |
|---|---|
| **Persona** | Analista de inteligência de criptoativos; responde em português; só afirma o que as fontes sustentam |
| **Tarefa** | Interpretar o pedido, escolher ferramentas, reunir evidências, produzir análise curta e verificável |
| **Contexto** | Pergunta, histórico recente, Observations das ferramentas, data de referência e **limitações das fontes**: dados atuais só nas ferramentas de mercado; sem séries históricas nem DeFi nesta fase; conhecimento sem preços; histórico = valores antigos |
| **Formato** | Varia por uso: ReAct → "Final Answer" de 2 a 5 frases citando fonte e horário; chains → JSON validado por Pydantic |

O mesmo `ptcf_sections(format_section)` gera o prefixo do agente (`react_prefix()`) e o system prompt das etapas JSON (`json_system_prompt(schema_hint)`). Regras comuns: não inventar valores, relatar falhas, citar fonte e horário, consultar cada ativo separadamente, pedir esclarecimento quando faltar o ativo, sem previsões nem recomendações.

## Exemplos few-shot (ReAct)

| Exemplo | Comportamento ensinado |
|---|---|
| Consulta de mercado | Uma ferramenta → resposta com valor, fonte e horário |
| Comparação de ativos | Uma chamada por ativo → comparação + limitação ("só 24h, sem histórico") |
| Informação ausente | Sem ferramenta → pedir o ativo em vez de chutar |
| Falha da API | `ok=false` → explicar a falha, "nenhum valor foi estimado" |

**Ativo fictício ("ExemploCoin", EXC/DEF):** os exemplos ensinam o padrão sem plantar números de ativos reais. Na Fase 1 vimos o modelo copiar o exemplo (`docs/stage-1-evidence.md`). Se mesmo assim copiar um valor, a verificação de grounding o remove, porque ele não existe nas observações.

## Restrições do protocolo ReAct

- As palavras-chave `Thought`, `Action`, `Action Input`, `Observation` e `Final Answer` ficam em **inglês**: o `MRKLOutputParser` depende delas. O resto do prompt é em português.
- O JSON dos exemplos é escapado (`{` → `{{`) porque o prefixo vira `PromptTemplate`.
- Variáveis do prompt: `input`, `history`, `reference_time`, `agent_scratchpad`. O histórico e a data entram no sufixo, perto da pergunta.
- `num_ctx=8192`: prefixo + ferramentas + exemplos somam cerca de 1.700 tokens antes das observações. O padrão do Ollama (4.096) truncaria uma conversa com histórico e 3–4 observações.

## Decisões sem "chain-of-thought" exposto

O rastro de execução registra **decisões** (ferramenta + entrada), chamadas (duração, ok/erro) e uso do LLM (tokens, duração). O texto "Thought" do modelo não é registrado nem devolvido. O `CryptoReport.decision_summary` mostra o que foi feito, não o raciocínio privado.

## Saída montada fora do LLM

O "Final Answer" é só o texto. Achados, fontes, horários e status vêm de código (`app/services/grounding.py`):

- **Achados e fontes:** extraídos das observações bem-sucedidas.
- **Verificação de valores:** todo valor monetário ou percentual da resposta precisa existir nas observações (tolerância de 1%; percentuais comparados em módulo; com multiplicador, "1.638 trilhões" aceita as leituras pt e en). Frases com valores não confirmados são removidas e registradas como incerteza.
- **Status:** `complete` / `partial` / `failed` / `unavailable`, decidido pelas falhas das ferramentas e pelos limites de execução, nunca pelo modelo.

## Prompts das chains

| Etapa | Esquema JSON | Observações |
|---|---|---|
| Extração de ativos | `{"assets": [...]}` (máx. 5) | Ativos explícitos (`--assets`) pulam o LLM |
| Classificação de rota | `{"route", "reason"}` | Rota inválida → regras de palavras-chave |
| Análise de mercado/comparação | `{"summary", "key_points", "uncertainties"}` | Recebe os DADOS e os **rankings calculados no código** |
| Conhecimento | `{"answer", "used_sources"}` | "Responda só com base nos TRECHOS" |

**Rankings calculados e conclusão determinística na comparação:** nos testes reais, o modelo errou comparações mesmo com números corretos ("BTC teve a maior queda" quando foi o ETH; "ETH tem market cap maior"). Primeiro, a ordenação por variação 24h e por market cap passou a ser calculada no código, enviada ao modelo e registrada como achado. **Mesmo assim**, em nova execução real, o resumo do `gemma4:e2b` voltou a inverter a ordem, contradizendo o ranking que recebeu. Decisão final: na rota `asset_comparison`, o `answer` é montado pelo código a partir dos rankings ("Variação 24h, do maior para o menor: SOL -0,50% > BTC -1,06% > ETH -1,17%..."), e o resumo livre do LLM é descartado; os pontos-chave do LLM, verificados, permanecem em `key_points`.
