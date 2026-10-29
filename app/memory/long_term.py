"""Memória de longo prazo (episódica e semântica) via Mem0, persistida no Chroma.

Mapeamento CoALA:
- **episódica**: análises concluídas e resumos da sessão, gravados SEM LLM (``infer=False``)
  com metadados (``category``, ``doc_type``, ``source_ref``);
- **semântica**: fatos duráveis extraídos pelo Mem0 (``infer=True``) só das falas do USUÁRIO
  (restrições, preferências, correções). Um filtro determinístico apaga o que contiver valor de
  mercado ou falar do assistente — no spike, o Mem0 com gemma4:e2b guardou um preço como fato;
- **procedimental**: fica no código (prompts, ferramentas, chains); nada é gravado aqui.

Isolamento: ``user_id`` fixo (não há autenticação) e ``run_id = session_id``; toda busca
filtra por ``run_id``. A interface ``MemoryBackend`` permite testes offline sem Mem0.
"""

import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.config import ConversationSettings, KnowledgeSettings, Settings, project_path
from app.schemas.analysis import MARKET_FIGURE_PATTERN

logger = logging.getLogger(__name__)

LOCAL_USER = "local"
ASSISTANT_TALK = re.compile(r"^\s*(the assistant|assistant|o assistente|a assistente|ai )", re.IGNORECASE)

EXTRACTION_PROMPT = """Extraia apenas fatos duráveis sobre o USUÁRIO que ajudem análises futuras de criptoativos:
restrições analíticas (períodos, moeda, critérios), preferências e correções que ele fez.
NUNCA registre preços, volumes, variações ou qualquer valor de mercado. Não registre falas do assistente.
Se não houver nada durável, não registre nada."""


@dataclass(frozen=True)
class MemoryHit:
    id: str
    text: str
    score: float
    metadata: dict


class MemoryBackend(Protocol):
    def add_episode(self, session_id: str, text: str, metadata: dict) -> str | None: ...
    def replace_episode(self, session_id: str, doc_type: str, text: str, metadata: dict) -> str | None: ...
    def extract_from_user(self, session_id: str, user_messages: list[str]) -> list[str]: ...
    def search(self, session_id: str, query: str, limit: int) -> list[MemoryHit]: ...
    def list(self, session_id: str) -> list[MemoryHit]: ...
    def delete_session(self, session_id: str) -> None: ...


def is_rejected_memory(text: str) -> bool:
    """Memórias que a política não aceita: valores de mercado ou falas do assistente."""
    return bool(MARKET_FIGURE_PATTERN.search(text) or ASSISTANT_TALK.search(text))


def _results(payload) -> list[dict]:
    return payload.get("results", []) if isinstance(payload, dict) else list(payload or [])


class Mem0Backend:
    def __init__(
        self,
        settings: Settings,
        conversation: ConversationSettings,
        knowledge: KnowledgeSettings,
    ) -> None:
        os.environ.setdefault("MEM0_TELEMETRY", "False")
        from mem0 import Memory  # import tardio: só quem usa o Mem0 paga o custo

        config = {
            "llm": {"provider": "ollama", "config": {
                "model": settings.ollama_model, "ollama_base_url": settings.ollama_base_url, "temperature": 0,
            }},
            "embedder": {"provider": "ollama", "config": {
                "model": knowledge.embedding_model, "ollama_base_url": settings.ollama_base_url,
                "embedding_dims": conversation.embedding_dims,
            }},
            "vector_store": {"provider": "chroma", "config": {
                "collection_name": conversation.memory_collection,
                "path": str(project_path(conversation.memory_store_dir)),
            }},
            "history_db_path": str(project_path(conversation.memory_history_db)),
        }
        self.memory = Memory.from_config(config)
        self.min_score = conversation.memory_min_score
        self._lock = threading.Lock()

    def add_episode(self, session_id: str, text: str, metadata: dict) -> str | None:
        meta = {**metadata, "created_at": datetime.now(UTC).isoformat(timespec="seconds")}
        with self._lock:
            out = _results(self.memory.add(
                [{"role": "user", "content": text}], user_id=LOCAL_USER, run_id=session_id, metadata=meta, infer=False,
            ))
        return out[0].get("id") if out else None

    def replace_episode(self, session_id: str, doc_type: str, text: str, metadata: dict) -> str | None:
        """Mantém um único episódio daquele tipo na sessão (ex.: só o resumo mais recente)."""
        for hit in self.list(session_id):
            if hit.metadata.get("doc_type") == doc_type:
                with self._lock:
                    self.memory.delete(hit.id)
        return self.add_episode(session_id, text, {**metadata, "doc_type": doc_type})

    def extract_from_user(self, session_id: str, user_messages: list[str]) -> list[str]:
        messages = [{"role": "user", "content": m} for m in user_messages if m.strip()]
        if not messages:
            return []
        with self._lock:
            events = _results(self.memory.add(
                messages, user_id=LOCAL_USER, run_id=session_id, infer=True, prompt=EXTRACTION_PROMPT,
                metadata={"category": "semantic", "doc_type": "user_memory",
                          "created_at": datetime.now(UTC).isoformat(timespec="seconds")},
            ))
            kept = []
            for event in events:
                if event.get("event") not in {"ADD", "UPDATE"}:
                    continue
                text = event.get("memory", "")
                if is_rejected_memory(text):
                    logger.info("memory rejected by policy session=%s text=%r", session_id, text[:80])
                    self.memory.delete(event["id"])
                else:
                    kept.append(text)
        return kept

    def search(self, session_id: str, query: str, limit: int) -> list[MemoryHit]:
        hits = _results(self.memory.search(
            query, filters={"user_id": LOCAL_USER, "run_id": session_id}, top_k=limit, threshold=self.min_score,
        ))
        return [
            MemoryHit(h["id"], h["memory"], float(h.get("score") or 0.0), h.get("metadata") or {})
            for h in hits
            if h.get("run_id") in (None, session_id)  # defesa extra contra vazamento entre sessões
        ]

    def list(self, session_id: str) -> list[MemoryHit]:
        items = _results(self.memory.get_all(filters={"user_id": LOCAL_USER, "run_id": session_id}))
        return [MemoryHit(h["id"], h["memory"], 1.0, h.get("metadata") or {}) for h in items]

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self.memory.delete_all(user_id=LOCAL_USER, run_id=session_id)


