from __future__ import annotations

import threading
import time
from typing import Any

from kgqa.models import ChatSessionPayload, ChatSessionRecord, ChatSessionSummary

_SESSION_STORE: dict[str, ChatSessionRecord] = {}
_SESSION_LOCK = threading.Lock()


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


def upsert_session(
    session_id: str,
    messages: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    status: str | None = None,
) -> ChatSessionRecord:
    now = time.time()
    with _SESSION_LOCK:
        existing = _SESSION_STORE.get(session_id)
        if existing is None:
            record = ChatSessionRecord(
                session_id=session_id,
                title=_derive_title(messages or []),
                created_at=now,
                updated_at=now,
                messages=messages or [],
                state=state or {},
                status=status or "idle",
            )
            _SESSION_STORE[session_id] = record
            return record

        updated = existing.model_copy(deep=True)
        if messages is not None:
            updated.messages = messages
            updated.title = _derive_title(messages, fallback=existing.title)
        if state is not None:
            updated.state = state
        if status is not None:
            updated.status = status
        updated.updated_at = now
        _SESSION_STORE[session_id] = updated
        return updated


def get_session(session_id: str) -> ChatSessionRecord | None:
    with _SESSION_LOCK:
        record = _SESSION_STORE.get(session_id)
        return record.model_copy(deep=True) if record else None


def list_sessions() -> list[ChatSessionSummary]:
    with _SESSION_LOCK:
        records = sorted(
            _SESSION_STORE.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        return [
            ChatSessionSummary(
                session_id=record.session_id,
                title=record.title,
                created_at=record.created_at,
                updated_at=record.updated_at,
                message_count=len(record.messages),
                status=record.status,
            )
            for record in records
        ]


def get_session_payload(session_id: str) -> ChatSessionPayload | None:
    record = get_session(session_id)
    if record is None:
        return None
    return ChatSessionPayload(
        session_id=record.session_id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        messages=record.messages,
        state=record.state,
        status=record.status,
    )


def clear_sessions() -> None:
    with _SESSION_LOCK:
        _SESSION_STORE.clear()
