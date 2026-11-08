# Repositório do código {.unnumbered}

Todo o código deste trabalho está no GitHub:

**<https://github.com/gabriel-q7/crypto-intelligent-agent>**

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
