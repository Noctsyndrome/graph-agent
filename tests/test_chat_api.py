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
        scenario_id="hvac",
        scenario_label="HVAC 冷水机组",
        dataset_name="kgqa_poc",
        messages=[{"id": "u1", "role": "user", "content": "测试问题"}],
        state={"latestResult": {"renderer": "raw_json"}},
        status="completed",
    )
    payload = get_session_payload("session-1")
    assert payload is not None
    assert payload.session_id == "session-1"
    assert payload.scenario_id == "hvac"
    assert payload.messages[0]["content"] == "测试问题"
    assert payload.state["latestResult"]["renderer"] == "raw_json"


def test_scenarios_endpoint_returns_hvac_elevator_and_property() -> None:
    client = TestClient(app)
    response = client.get("/scenarios")
    assert response.status_code == 200
    payload = response.json()
    assert {item["id"] for item in payload} == {"elevator", "hvac", "property"}


def test_chat_stream_endpoint_returns_sse(monkeypatch) -> None:
    clear_sessions()
    monkeypatch.setattr("kgqa.api.get_kgqa_agent", lambda settings, scenario=None: _FakeAgent())
    client = TestClient(app)

    with client.stream(
        "POST",
        "/chat",
        json={
            "threadId": "fake-thread",
            "scenarioId": "elevator",
            "messages": [{"id": "u1", "role": "user", "content": "你好"}],
            "state": {},
        },
    ) as response:
        chunks = [line for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert any("RUN_STARTED" in chunk for chunk in chunks)
    assert any("TEXT_MESSAGE_CONTENT" in chunk for chunk in chunks)


def test_chat_stream_rejects_switching_scenario() -> None:
    clear_sessions()
    upsert_session(
        "locked-session",
        scenario_id="elevator",
        scenario_label="建筑行业 · 电梯设备",
        dataset_name="elevator_poc",
        messages=[{"id": "u1", "role": "user", "content": "之前的问题"}],
        state={},
        status="completed",
    )
    client = TestClient(app)
    response = client.post(
        "/chat",
        json={
            "threadId": "locked-session",
            "scenarioId": "hvac",
            "messages": [{"id": "u2", "role": "user", "content": "继续"}],
            "state": {},
        },
    )
    assert response.status_code == 409
