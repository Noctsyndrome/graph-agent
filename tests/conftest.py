from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from kgqa.config import get_settings
from kgqa.session import close_session_db


@pytest.fixture(autouse=True)
def _isolated_session_db(monkeypatch):  # type: ignore[no-untyped-def]
    """Redirect session DB to a test-local SQLite file to avoid polluting data/sessions.db."""
    close_session_db()
    db_dir = Path(__file__).resolve().parents[1] / "data" / "test-sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"{uuid.uuid4().hex}.db"
    settings = get_settings().model_copy(update={"session_db_path": db_path})
    monkeypatch.setattr("kgqa.config.get_settings", lambda: settings)
    yield
    close_session_db()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
