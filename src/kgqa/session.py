from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from kgqa.models import ChatSessionPayload, ChatSessionRecord, ChatSessionSummary

_DB_CONN: sqlite3.Connection | None = None
_DB_LOCK = threading.RLock()


def _derive_title(messages: list[dict[str, Any]], fallback: str = "新会话") -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            text = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        else:
            text = str(content)
        text = text.strip()
        if text:
            return text[:36]
    return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str) -> Any:
    return json.loads(value)


def _get_db() -> sqlite3.Connection:
    global _DB_CONN
    if _DB_CONN is not None:
        return _DB_CONN

    with _DB_LOCK:
        if _DB_CONN is not None:
            return _DB_CONN

        from kgqa.config import get_settings

        db_path = get_settings().session_db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(conn)
        _DB_CONN = conn
        return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id     TEXT PRIMARY KEY,
            title          TEXT NOT NULL,
            scenario_id    TEXT NOT NULL,
            scenario_label TEXT NOT NULL,
            dataset_name   TEXT NOT NULL,
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL,
            messages_json  TEXT NOT NULL DEFAULT '[]',
            state_json     TEXT NOT NULL DEFAULT '{}',
            status         TEXT NOT NULL DEFAULT 'idle'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
        ON sessions(updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_scenario_id
        ON sessions(scenario_id)
        """
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> ChatSessionRecord:
    return ChatSessionRecord(
        session_id=str(row["session_id"]),
        title=str(row["title"]),
        scenario_id=str(row["scenario_id"]),
        scenario_label=str(row["scenario_label"]),
        dataset_name=str(row["dataset_name"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        messages=_json_loads(str(row["messages_json"])),
        state=_json_loads(str(row["state_json"])),
        status=str(row["status"]),
    )


def upsert_session(
    session_id: str,
    scenario_id: str,
    scenario_label: str,
    dataset_name: str,
    messages: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    status: str | None = None,
) -> ChatSessionRecord:
    now = time.time()

    # 注意：agent.py 在传入 state 前会先通过 _public_state() 剥离
    # _latest_rows 等内部字段。这里按“收到什么就持久化什么”处理，
    # 不再对 state 做二次裁剪。
    with _DB_LOCK:
        conn = _get_db()
        existing_row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if existing_row is None:
            record = ChatSessionRecord(
                session_id=session_id,
                title=_derive_title(messages or []),
                scenario_id=scenario_id,
                scenario_label=scenario_label,
                dataset_name=dataset_name,
                created_at=now,
                updated_at=now,
                messages=messages or [],
                state=state or {},
                status=status or "idle",
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    title,
                    scenario_id,
                    scenario_label,
                    dataset_name,
                    created_at,
                    updated_at,
                    messages_json,
                    state_json,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.title,
                    record.scenario_id,
                    record.scenario_label,
                    record.dataset_name,
                    record.created_at,
                    record.updated_at,
                    _json_dumps(record.messages),
                    _json_dumps(record.state),
                    record.status,
                ),
            )
            conn.commit()
            return record

        existing = _row_to_record(existing_row)
        updated = existing.model_copy(deep=True)
        updated.scenario_id = scenario_id
        updated.scenario_label = scenario_label
        updated.dataset_name = dataset_name
        if messages is not None:
            updated.messages = messages
            updated.title = _derive_title(messages, fallback=existing.title)
        if state is not None:
            updated.state = state
        if status is not None:
            updated.status = status
        updated.updated_at = now

        conn.execute(
            """
            UPDATE sessions
            SET title = ?,
                scenario_id = ?,
                scenario_label = ?,
                dataset_name = ?,
                created_at = ?,
                updated_at = ?,
                messages_json = ?,
                state_json = ?,
                status = ?
            WHERE session_id = ?
            """,
            (
                updated.title,
                updated.scenario_id,
                updated.scenario_label,
                updated.dataset_name,
                updated.created_at,
                updated.updated_at,
                _json_dumps(updated.messages),
                _json_dumps(updated.state),
                updated.status,
                updated.session_id,
            ),
        )
        conn.commit()
        return updated


def get_session(session_id: str) -> ChatSessionRecord | None:
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def list_sessions() -> list[ChatSessionSummary]:
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT
            session_id,
            title,
            scenario_id,
            scenario_label,
            dataset_name,
            created_at,
            updated_at,
            messages_json,
            status
        FROM sessions
        ORDER BY updated_at DESC
        """
    ).fetchall()
    return [
        ChatSessionSummary(
            session_id=str(row["session_id"]),
            title=str(row["title"]),
            scenario_id=str(row["scenario_id"]),
            scenario_label=str(row["scenario_label"]),
            dataset_name=str(row["dataset_name"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            message_count=len(_json_loads(str(row["messages_json"]))),
            status=str(row["status"]),
        )
        for row in rows
    ]


def get_session_payload(session_id: str) -> ChatSessionPayload | None:
    record = get_session(session_id)
    if record is None:
        return None
    return ChatSessionPayload(
        session_id=record.session_id,
        title=record.title,
        scenario_id=record.scenario_id,
        scenario_label=record.scenario_label,
        dataset_name=record.dataset_name,
        created_at=record.created_at,
        updated_at=record.updated_at,
        messages=record.messages,
        state=record.state,
        status=record.status,
    )


def clear_sessions(scenario_id: str | None = None) -> None:
    with _DB_LOCK:
        conn = _get_db()
        if scenario_id is None:
            conn.execute("DELETE FROM sessions")
        else:
            conn.execute("DELETE FROM sessions WHERE scenario_id = ?", (scenario_id,))
        conn.commit()


def delete_session(session_id: str) -> bool:
    with _DB_LOCK:
        conn = _get_db()
        cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0


def close_session_db() -> None:
    global _DB_CONN

    with _DB_LOCK:
        if _DB_CONN is None:
            return
        _DB_CONN.commit()
        _DB_CONN.close()
        _DB_CONN = None
