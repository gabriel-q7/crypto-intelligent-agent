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

## Convenções

- Um arquivo por fase: `fase-NN-<tema>.md`, a partir de [_modelo-fase.md](_modelo-fase.md).
- Registrar **datas absolutas** (AAAA-MM-DD), versões de modelo e bibliotecas e o ambiente (CPU/GPU) junto de cada métrica.
- Toda afirmação de resultado aponta para a evidência (saída de comando, teste, arquivo do repositório).
- Registrar também o que **não funcionou** e por quê. Para o relatório final, isso vale tanto quanto o que funcionou.
- Pendências e perguntas abertas ficam no fim de cada fase e são retomadas na fase seguinte.
