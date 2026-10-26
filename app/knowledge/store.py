"""Base de conhecimento local: ingestão do corpus, Chroma (vector store) e busca semântica.

Separada de propósito das ferramentas de mercado: o corpus tem só conceitos estáveis,
nunca preços. Embeddings são gerados localmente pelo Ollama (``nomic-embed-text``).

Chroma em modo embutido persistente (``chromadb.PersistentClient``) gravando em
``VECTOR_STORE_DIR``; sem serviço extra. Um ``manifest.json`` no mesmo diretório registra
modelo de embedding, parâmetros de chunking e hash de cada arquivo para detectar
coleção desatualizada.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
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
# Distância de cosseno (0 = idêntico): adequada a embeddings de texto.
COLLECTION_METADATA = {"hnsw:space": "cosine"}


class KnowledgeIndexError(Exception):
    """Coleção ausente, incompatível ou corrompida."""


@dataclass(frozen=True)
class KnowledgeHit:
    content: str
    source: str
    title: str
    score: float  # distância de cosseno: menor = mais relevante


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


def _client(store_dir: Path) -> chromadb.ClientAPI:
    store_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(store_dir), settings=ChromaSettings(anonymized_telemetry=False))


def _has_collection(client: chromadb.ClientAPI, name: str) -> bool:
    return any(getattr(c, "name", c) == name for c in client.list_collections())


def build_index(
    knowledge: KnowledgeSettings | None = None, embeddings: Embeddings | None = None
) -> tuple[Chroma, dict]:
    """Lê o corpus, gera embeddings e recria a coleção. Retorna a coleção e o manifesto."""
    knowledge = knowledge or get_knowledge_settings()
    embeddings = embeddings or build_embeddings(knowledge)
    corpus_dir, store_dir = project_path(knowledge.corpus_dir), project_path(knowledge.store_dir)

    chunks = split_documents(load_corpus(corpus_dir), knowledge)
    client = _client(store_dir)
    # Reindexação recria a coleção: sem trechos duplicados nem órfãos de arquivos removidos.
    if _has_collection(client, knowledge.collection):
        client.delete_collection(knowledge.collection)
    store = Chroma(
        client=client,
        collection_name=knowledge.collection,
        embedding_function=embeddings,
        collection_metadata=COLLECTION_METADATA,
    )
    ids = [f"{chunk.metadata['source']}#{i}" for i, chunk in enumerate(chunks)]
    store.add_documents(chunks, ids=ids)

    manifest = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vector_store": "chroma",
        "collection": knowledge.collection,
        "embedding_model": knowledge.embedding_model,
        "chunk_size": knowledge.chunk_size,
        "chunk_overlap": knowledge.chunk_overlap,
        "chunks": len(chunks),
        "files": _file_hashes(corpus_dir),
    }
    (store_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    logger.info("knowledge collection built chunks=%d dir=%s", len(chunks), store_dir)
    return store, manifest


def load_index(
    knowledge: KnowledgeSettings | None = None, embeddings: Embeddings | None = None
) -> Chroma:
    knowledge = knowledge or get_knowledge_settings()
    store_dir = project_path(knowledge.store_dir)
    manifest_path = store_dir / MANIFEST
    if not manifest_path.exists():
        raise KnowledgeIndexError(f"base de conhecimento não encontrada em {store_dir}; rode: POST /knowledge/ingest")
    manifest = json.loads(manifest_path.read_text())
    if manifest["embedding_model"] != knowledge.embedding_model:
        raise KnowledgeIndexError(
            f"coleção criada com {manifest['embedding_model']!r}, configuração usa "
            f"{knowledge.embedding_model!r}; reindexe com POST /knowledge/ingest"
        )
    if manifest["files"] != _file_hashes(project_path(knowledge.corpus_dir)):
        logger.warning("knowledge collection desatualizada em relação ao corpus; reindexe")
    client = _client(store_dir)
    if not _has_collection(client, knowledge.collection):
        raise KnowledgeIndexError(
            f"coleção {knowledge.collection!r} ausente em {store_dir}; rode: POST /knowledge/ingest"
        )
    return Chroma(
        client=client,
        collection_name=knowledge.collection,
        embedding_function=embeddings or build_embeddings(knowledge),
        collection_metadata=COLLECTION_METADATA,
    )


def search(store: Chroma, query: str, k: int) -> list[KnowledgeHit]:
    results = store.similarity_search_with_score(query, k=k)
    return [
        KnowledgeHit(
            content=doc.page_content,
            source=doc.metadata.get("source", "?"),
            title=doc.metadata.get("title", ""),
            score=float(score),
        )
        for doc, score in results
    ]
