"""Fase 4: Query Rewriting e Retrieval Agent (índice falso, LLM roteirizado)."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import ValidationError

from app.config import RetrievalSettings
from app.retrieval.agent import RetrievalAgent, anchor_terms
from app.retrieval.rewriter import QueryRewrite, QueryRewriteError, QueryRewriter
from app.tools.retrieval_tool import build_retrieval_tool
from tests.unit.phase4_fakes import FakeIndex

SETTINGS = RetrievalSettings(rewrite_mode="auto", rewrite_trigger_distance=0.33, relevance_max_distance=0.38, top_k=3)
QUESTION = "Por que a recompensa dos mineradores cai pela metade?"
REWRITTEN = "halving redução da recompensa de bloco pela metade"
REWRITE_JSON = json.dumps({
    "main_concept": "halving", "intent": "entender a redução da emissão",
    "rewritten_query": REWRITTEN, "added_terms": ["recompensa de bloco", "blockchain"],
})
INDEX = {
    QUESTION: [("blockchain-basics.md#3", "Fundamentos", 0.345), ("bitcoin.md#0", "Bitcoin", 0.351), ("bitcoin.md#1", "Bitcoin", 0.353)],
    REWRITTEN: [("bitcoin.md#1", "Bitcoin", 0.20), ("bitcoin.md#0", "Bitcoin", 0.30), ("risks.md#12", "Riscos", 0.31)],
    "Explique proof of stake": [("ethereum-staking.md#8", "Ethereum e staking", 0.30)],
    "queima de moedas": [("market-metrics.md#9", "Métricas", 0.437)],
    "token burn blockchain": [("blockchain-basics.md#2", "Fundamentos", 0.18)],
}
CONTENTS = {
    "bitcoin.md#1": "## Halving\nO halving reduz pela metade a recompensa por bloco.",
    "bitcoin.md#0": "# Bitcoin\nNovas moedas entram como recompensa de bloco paga aos mineradores.",
    "risks.md#12": "# Riscos em criptoativos",
    "blockchain-basics.md#2": "Uma blockchain é um registro distribuído.",
    "blockchain-basics.md#3": "## Consenso\nO mecanismo de consenso define quem adiciona o próximo bloco.",
}


def agent(*replies: str, index: FakeIndex | None = None, settings: RetrievalSettings = SETTINGS):
    index = index or FakeIndex(INDEX, CONTENTS)
    llm = FakeListChatModel(responses=list(replies) or ["{}"])
    return RetrievalAgent(lambda: index, QueryRewriter(llm), settings), index


def test_rewrite_schema_and_anchor_words():
    rewrite = QueryRewrite(main_concept="Perda impermanente", rewritten_query="  termos   em\nlinha ", added_terms=["AMM", "blockchain", "provedores de liquidez"])
    assert rewrite.rewritten_query == "termos em linha"
    # Palavras curtas e genéricas não servem de âncora; acentos são removidos.
    assert anchor_terms(rewrite) == ["perda", "impermanente", "provedores", "liquidez"]
    with pytest.raises(ValidationError):
        QueryRewrite(main_concept="x", rewritten_query="")
    with pytest.raises(QueryRewriteError):
        QueryRewriter(FakeListChatModel(responses=["sem json"])).rewrite("pergunta")


def test_vocabulary_mismatch_is_rewritten_and_anchored_results_kept():
    retrieval, index = agent(REWRITE_JSON)
    result = retrieval.retrieve(QUESTION)
    assert result.rewrite_applied and result.rewritten_query == REWRITTEN
    # risks.md#12 veio da reescrita mas não contém nenhuma âncora: não é evidência.
    assert [d.chunk_id for d in result.documents] == ["bitcoin.md#1", "bitcoin.md#0"]
    assert result.documents[0].anchor_terms == ["halving", "recompensa", "bloco"]
    assert result.documents[0].question_distance == 0.353  # distância à pergunta original (avaliação)
    assert result.documents[0].metadata == {"source": "bitcoin.md", "title": "Bitcoin"}
    assert [d.chunk_id for d in result.original_documents][0] == "blockchain-basics.md#3"
    assert index.queries[1] == REWRITTEN and result.status == "ok"


def test_suitable_question_is_not_rewritten():
    retrieval, index = agent("não deveria ser chamado")
    result = retrieval.retrieve("Explique proof of stake")
    assert not result.rewrite_applied and result.rewritten_query is None
    assert index.queries == ["Explique proof of stake"] and "adequada" in result.decision


def test_generic_rewrite_cannot_fabricate_relevance():
    # Diagnóstico real: termos genéricos aproximam a consulta de trechos irrelevantes (0,18).
    reply = json.dumps({"main_concept": "queima", "rewritten_query": "token burn blockchain", "added_terms": ["blockchain"]})
    retrieval, _ = agent(reply)
    result = retrieval.retrieve("queima de moedas")
    assert not result.rewrite_applied and result.query_used == "queima de moedas"
    assert result.status == "no_relevant_documents" and result.documents == []
    assert result.rewritten_documents[0].score == 0.18 and result.rewritten_documents[0].anchor_terms == []


def test_rewrite_failure_falls_back_to_original():
    retrieval, _ = agent("resposta inválida")
    result = retrieval.retrieve(QUESTION)
    assert not result.rewrite_applied and "falhou" in result.decision
    assert result.status == "ok" and result.documents[0].chunk_id == "blockchain-basics.md#3"


def test_modes_never_and_always():
    never, index = agent()
    result = never.retrieve(QUESTION, mode="never")
    assert not result.rewrite_applied and index.queries == [QUESTION]
    always, index = agent(REWRITE_JSON)
    assert always.retrieve(QUESTION, mode="always").rewrite_applied


def test_tool_keeps_phase2_contract_and_adds_rewrite_metadata():
    retrieval, _ = agent(REWRITE_JSON)
    payload = json.loads(build_retrieval_tool(retrieval).run(QUESTION))
    assert payload["ok"] and payload["tool"] == "search_knowledge_base"
    assert {"source", "title", "content", "score", "chunk_id"} <= set(payload["data"][0])
    assert payload["retrieval"]["rewrite_applied"] and payload["retrieval"]["original_query"] == QUESTION
    no_docs, _ = agent(json.dumps({"main_concept": "q", "rewritten_query": "nada", "added_terms": []}))
    failed = json.loads(build_retrieval_tool(no_docs).run("queima de moedas"))
    assert not failed["ok"] and failed["error"]["kind"] == "no_relevant_documents"


def test_assets_not_in_question_are_removed_from_rewrite():
    reply = json.dumps({"main_concept": "halving", "rewritten_query": "halving redução da recompensa de bloco pela metade bitcoin Ethereum",
                        "added_terms": ["recompensa de bloco", "ethereum"]})
    index = FakeIndex({**INDEX, REWRITTEN: INDEX[REWRITTEN]}, CONTENTS)
    retrieval, index = agent(reply, index=index)
    result = retrieval.retrieve(QUESTION)
    assert result.removed_terms == ["bitcoin", "ethereum"]
    assert result.rewritten_query == "halving redução da recompensa de bloco pela metade" == index.queries[1]
    assert result.rewrite.added_terms == ["recompensa de bloco"]
    # Ativo citado na pergunta é preservado.
    kept, removed = __import__("app.retrieval.agent", fromlist=["x"]).strip_unrequested_assets(
        "Como o halving afeta o Bitcoin?", QueryRewrite(main_concept="halving", rewritten_query="halving bitcoin emissão"))
    assert kept.rewritten_query == "halving bitcoin emissão" and removed == []
