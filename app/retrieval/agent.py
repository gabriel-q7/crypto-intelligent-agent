"""Retrieval Agent: busca na coleção Chroma existente (``crypto_knowledge``) com Query Rewriting.

Reusa a coleção, o modelo de embedding e o ``KnowledgeSearcher`` da Fase 2 — não reindexa,
não recria nem apaga a coleção, e não toca na coleção de memória do Mem0.

Política (modo ``auto``):
1. busca com a pergunta original;
2. se o melhor trecho estiver próximo o bastante (distância de cosseno
   ``<= rewrite_trigger_distance``), a pergunta já serve: devolve sem reescrever (sem custo de LLM);
3. senão, o LLM reescreve a pergunta (``QueryRewriter``) e a busca é refeita com a consulta
   reescrita, cujo ranking passa a valer;
4. um trecho só conta como evidência se:
   - a distância à consulta que o trouxe for ``<= relevance_max_distance``; e
   - quando veio da reescrita, contiver ao menos uma **palavra âncora** da reescrita (palavra
     distintiva de ``main_concept`` ou ``added_terms``, com 5+ letras, fora da lista genérica,
     casando como prefixo de uma palavra do trecho — "chave" casa com "chaves"). Sem isso, termos genéricos acrescentados pelo LLM
     ("blockchain", "tokens") aproximam a consulta de qualquer trecho genérico e fabricam
     relevância — comportamento observado no diagnóstico real (docs/phase-4-evidence.md);
5. ativos que a pergunta não cita são removidos da reescrita (``strip_unrequested_assets``): o LLM
   não pode mudar o assunto;
6. se a reescrita não trouxer nenhum trecho válido, fica a busca original;
7. sem nenhum trecho válido → ``no_relevant_documents``: o agente deve dizer que a base não
   cobre o assunto, sem inventar evidência.

Cada trecho também traz ``question_distance`` (distância à pergunta original), só para
avaliação: no diagnóstico ela se mostrou inadequada como critério de relevância quando o
usuário usa outro vocabulário (o trecho certo ficava mais longe da pergunta que trechos errados).

Modos ``always`` (reescreve mesmo quando a original é boa) e ``never`` existem para avaliação
(comparação antes/depois).
"""

import logging
import re
import unicodedata
from collections.abc import Callable
from typing import Literal

from langchain_core.vectorstores import VectorStore
from pydantic import BaseModel, Field

from app.config import RetrievalSettings
from app.intent.classifier import ASSET_ALIASES, find_assets
from app.retrieval.rewriter import QueryRewrite, QueryRewriteError, QueryRewriter

logger = logging.getLogger(__name__)

RewriteMode = Literal["auto", "always", "never"]
# Profundidade da busca com a pergunta original usada para medir a distância pergunta↔trecho
# dos trechos trazidos pela reescrita (o corpus atual tem 18 trechos).
QUESTION_SCAN_K = 50
MIN_ANCHOR_LENGTH = 5
# Palavras que aparecem em quase todo o corpus ou não identificam assunto: não servem de âncora.
GENERIC_TERMS = frozenset({
    "blockchain", "blockchains", "cripto", "crypto", "criptoativo", "criptoativos", "criptomoeda",
    "criptomoedas", "cryptocurrency", "cryptocurrencies", "mercado", "market", "token", "tokens",
    "moeda", "moedas", "ativo", "ativos", "network", "mecanismo", "mechanism", "funciona",
    "conceito", "sobre", "entre", "valor", "value", "preco", "price", "usuario", "usuarios",
})
WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode().split())


class RetrievedDocument(BaseModel):
    chunk_id: str | None
    source: str
    title: str
    score: float = Field(description="Distância de cosseno à consulta usada na busca (0 = idêntico).")
    question_distance: float | None = Field(
        default=None, description="Distância à pergunta original do usuário (só avaliação)."
    )
    anchor_terms: list[str] = Field(default=[], description="Termos âncora da reescrita presentes no trecho.")
    rank: int
    content: str
    metadata: dict = Field(default={}, description="Metadados originais do trecho no Chroma.")


class RetrievalResult(BaseModel):
    status: Literal["ok", "no_relevant_documents"]
    original_query: str
    rewritten_query: str | None = None
    rewrite: QueryRewrite | None = None
    rewrite_applied: bool = Field(description="True quando os documentos devolvidos vieram da consulta reescrita.")
    removed_terms: list[str] = Field(default=[], description="Ativos acrescentados pelo LLM e removidos (a pergunta não os cita).")
    decision: str = Field(description="Por que reescreveu (ou não) e qual busca foi usada.")
    query_used: str
    rewrite_trigger_distance: float
    relevance_max_distance: float
    documents: list[RetrievedDocument] = Field(description="Evidência: só trechos que passaram nos critérios.")
    original_documents: list[RetrievedDocument] = Field(default=[], description="Top-k da pergunta original (avaliação).")
    rewritten_documents: list[RetrievedDocument] = Field(default=[], description="Top-k da consulta reescrita (avaliação).")


def _best(docs: list[RetrievedDocument]) -> float:
    return min((d.score for d in docs), default=float("inf"))


