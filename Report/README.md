# Report — Diário técnico por fase

Esta pasta reúne a documentação escrita **durante** o desenvolvimento, fase a fase. Ela é a matéria-prima do relatório técnico final.

## Diferença em relação a `docs/`

| Pasta | Conteúdo | Quando muda |
|---|---|---|
| `docs/` | Documentação de referência do sistema **como ele é agora** (arquitetura, decisões vigentes, evidências da fase) | Atualizada quando o sistema muda |
| `Report/` | Narrativa **do que aconteceu**: hipóteses, decisões e seus motivos, problemas, tentativas que falharam, métricas, aprendizados | Só recebe acréscimos; o histórico não é reescrito |

## Fases

| Fase | Documento | Status |
|---|---|---|
| 1 — Chain estruturada (LangChain + Ollama, sem APIs) | [fase-01-chain-estruturada.md](fase-01-chain-estruturada.md) | Concluída |
| 2 — Agente com ferramentas (CoinPaprika, ReAct, roteamento, memória, Chroma, API HTTP) | [fase-02-agente-ferramentas.md](fase-02-agente-ferramentas.md) | Concluída |
| 3 — Memória conversacional (SQLite, Mem0/Chroma, intenção e contexto, sessões persistentes) | [fase-03-memoria-conversacional.md](fase-03-memoria-conversacional.md) | Concluída |
| 4 — Ferramentas externas e RAG existente (SQLite de snapshots, Text-to-Query, Retrieval Agent, Query Rewriting) | [fase-04-ferramentas-rag.md](fase-04-ferramentas-rag.md) | Concluída |
| 5 — Agente integrado (`create_agent`), verificação de respostas, avaliação (LangSmith) e interface Streamlit | [fase-05-agente-integrado.md](fase-05-agente-integrado.md) | Concluída (diário consolidado em 2026-11-08; adendo em 2026-11-12: seção 5.9) |

## Convenções

- Um arquivo por fase: `fase-NN-<tema>.md`, a partir de [_modelo-fase.md](_modelo-fase.md).
- Registrar **datas absolutas** (AAAA-MM-DD), versões de modelo e bibliotecas e o ambiente (CPU/GPU) junto de cada métrica.
- Toda afirmação de resultado aponta para a evidência (saída de comando, teste, arquivo do repositório).
- Registrar também o que **não funcionou** e por quê. Para o relatório final, isso vale tanto quanto o que funcionou.
- Pendências e perguntas abertas ficam no fim de cada fase e são retomadas na fase seguinte.

## Relatório final

Os capítulos comuns (repositório, uso de IA, introdução, arquitetura, decisões transversais, conclusão e referências) ficam em [relatorio-final/](relatorio-final/). O relatório completo é montado com:

```bash
make report        # ou ./scripts/generate-report.sh
```

O script confere os números citados contra as evidências (`scripts/verify_report_numbers.py`), monta `docs/final-report.md` na ordem definida em `REPORT_SOURCES` e gera o PDF com pandoc + XeLaTeX. Se algum número não bater com a evidência, nada é gerado.
