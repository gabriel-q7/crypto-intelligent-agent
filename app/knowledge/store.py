"""Base de conhecimento local: ingestão do corpus, índice FAISS e busca semântica.

Separada de propósito das ferramentas de mercado: o corpus tem só conceitos estáveis,
nunca preços. Embeddings são gerados localmente pelo Ollama (``nomic-embed-text``).

O índice é salvo em ``FAISS_INDEX_DIR`` com um ``manifest.json`` (modelo de embedding,
parâmetros de chunking e hash de cada arquivo) para detectar índice desatualizado.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import (
    KnowledgeSettings,
    Settings,
    get_knowledge_settings,
    get_settings,
    project_path,
)

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
EXCLUDED_FILES = {"README.md"}


class KnowledgeIndexError(Exception):
    """Índice ausente, incompatível ou corrompido."""


@dataclass(frozen=True)
class KnowledgeHit:
    content: str
    source: str
    title: str
    score: float


def build_embeddings(
    knowledge: KnowledgeSettings | None = None, settings: Settings | None = None
) -> Embeddings:
    knowledge = knowledge or get_knowledge_settings()
    settings = settings or get_settings()
    return OllamaEmbeddings(model=knowledge.embedding_model, base_url=settings.ollama_base_url)


def load_corpus(corpus_dir: Path) -> list[Document]:
    documents = []
    for path in sorted(corpus_dir.glob("*.md")):
        if path.name in EXCLUDED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), path.stem)
        documents.append(Document(page_content=text, metadata={"source": path.name, "title": title}))
    if not documents:
        raise KnowledgeIndexError(f"nenhum documento .md em {corpus_dir}")
    return documents


def split_documents(documents: list[Document], knowledge: KnowledgeSettings) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=knowledge.chunk_size,
        chunk_overlap=knowledge.chunk_overlap,
        separators=["\n## ", "\n\n", "\n", ". ", " "],
    )
    return splitter.split_documents(documents)


def _file_hashes(corpus_dir: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(corpus_dir.glob("*.md"))
        if p.name not in EXCLUDED_FILES
    }


def build_index(
    knowledge: KnowledgeSettings | None = None, embeddings: Embeddings | None = None
) -> tuple[FAISS, dict]:
    """Lê o corpus, gera embeddings, cria e salva o índice. Retorna o índice e o manifesto."""
    knowledge = knowledge or get_knowledge_settings()
    embeddings = embeddings or build_embeddings(knowledge)
    corpus_dir, index_dir = project_path(knowledge.corpus_dir), project_path(knowledge.index_dir)

    chunks = split_documents(load_corpus(corpus_dir), knowledge)
    index = FAISS.from_documents(chunks, embeddings)
    index_dir.mkdir(parents=True, exist_ok=True)
    index.save_local(str(index_dir))

    manifest = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedding_model": knowledge.embedding_model,
        "chunk_size": knowledge.chunk_size,
        "chunk_overlap": knowledge.chunk_overlap,
        "chunks": len(chunks),
        "files": _file_hashes(corpus_dir),
    }
    (index_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    logger.info("knowledge index built chunks=%d dir=%s", len(chunks), index_dir)
    return index, manifest


def load_index(
    knowledge: KnowledgeSettings | None = None, embeddings: Embeddings | None = None
) -> FAISS:
    knowledge = knowledge or get_knowledge_settings()
    index_dir = project_path(knowledge.index_dir)
    manifest_path = index_dir / MANIFEST
    if not manifest_path.exists():
        raise KnowledgeIndexError(
            f"índice não encontrado em {index_dir}; rode: POST /knowledge/ingest"
        )
    manifest = json.loads(manifest_path.read_text())
    if manifest["embedding_model"] != knowledge.embedding_model:
        raise KnowledgeIndexError(
            f"índice criado com {manifest['embedding_model']!r}, configuração usa "
            f"{knowledge.embedding_model!r}; reindexe com POST /knowledge/ingest"
        )
    if manifest["files"] != _file_hashes(project_path(knowledge.corpus_dir)):
        logger.warning("knowledge index desatualizado em relação ao corpus; reindexe")
    # O docstore do FAISS é serializado com pickle: carregar só índices gerados localmente.
    return FAISS.load_local(
        str(index_dir),
        embeddings or build_embeddings(knowledge),
        allow_dangerous_deserialization=True,
    )


def search(index: FAISS, query: str, k: int) -> list[KnowledgeHit]:
    results = index.similarity_search_with_score(query, k=k)
    return [
        KnowledgeHit(
            content=doc.page_content,
            source=doc.metadata.get("source", "?"),
            title=doc.metadata.get("title", ""),
            score=float(score),
        )
        for doc, score in results
    ]