class InMemoryBackend:
    """Implementação offline (testes): mesma interface e mesma política de retenção."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str, dict]] = {}
        self._counter = 0
        self.extracted_inputs: list[list[str]] = []

    def _new_id(self) -> str:
        self._counter += 1
        return f"mem{self._counter}"

    def add_episode(self, session_id: str, text: str, metadata: dict) -> str | None:
        memory_id = self._new_id()
        self._items[memory_id] = (session_id, text, metadata)
        return memory_id

    def replace_episode(self, session_id: str, doc_type: str, text: str, metadata: dict) -> str | None:
        self._items = {i: v for i, v in self._items.items() if not (v[0] == session_id and v[2].get("doc_type") == doc_type)}
        return self.add_episode(session_id, text, {**metadata, "doc_type": doc_type})

    def extract_from_user(self, session_id: str, user_messages: list[str]) -> list[str]:
        self.extracted_inputs.append(user_messages)
        kept = [m for m in user_messages if not is_rejected_memory(m)]
        for text in kept:
            self._items[self._new_id()] = (session_id, text, {"category": "semantic", "doc_type": "user_memory"})
        return kept

    def search(self, session_id: str, query: str, limit: int) -> list[MemoryHit]:
        words = set(re.findall(r"\w+", query.lower()))
        scored = []
        for memory_id, (sid, text, meta) in self._items.items():
            if sid != session_id:
                continue
            overlap = len(words & set(re.findall(r"\w+", text.lower())))
            if overlap:
                scored.append(MemoryHit(memory_id, text, overlap / max(len(words), 1), meta))
        return sorted(scored, key=lambda h: h.score, reverse=True)[:limit]

    def list(self, session_id: str) -> list[MemoryHit]:
        return [MemoryHit(i, t, 1.0, m) for i, (s, t, m) in self._items.items() if s == session_id]

    def delete_session(self, session_id: str) -> None:
        self._items = {i: v for i, v in self._items.items() if v[0] != session_id}


class LazyMemoryBackend:
    """Cria o backend real no primeiro uso: a API sobe mesmo com o Ollama fora do ar.

    Se a criação falhar, a exceção chega a quem chamou (o serviço trata como memória
    indisponível no turno) e a próxima chamada tenta de novo.
    """

    def __init__(self, factory) -> None:
        self._factory = factory
        self._backend: MemoryBackend | None = None
        self._lock = threading.Lock()

    def _get(self) -> MemoryBackend:
        with self._lock:
            if self._backend is None:
                self._backend = self._factory()
            return self._backend

    def add_episode(self, session_id: str, text: str, metadata: dict) -> str | None:
        return self._get().add_episode(session_id, text, metadata)

    def replace_episode(self, session_id: str, doc_type: str, text: str, metadata: dict) -> str | None:
        return self._get().replace_episode(session_id, doc_type, text, metadata)

    def extract_from_user(self, session_id: str, user_messages: list[str]) -> list[str]:
        return self._get().extract_from_user(session_id, user_messages)

    def search(self, session_id: str, query: str, limit: int) -> list[MemoryHit]:
        return self._get().search(session_id, query, limit)

    def list(self, session_id: str) -> list[MemoryHit]:
        return self._get().list(session_id)

    def delete_session(self, session_id: str) -> None:
        self._get().delete_session(session_id)
