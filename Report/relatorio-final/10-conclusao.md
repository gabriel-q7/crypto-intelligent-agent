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
| 1–5 | Suíte offline com todos os avisos visíveis | 383 testes aprovados, nenhum aviso de depreciação |

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
- **LangSmith:** implementado, mas não exercitado neste ambiente; os resultados vêm da execução local equivalente.

## Trabalhos futuros

1. NLI com modelo de classificação dedicado (mesma interface `classify`), se houver disco disponível.
2. Coleta agendada de snapshots e uma fonte de séries históricas (CoinGecko) para perguntas de longo prazo.
3. Extração de afirmações com divisão de fatos compostos e checagem da métrica atribuída a cada número.
4. Remoção do código legado da Fase 2 (`app/legacy/`), já substituído pelo agente integrado.
5. Autenticação e dono por sessão antes de qualquer uso multiusuário.
6. Fonte de referência externa (Wikipedia ou documentação oficial) com cache no Chroma para aprofundar redes fora da base, e a comparação entre redes no dataset de avaliação.
7. Experimentos no LangSmith para comparar versões de prompt e de modelo ao longo do tempo.
