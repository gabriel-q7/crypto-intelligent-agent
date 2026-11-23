"""Agente em grafo explícito (projeto LangGraph, sobre o código das Fases 1 a 5).

Diferente do agente integrado (``app/integrated``), que usa o grafo pronto de ``create_agent``, aqui cada
etapa é um nó de um ``StateGraph`` e cada transição é uma aresta declarada. Ver docs/langgraph-architecture.md.
"""

from app.graph.builder import build_react_graph
from app.graph.run import GraphRun, graph_config, run_react

__all__ = ["GraphRun", "build_react_graph", "graph_config", "run_react"]
