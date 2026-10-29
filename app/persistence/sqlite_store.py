"""Persistência relacional da conversa (SQLite): fonte da verdade da Fase 3.

Guarda sessões, mensagens ordenadas, resumos, análises anteriores e o contexto ativo
da sessão. O Chroma (memória semântica) só guarda cópias indexadas com referência
estável para estes registros (``analysis:<id>``, ``summary:<session_id>``).

Concorrência:
- uma conexão por operação (seguro entre threads do FastAPI);
- WAL + ``busy_timeout`` para leituras concorrentes com escrita;
- gravações em ``BEGIN IMMEDIATE``: a sequência das mensagens é alocada dentro da
  transação, então requisições simultâneas na mesma sessão nunca colidem;
- resumo com versão otimista: uma atualização baseada em versão antiga é rejeitada.
"""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

SCHEMA_VERSION = 1

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        next_seq INTEGER NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
        active_context TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE (session_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_summaries (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        summary TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}',
        covers_until_seq INTEGER NOT NULL,
        version INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analyses (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        message_seq INTEGER NOT NULL,
        intent TEXT NOT NULL,
        request TEXT NOT NULL,
        report TEXT NOT NULL,
        data_retrieved_at TEXT,
        created_at TEXT NOT NULL,
        indexed INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_analyses_session ON analyses (session_id, created_at)",
]

Role = Literal["user", "assistant"]


class SessionNotFoundError(Exception):
    pass


class StaleSummaryError(Exception):
    """Tentativa de gravar resumo a partir de uma versão desatualizada."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Session:
    id: str
    title: str | None
    created_at: datetime
    last_activity_at: datetime
    message_count: int
    active_context: dict


@dataclass(frozen=True)
class StoredMessage:
    session_id: str
    seq: int
    role: Role
    content: str
    metadata: dict
    created_at: datetime


@dataclass(frozen=True)
class StoredSummary:
    session_id: str
    summary: str
    details: dict
    covers_until_seq: int
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class StoredAnalysis:
    id: str
    session_id: str
    message_seq: int
    intent: str
    request: dict
    report: dict
    data_retrieved_at: datetime | None
    created_at: datetime
    indexed: bool


class ConversationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # ------------------------------------------------------------------ infraestrutura

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                conn.execute("BEGIN IMMEDIATE")
                for statement in SCHEMA:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
        finally:
            conn.close()

    # ------------------------------------------------------------------ sessões

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
            message_count=row["next_seq"] - 1,
            active_context=json.loads(row["active_context"]),
        )

    def create_session(self, title: str | None = None) -> Session:
        now = _iso(utcnow())
        session_id = uuid.uuid4().hex
        with self._write() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, last_activity_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session(row)

    def get_session(self, session_id: str) -> Session | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session(row) if row else None

    def require_session(self, session_id: str) -> Session:
        session = self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def set_active_context(self, session_id: str, context: dict) -> None:
        with self._write() as conn:
            updated = conn.execute(
                "UPDATE sessions SET active_context = ?, last_activity_at = ? WHERE id = ?",
                (json.dumps(context, ensure_ascii=False, default=str), _iso(utcnow()), session_id),
            ).rowcount
        if not updated:
            raise SessionNotFoundError(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._write() as conn:
            return conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    # ------------------------------------------------------------------ mensagens

    @staticmethod
    def _message(row: sqlite3.Row) -> StoredMessage:
        return StoredMessage(
            session_id=row["session_id"],
            seq=row["seq"],
            role=row["role"],
            content=row["content"],
            metadata=json.loads(row["metadata"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def append_message(self, session_id: str, role: Role, content: str, metadata: dict | None = None) -> StoredMessage:
        now = _iso(utcnow())
        with self._write() as conn:
            row = conn.execute("SELECT next_seq FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise SessionNotFoundError(session_id)
            seq = row["next_seq"]
            conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, seq, role, content, json.dumps(metadata or {}, ensure_ascii=False, default=str), now),
            )
            conn.execute(
                "UPDATE sessions SET next_seq = ?, last_activity_at = ? WHERE id = ?", (seq + 1, now, session_id)
            )
            stored = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND seq = ?", (session_id, seq)
            ).fetchone()
        return self._message(stored)

    def list_messages(
        self, session_id: str, after_seq: int = 0, before_seq: int | None = None, last: int | None = None
    ) -> list[StoredMessage]:
        """Mensagens em ordem crescente de ``seq``. ``last=N`` devolve só as N mais recentes."""
        query = "SELECT * FROM messages WHERE session_id = ? AND seq > ?"
        params: list = [session_id, after_seq]
        if before_seq is not None:
            query += " AND seq < ?"
            params.append(before_seq)
        if last is not None:
            query = f"SELECT * FROM ({query} ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC"
            params.append(last)
        else:
            query += " ORDER BY seq ASC"
        with self._read() as conn:
            return [self._message(r) for r in conn.execute(query, params).fetchall()]

    def update_message_metadata(self, session_id: str, seq: int, metadata: dict) -> None:
        """Mescla metadados (ex.: intenção e entidades resolvidas) numa mensagem já gravada."""
        with self._write() as conn:
            row = conn.execute(
                "SELECT metadata FROM messages WHERE session_id = ? AND seq = ?", (session_id, seq)
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(f"{session_id}:{seq}")
            merged = {**json.loads(row["metadata"]), **metadata}
            conn.execute(
                "UPDATE messages SET metadata = ? WHERE session_id = ? AND seq = ?",
                (json.dumps(merged, ensure_ascii=False, default=str), session_id, seq),
            )

    # ------------------------------------------------------------------ resumos

    def get_summary(self, session_id: str) -> StoredSummary | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM session_summaries WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return StoredSummary(
            session_id=row["session_id"],
            summary=row["summary"],
            details=json.loads(row["details"]),
            covers_until_seq=row["covers_until_seq"],
            version=row["version"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def save_summary(
        self, session_id: str, summary: str, details: dict, covers_until_seq: int, expected_version: int
    ) -> StoredSummary:
        """Grava a versão ``expected_version + 1``; falha se outra atualização chegou antes."""
        now = _iso(utcnow())
        payload = json.dumps(details, ensure_ascii=False, default=str)
        with self._write() as conn:
            current = conn.execute(
                "SELECT version FROM session_summaries WHERE session_id = ?", (session_id,)
            ).fetchone()
            current_version = current["version"] if current else 0
            if current_version != expected_version:
                raise StaleSummaryError(f"versão atual {current_version}, esperada {expected_version}")
            conn.execute(
                """
                INSERT INTO session_summaries (session_id, summary, details, covers_until_seq, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary, details = excluded.details,
                    covers_until_seq = excluded.covers_until_seq, version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (session_id, summary, payload, covers_until_seq, expected_version + 1, now),
            )
        stored = self.get_summary(session_id)
        assert stored is not None
        return stored

    # ------------------------------------------------------------------ análises

    @staticmethod
    def _analysis(row: sqlite3.Row) -> StoredAnalysis:
        return StoredAnalysis(
            id=row["id"],
            session_id=row["session_id"],
            message_seq=row["message_seq"],
            intent=row["intent"],
            request=json.loads(row["request"]),
            report=json.loads(row["report"]),
            data_retrieved_at=datetime.fromisoformat(row["data_retrieved_at"]) if row["data_retrieved_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            indexed=bool(row["indexed"]),
        )

    def save_analysis(
        self,
        session_id: str,
        message_seq: int,
        intent: str,
        request: dict,
        report: dict,
        data_retrieved_at: datetime | None,
    ) -> StoredAnalysis:
        analysis_id = uuid.uuid4().hex[:12]
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO analyses (id, session_id, message_seq, intent, request, report, data_retrieved_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id, session_id, message_seq, intent,
                    json.dumps(request, ensure_ascii=False, default=str),
                    json.dumps(report, ensure_ascii=False, default=str),
                    _iso(data_retrieved_at) if data_retrieved_at else None,
                    _iso(utcnow()),
                ),
            )
            row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        return self._analysis(row)

    def list_analyses(self, session_id: str) -> list[StoredAnalysis]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM analyses WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
            ).fetchall()
        return [self._analysis(r) for r in rows]

    def get_analysis(self, session_id: str, analysis_id: str) -> StoredAnalysis | None:
        """Sempre filtrada pela sessão: uma sessão não lê análise de outra."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE id = ? AND session_id = ?", (analysis_id, session_id)
            ).fetchone()
        return self._analysis(row) if row else None

    def mark_indexed(self, analysis_id: str, indexed: bool = True) -> None:
        with self._write() as conn:
            conn.execute("UPDATE analyses SET indexed = ? WHERE id = ?", (int(indexed), analysis_id))

    def pending_index(self, session_id: str) -> list[StoredAnalysis]:
        return [a for a in self.list_analyses(session_id) if not a.indexed]
