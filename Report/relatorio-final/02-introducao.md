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
