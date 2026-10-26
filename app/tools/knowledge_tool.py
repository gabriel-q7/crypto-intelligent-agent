"""Ferramenta de busca semântica na base de conhecimento local (Chroma)."""

import json
from collections.abc import Callable

from langchain_core.tools import Tool
from langchain_core.vectorstores import VectorStore

from app.config import get_knowledge_settings
from app.knowledge.store import KnowledgeIndexError, load_index, search

TOOL_NAME = "search_knowledge_base"


def build_knowledge_tool(
    index_loader: Callable[[], VectorStore] = load_index, top_k: int | None = None
) -> Tool:
    top_k = top_k or get_knowledge_settings().top_k
    cache: dict[str, VectorStore] = {}

    def run(query: str) -> str:
        query = query.strip().strip("'\"`")
        if not query:
            return json.dumps({"ok": False, "tool": TOOL_NAME, "error": {"kind": "invalid_argument", "message": "consulta vazia"}})
        try:
            if "index" not in cache:
                cache["index"] = index_loader()
            hits = search(cache["index"], query, top_k)
        except KnowledgeIndexError as exc:
            return json.dumps({"ok": False, "tool": TOOL_NAME, "error": {"kind": "index_unavailable", "message": str(exc)}}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "tool": TOOL_NAME,
                "data": [{"source": h.source, "title": h.title, "content": h.content} for h in hits],
                "source": {"provider": "base de conhecimento local (Chroma)"},
            },
            ensure_ascii=False,
        )

    return Tool(
        name=TOOL_NAME,
        func=run,
        description=(
            "Busca CONCEITOS na base de conhecimento local (blockchain, Bitcoin, Ethereum, staking, "
            "DeFi, TVL, stablecoins, métricas e riscos). Não contém preços nem dados atuais. "
            "Entrada: pergunta ou termo (ex.: 'o que é TVL')."
        ),
    )
