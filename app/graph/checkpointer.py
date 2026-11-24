"""Checkpointer SQLite do grafo e identificação das threads.

Divisão de responsabilidades (decisão aprovada em 2026-11-23, ver docs/langgraph-persistence.md):

| Armazenamento | Responsabilidade |
|---|---|
| ``data/conversations.sqlite3`` | Fonte da verdade da conversa: mensagens exibidas, resumos, análises |
| Mem0 (``data/memory_chroma`` + ``data/mem0_history.sqlite3``) | Memória episódica e semântica |
| ``data/chroma`` | Base de conhecimento (RAG) |
| ``data/market.sqlite3`` | Snapshots de mercado |
| **``data/langgraph_checkpoints.sqlite3``** | **Estado de execução do grafo por thread: mensagens com tool calls e observações, evidência, verificação, correção** |

O checkpoint não substitui o histórico da conversa: a API continua lendo e gravando mensagens no SQLite de
conversas. Ele guarda o que o SQLite de conversas não guarda (chamadas de ferramenta e observações dos turnos
anteriores, estado intermediário de um turno interrompido) e é apagado junto com a sessão.
"""

import sqlite3
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph

DEFAULT_ARCHITECTURE = "react"


def open_checkpointer(path: str | Path) -> SqliteSaver:
    """``SqliteSaver`` sobre um arquivo próprio. ``check_same_thread=False`` é seguro: o saver serializa o acesso
    com um ``threading.Lock`` (langgraph-checkpoint-sqlite 3.1.3), e o FastAPI síncrono roda rotas em threads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
    saver.setup()
    return saver


def close_checkpointer(saver: SqliteSaver) -> None:
    saver.conn.close()


def thread_id_for(session_id: str, architecture: str = DEFAULT_ARCHITECTURE) -> str:
    """``thread_id`` de uma sessão: o próprio id da conversa (``uuid4().hex``, único por sessão).

    Arquiteturas diferentes (ReAct, Reflexion) têm esquemas de estado diferentes; se a mesma sessão usar as duas,
    cada uma tem sua thread (``<sessão>:<arquitetura>``) para que um estado não seja lido pelo grafo errado.
    """
    if not session_id:
        raise ValueError("session_id vazio: toda execução com checkpoint precisa de uma sessão")
    return session_id if architecture == DEFAULT_ARCHITECTURE else f"{session_id}:{architecture}"


def thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def pending_nodes(graph: CompiledStateGraph, thread_id: str) -> tuple[str, ...]:
    """Nós que ainda iam executar quando a última execução da thread parou (vazio = turno concluído)."""
    return tuple(graph.get_state(thread_config(thread_id)).next)


def delete_session_threads(saver: SqliteSaver, session_id: str, architectures: tuple[str, ...] = ()) -> None:
    """Apaga os checkpoints da sessão (todas as arquiteturas informadas). Chamado quando a conversa é apagada."""
    for architecture in (DEFAULT_ARCHITECTURE, *architectures):
        saver.delete_thread(thread_id_for(session_id, architecture))
