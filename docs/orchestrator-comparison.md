# Comparação de orquestradores de agentes

Contexto: agente local (Ollama, modelo pequeno em CPU), poucas ferramentas HTTP, saída validada por Pydantic, testes offline com LLM falso. Comparamos o que o projeto usa (LangChain clássico + LCEL) com três alternativas.

## Critérios

- **Autonomia:** quanto o orquestrador deixa o modelo decidir (ferramentas, laços, planejamento).
- **Abstração:** quanto esconde do fluxo real; mais abstração = menos código, menos controle.
- **Modularidade:** facilidade de trocar partes (modelo, ferramentas, memória, roteamento).
- **Testes:** facilidade de testar sem modelo e sem rede.
- **Complexidade de integração:** esforço para ligar Ollama, APIs HTTP e Pydantic.

## Comparação

| Critério | LangChain clássico (`initialize_agent`, `RouterChain`) | LangChain LCEL / `create_agent` | LangGraph | LlamaIndex (agentes + workflows) |
|---|---|---|---|---|
| **Autonomia** | Média: laço ReAct fixo (pensar → agir → observar), tipos de agente pré-definidos | Média: `create_agent` faz o laço de tool calling; LCEL puro é determinístico | Alta e controlável: o grafo define estados, laços, ramificações e pontos de intervenção humana | Média-alta: agentes ReAct/function-calling e workflows orientados a eventos |
| **Abstração** | Alta e opaca: prompt, parser e laço embutidos; customizar exige `agent_kwargs` | Média: composição explícita de runnables | Baixa-média: você escreve nós e arestas; o estado é explícito | Alta para RAG (índices, retrievers prontos); média para workflows |
| **Modularidade** | Baixa: ferramentas só com entrada em texto (zero-shot), memória acoplada ao tipo de agente | Alta: cada etapa é um runnable trocável | Alta: nós independentes, estado tipado, subgrafos | Alta no ecossistema dele; integra ferramentas LangChain por adaptadores |
| **Testes** | Razoável: `FakeListLLM` roteiriza o ReAct, mas os testes dependem do formato textual | Boa: runnables testáveis isoladamente com LLM falso | Boa: nós são funções puras sobre o estado; dá para testar transições | Boa para retrievers; agentes exigem mocks do LLM do próprio framework |
| **Integração** (Ollama, HTTP, Pydantic) | Simples: `ChatOllama` + `Tool`, mas protocolo em texto frágil com modelos pequenos | Simples: mesmas integrações; saída estruturada nativa | Média: mais conceitos (estado, checkpointer), mesmas integrações LangChain | Média: integrações próprias (`llama-index-llms-ollama`), outro modelo de documentos |
| **Situação** | **Depreciado** (`langchain-classic`) | Caminho recomendado no LangChain 1.x | Recomendado para agentes com estado | Ativo |

## Por que este projeto usa LangChain (híbrido)

1. **Requisito do enunciado:** `initialize_agent`, `ZERO_SHOT_REACT_DESCRIPTION` e `RouterChain` são obrigatórios. Ficaram isolados em `app/legacy/`.
2. **Continuidade:** a Fase 1 já era LCEL. Chains, roteamento padrão e conhecimento seguem em LCEL, com testes offline.
3. **Controle da saída:** o orquestrador decide *o que* chamar; a montagem do `CryptoReport` é código próprio. Fontes, horários e verificação de valores não dependem do framework, o que reduz o custo de trocar de orquestrador.

## Limitações observadas do legado (na prática)

- Ferramentas de entrada única: foi preciso normalizar texto livre do modelo (`'"bitcoin"'`, `asset=eth`).
- Formato textual (`Action:` / `Action Input:` / `Final Answer:`): depende do modelo seguir o protocolo; erros de formato consomem iterações (`handle_parsing_errors`).
- Sem memória nativa no zero-shot: o histórico entrou como variável do prompt.
- `RouterChain` espera destinos `LLMChain`; aqui só a classificação é usada.
- Avisos de depreciação suprimidos dentro de `app/legacy/` para não poluir o restante.

## Recomendação para as próximas fases

Migrar o agente para **LangGraph** (ou `create_agent`) com tool calling nativo do Ollama quando o modelo suportar bem. Isso traz ferramentas com argumentos tipados (Pydantic), estado explícito para memória e planejamento hierárquico, e checkpoints. Mantém `app/tools`, `app/services/grounding` e `CryptoReport` sem mudanças. LlamaIndex seria interessante só se a base de conhecimento crescer a ponto de precisar de recursos avançados de RAG.
