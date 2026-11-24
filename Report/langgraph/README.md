# Report — Crypto Intelligence Agent: evolução LangGraph

Diário técnico de um **projeto novo**. Ele usa como base o código do projeto anterior (Fases 1 a 5, diário em
[../README.md](../README.md)) e não faz parte dele: o relatório das Fases 1 a 5 (`docs/final-report.md`) fica
fechado e não é alterado.

| | Projeto anterior (base) | Este projeto |
|---|---|---|
| Escopo | Fases 1 a 5: chain, agente com ferramentas, memória, RAG, agente integrado (`create_agent`), verificação, avaliação | `StateGraph` explícito, `ToolNode` + `tools_condition`, checkpointer SQLite com `thread_id`, gestão de histórico, Reflexion × ReAct |
| Código | `app/` até o commit `0f9e5a6` (mantido como baseline) | Novos módulos sobre o mesmo `app/`, reaproveitando ferramentas, verificação e memória |
| Branch | `main` | `feature/langgraph-stategraph-agent` |
| Diário | `Report/fase-NN-*.md` | `Report/langgraph/etapa-NN-*.md` |
| Relatório final | `docs/final-report.md` | `docs/langgraph-final-report.md` (capítulos em `Report/langgraph/relatorio-final/`) |

## Etapas

| Etapa | Documento | Status |
|---|---|---|
| 0 — Avaliação do repositório-base e branch | [etapa-00-avaliacao-e-branch.md](etapa-00-avaliacao-e-branch.md) | Concluída em 2026-11-23 |
| 1 — StateGraph mínimo (agente, ToolNode, tools_condition) | [etapa-01-stategraph-minimo.md](etapa-01-stategraph-minimo.md) | Concluída em 2026-11-23 |
| 2 — Integração completa das ferramentas, evidência e verificação | [etapa-02-ferramentas-evidencia.md](etapa-02-ferramentas-evidencia.md) | Concluída em 2026-11-24 |
| 3 — Checkpointer persistente e threads | [etapa-03-checkpointer-threads.md](etapa-03-checkpointer-threads.md) | Concluída em 2026-11-24 |
| 4 — Gestão do histórico | — | Pendente |
| 5 — Reflexion | — | Pendente |
| 6 — Avaliação ReAct × Reflexion | — | Pendente |
| 7 — Integração na aplicação | — | Pendente |
| 8 — Documentação e relatório final | — | Pendente |

## Convenções

As mesmas do diário anterior: datas absolutas, versões e ambiente junto de cada métrica, toda afirmação de resultado
aponta para a evidência, o que não funcionou também é registrado. Estrutura de cada etapa a partir de
[../_modelo-fase.md](../_modelo-fase.md).
