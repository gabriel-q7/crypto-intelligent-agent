"""Retrieval Agent como ferramenta do agente ReAct.

Mantém o nome ``search_knowledge_base`` da Fase 2 (prompt, regras e ancoragem já o conhecem),
mas agora passa pelo ``RetrievalAgent``: pergunta original, Query Rewriting quando a busca é
fraca e limiar de relevância. ``data`` mantém o formato anterior (lista de trechos com
``source``/``title``/``content``) acrescido de ``score`` e ``chunk_id``; a decisão de reescrita
fica em ``retrieval``.
"""

from langchain_core.tools import Tool

from app.knowledge.store import KnowledgeIndexError
from app.retrieval.agent import RetrievalAgent
from app.tools.market_tools import error_observation, to_json

TOOL_NAME = "search_knowledge_base"
PROVIDER = "base de conhecimento local (Chroma)"


def build_retrieval_tool(agent: RetrievalAgent) -> Tool:
    def run(raw: str) -> str:
        query = (raw or "").strip().strip("'\"`").strip()
        if len(query) < 2:
            return to_json(error_observation(TOOL_NAME, "invalid_argument", "consulta vazia"))
        try:
            result = agent.retrieve(query[:300])
        except KnowledgeIndexError as exc:
            return to_json(error_observation(TOOL_NAME, "index_unavailable", str(exc)))
        retrieval = {
            "original_query": result.original_query,
            "rewritten_query": result.rewritten_query,
            "rewrite_applied": result.rewrite_applied,
            "decision": result.decision,
        }
        if result.status == "no_relevant_documents":
            observation = error_observation(
                TOOL_NAME,
                "no_relevant_documents",
                "a base de conhecimento local não tem trechos relevantes para esta pergunta",
            )
            observation["retrieval"] = retrieval
            return to_json(observation)
        return to_json({
            "ok": True,
            "tool": TOOL_NAME,
            "data": [
                {"source": d.source, "title": d.title, "chunk_id": d.chunk_id, "score": d.score, "content": d.content}
                for d in result.documents
            ],
            "retrieval": retrieval,
            "source": {"provider": PROVIDER, "endpoint": "crypto_knowledge"},
        })

    return Tool(
        name=TOOL_NAME,
        func=run,
        description=(
            "Busca CONCEITOS na base de conhecimento local (blockchain, consenso PoW/PoS, Bitcoin, Ethereum, "
            "staking, DeFi, TVL, stablecoins, métricas e riscos); reescreve a consulta quando o vocabulário "
            "não casa com os documentos. Não contém preços nem dados atuais. "
            "Entrada: a pergunta conceitual completa do usuário, sem resumir (ex.: 'O que é slashing e "
            "quando ele acontece?')."
        ),
    )
