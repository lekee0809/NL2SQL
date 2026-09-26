"""Local durable storage for conversation state, independent of query execution."""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .conversation import ConversationState, SessionConflictError
from .query_spec import QuerySpec


class SQLiteConversationStore:
    """Same public operations as ConversationStore, backed by one local SQLite file."""

    def __init__(
        self,
        path: str | Path,
        ttl_seconds: int = 3600,
        clock: Callable[[], datetime] | None = None,
        max_sessions: int = 1000,
    ):
        if ttl_seconds < 1:
            raise ValueError("会话过期时间必须大于 0 秒")
        if max_sessions < 1:
            raise ValueError("最大会话数必须大于 0")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS conversation_sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_sessions_updated_at "
                         "ON conversation_sessions(updated_at)")
            self._purge(conn, self._now())
            self._trim(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA secure_delete = ON")
        return conn

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _purge(self, conn: sqlite3.Connection, now: datetime) -> int:
        cursor = conn.execute("DELETE FROM conversation_sessions WHERE updated_at <= ?",
                              (now.timestamp() - self._ttl_seconds,))
        return cursor.rowcount

    def _trim(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM conversation_sessions WHERE session_id IN ("
            "SELECT session_id FROM conversation_sessions "
            "ORDER BY updated_at DESC, session_id DESC LIMIT -1 OFFSET ?)",
            (self._max_sessions,),
        )

    def purge_expired(self) -> int:
        with self._lock, self._connect() as conn:
            return self._purge(conn, self._now())

    def create(
        self,
        spec: QuerySpec,
        message: str,
        pending_clarification: dict | None = None,
    ) -> ConversationState:
        now = self._now()
        state = ConversationState(
            session_id=uuid4().hex, query_spec=spec, last_message=message,
            first_message=message,
            turn_count=1, pending_clarification=pending_clarification, updated_at=now,
        )
        with self._lock, self._connect() as conn:
            self._purge(conn, now)
            conn.execute("INSERT INTO conversation_sessions VALUES (?, ?, ?)",
                         (state.session_id, state.model_dump_json(), now.timestamp()))
            self._trim(conn)
        return state.model_copy(deep=True)

    def get(self, session_id: str) -> ConversationState:
        with self._lock, self._connect() as conn:
            self._purge(conn, self._now())
            row = conn.execute("SELECT state_json FROM conversation_sessions WHERE session_id = ?",
                               (session_id,)).fetchone()
        if row is None:
            raise KeyError("会话不存在或已过期")
        return ConversationState.model_validate_json(row[0])

    def update(
        self,
        session_id: str,
        spec: QuerySpec,
        message: str,
        pending_clarification: dict | None = None,
        expected_turn_count: int | None = None,
    ) -> ConversationState:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge(conn, now)
            row = conn.execute("SELECT state_json FROM conversation_sessions WHERE session_id = ?",
                               (session_id,)).fetchone()
            if row is None:
                raise KeyError("会话不存在或已过期")
            current = ConversationState.model_validate_json(row[0])
            if expected_turn_count is not None and current.turn_count != expected_turn_count:
                raise SessionConflictError("会话已在其他页面更新，请刷新后重试")
            updated = ConversationState(
                session_id=session_id, query_spec=spec, last_message=message,
                first_message=current.first_message or current.last_message,
                turn_count=current.turn_count + 1,
                pending_clarification=pending_clarification, updated_at=now,
            )
            conn.execute("UPDATE conversation_sessions SET state_json = ?, updated_at = ? "
                         "WHERE session_id = ?",
                         (updated.model_dump_json(), now.timestamp(), session_id))
        return updated.model_copy(deep=True)

    def delete(self, session_id: str) -> bool:
        with self._lock, self._connect() as conn:
            self._purge(conn, self._now())
            return conn.execute("DELETE FROM conversation_sessions WHERE session_id = ?",
                                (session_id,)).rowcount > 0

    def list_recent(self, limit: int = 20) -> list[ConversationState]:
        with self._lock, self._connect() as conn:
            self._purge(conn, self._now())
            rows = conn.execute(
                "SELECT state_json FROM conversation_sessions "
                "ORDER BY updated_at DESC, session_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [ConversationState.model_validate_json(row[0]) for row in rows]