def strip_unrequested_assets(question: str, rewrite: QueryRewrite) -> tuple[QueryRewrite, list[str]]:
    """Remove da reescrita ativos que a pergunta não cita (o LLM não pode mudar o assunto).

    Observado na regressão real: "recompensa dos mineradores cai pela metade" virou
    "... bitcoin ethereum", e o trecho de Ethereum passou à frente do trecho sobre halving.
    """
    asked = set(find_assets(question))
    removed: list[str] = []

    def clean(text: str) -> str:
        for alias, symbol in ASSET_ALIASES.items():
            if symbol in asked:
                continue
            pattern = re.compile(rf"(?<![\w]){re.escape(alias)}(?![\w])", re.IGNORECASE)
            if pattern.search(text):
                removed.append(alias)
                text = pattern.sub(" ", text)
        return " ".join(text.split())

    query = clean(rewrite.rewritten_query)
    terms = [t for t in (clean(x) for x in rewrite.added_terms) if t]
    concept = clean(rewrite.main_concept) or rewrite.main_concept
    if len(query) < 3:  # sobrou nada útil: mantém a original da reescrita
        return rewrite, []
    cleaned = rewrite.model_copy(update={"rewritten_query": query, "added_terms": terms, "main_concept": concept})
    return cleaned, sorted(set(removed))


def anchor_terms(rewrite: QueryRewrite) -> list[str]:
    """Palavras distintivas do conceito e dos termos acrescentados pela reescrita."""
    words = WORD.findall(normalize(" ".join([rewrite.main_concept, *rewrite.added_terms])))
    return list(dict.fromkeys(w for w in words if len(w) >= MIN_ANCHOR_LENGTH and w not in GENERIC_TERMS))


class RetrievalAgent:
    def __init__(
        self,
        index: Callable[[], VectorStore],
        rewriter: QueryRewriter,
        settings: RetrievalSettings,
    ) -> None:
        self.index = index
        self.rewriter = rewriter
        self.settings = settings

    def _search(self, query: str, k: int) -> list[RetrievedDocument]:
        results = self.index().similarity_search_with_score(query, k=k)
        return [
            RetrievedDocument(
                chunk_id=getattr(doc, "id", None),
                source=doc.metadata.get("source", "?"),
                title=doc.metadata.get("title", ""),
                score=round(float(score), 4),
                rank=i,
                content=doc.page_content,
                metadata=dict(doc.metadata),
            )
            for i, (doc, score) in enumerate(results, start=1)
        ]

    def _valid(self, docs: list[RetrievedDocument], anchored: bool) -> list[RetrievedDocument]:
        limit = self.settings.relevance_max_distance
        return [d for d in docs if d.score <= limit and (not anchored or d.anchor_terms)]

    def retrieve(self, question: str, mode: RewriteMode | None = None, k: int | None = None) -> RetrievalResult:
        mode = mode or self.settings.rewrite_mode  # type: ignore[assignment]
        k = k or self.settings.top_k
        question = question.strip()
        # Uma busca larga com a pergunta original dá o top-k original e a distância
        # pergunta↔trecho de tudo o que a reescrita puder trazer (para avaliação).
        scan = self._search(question, max(k, QUESTION_SCAN_K))
        question_distance = {d.chunk_id: d.score for d in scan}
        original = [d.model_copy(update={"question_distance": d.score}) for d in scan[:k]]
        common = {
            "original_query": question,
            "rewrite_trigger_distance": self.settings.rewrite_trigger_distance,
            "relevance_max_distance": self.settings.relevance_max_distance,
            "original_documents": original,
        }
        best_original = _best(original)

        if mode == "never" or (mode == "auto" and best_original <= self.settings.rewrite_trigger_distance):
            reason = (
                "reescrita desligada (modo never)" if mode == "never"
                else f"pergunta original adequada: melhor distância {best_original:.3f} <= {self.settings.rewrite_trigger_distance}"
            )
            return self._finish(original, False, question, None, False, reason, common)

        try:
            rewrite = self.rewriter.rewrite(question)
        except QueryRewriteError as exc:
            logger.warning("query rewrite failed: %s", exc)
            return self._finish(original, False, question, None, False, f"reescrita falhou ({exc}); usada a original", common)

        rewrite, removed = strip_unrequested_assets(question, rewrite)
        common["removed_terms"] = removed
        anchors = anchor_terms(rewrite)
        rewritten = []
        for doc in self._search(rewrite.rewritten_query, k):
            content_words = set(WORD.findall(normalize(doc.content)))
            rewritten.append(doc.model_copy(update={
                "question_distance": question_distance.get(doc.chunk_id),
                # Prefixo: "chave" casa com "chaves", "privada" com "privadas".
                "anchor_terms": [t for t in anchors if any(w.startswith(t) for w in content_words)],
            }))
        common["rewritten_documents"] = rewritten
        valid_rewritten = self._valid(rewritten, anchored=True)
        reason = (
            f"melhor distância original {best_original:.3f} > {self.settings.rewrite_trigger_distance}; "
            f"reescrita: {len(valid_rewritten)} trecho(s) válido(s) (âncoras {anchors}); "
            + ("usada a consulta reescrita" if valid_rewritten else "nenhum trecho da reescrita contém os termos âncora: usada a original")
        )
        if valid_rewritten:
            return self._finish(rewritten, True, rewrite.rewritten_query, rewrite, True, reason, common)
        return self._finish(original, False, question, rewrite, False, reason, common)

    def _finish(
        self,
        docs: list[RetrievedDocument],
        anchored: bool,
        query_used: str,
        rewrite: QueryRewrite | None,
        applied: bool,
        decision: str,
        common: dict,
    ) -> RetrievalResult:
        valid = self._valid(docs, anchored)
        status = "ok" if valid else "no_relevant_documents"
        if not valid:
            decision += f"; nenhum trecho a até {self.settings.relevance_max_distance} da consulta"
        logger.info("retrieval status=%s rewrite_applied=%s best=%.3f", status, applied, _best(docs))
        return RetrievalResult(
            status=status,
            rewritten_query=rewrite.rewritten_query if rewrite else None,
            rewrite=rewrite,
            rewrite_applied=applied,
            decision=decision,
            query_used=query_used,
            documents=valid,
            **common,
        )
