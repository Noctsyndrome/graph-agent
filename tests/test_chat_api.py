from __future__ import annotations

from fastapi.testclient import TestClient

from kgqa.api import app
from kgqa.session import clear_sessions, get_session_payload, upsert_session


class _FakeAgent:
    def stream_chat(self, request):  # type: ignore[no-untyped-def]
        yield 'data: {"type":"RUN_STARTED","threadId":"fake-thread"}\n\n'
        yield 'data: {"type":"TEXT_MESSAGE_START","messageId":"m1","role":"assistant"}\n\n'
        yield 'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"hello"}\n\n'
        yield 'data: {"type":"TEXT_MESSAGE_END","messageId":"m1"}\n\n'
        yield 'data: {"type":"RUN_FINISHED","threadId":"fake-thread"}\n\n'


def test_session_store_roundtrip() -> None:
    clear_sessions()
    upsert_session(
        "session-1",
        messages=[{"id": "u1", "role": "user", "content": "测试问题"}],
        state={"latestResult": {"renderer": "raw_json"}},
        status="completed",
    )
    payload = get_session_payload("session-1")
    assert payload is not None
    assert payload.session_id == "session-1"
    assert payload.messages[0]["content"] == "测试问题"
    assert payload.state["latestResult"]["renderer"] == "raw_json"


def test_chat_stream_endpoint_returns_sse(monkeypatch) -> None:
    clear_sessions()
    monkeypatch.setattr("kgqa.api.get_kgqa_agent", lambda settings: _FakeAgent())
    client = TestClient(app)

    with client.stream(
        "POST",
        "/chat",
        json={
            "threadId": "fake-thread",
            "messages": [{"id": "u1", "role": "user", "content": "你好"}],
            "state": {},
        },
    ) as response:
        chunks = [line for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert any("RUN_STARTED" in chunk for chunk in chunks)
    assert any("TEXT_MESSAGE_CONTENT" in chunk for chunk in chunks)
