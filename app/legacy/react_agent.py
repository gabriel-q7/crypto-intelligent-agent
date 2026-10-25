"""Agente ReAct clássico: ``initialize_agent`` + ``AgentType.ZERO_SHOT_REACT_DESCRIPTION``.

API LEGADA, isolada neste módulo. No LangChain 1.x ela vive no pacote ``langchain-classic``
e está depreciada. Alternativa moderna: ``langchain.agents.create_agent`` ou um grafo
LangGraph com tool calling nativo (ver docs/orchestrator-comparison.md).

Limitações relevantes (documentadas em docs/reliability-decisions.md):
- ferramentas só com UMA entrada em texto;
- protocolo em texto livre (Thought/Action/...): o parser depende do modelo seguir o formato;
- sem memória nativa: o histórico entra como variável do prompt.
"""

import warnings

from langchain_core.language_models import BaseLanguageModel
from langchain_core.tools import BaseTool

from app.config import AgentSettings
from app.prompts.ptcf import (
    REACT_FORMAT_INSTRUCTIONS,
    REACT_INPUT_VARIABLES,
    REACT_SUFFIX,
    react_prefix,
)

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from langchain_classic.agents import AgentExecutor, AgentType, initialize_agent

# Devolvido ao modelo quando ele foge do formato; o executor tenta de novo (conta como iteração).
PARSING_ERROR_FEEDBACK = (
    "Formato inválido. Responda com 'Action:' e 'Action Input:' para usar uma ferramenta, "
    "ou com 'Final Answer:' para concluir."
)


def build_react_executor(
    llm: BaseLanguageModel, tools: list[BaseTool], settings: AgentSettings
) -> AgentExecutor:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # LangChainDeprecationWarning de initialize_agent
        return initialize_agent(
            tools,
            llm,
            agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            agent_kwargs={
                "prefix": react_prefix(),
                "format_instructions": REACT_FORMAT_INSTRUCTIONS,
                "suffix": REACT_SUFFIX,
                "input_variables": REACT_INPUT_VARIABLES,
            },
            max_iterations=settings.max_iterations,
            max_execution_time=settings.max_execution_time_s,
            # "force": ao atingir o limite, devolve uma mensagem fixa em vez de lançar exceção.
            early_stopping_method="force",
            handle_parsing_errors=PARSING_ERROR_FEEDBACK,
            return_intermediate_steps=True,
            verbose=False,
        )
