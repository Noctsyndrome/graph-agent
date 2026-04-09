from __future__ import annotations

from fastapi.testclient import TestClient

from kgqa.api import app
from kgqa.session import clear_sessions, close_session_db, get_session_payload, list_sessions, upsert_session


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


def test_session_store_persists_after_close_and_reopen() -> None:
    upsert_session(
        "persistent-session",
        scenario_id="property",
        scenario_label="物业管理",
        dataset_name="property_poc",
        messages=[{"id": "u1", "role": "user", "content": "帮我继续上次的问题"}],
        state={"latestResult": {"renderer": "markdown_table"}},
        status="running",
    )

    close_session_db()

    payload = get_session_payload("persistent-session")
    assert payload is not None
    assert payload.scenario_id == "property"
    assert payload.status == "running"
    assert payload.messages[0]["content"] == "帮我继续上次的问题"


def test_clear_sessions_only_deletes_target_scenario() -> None:
    upsert_session(
        "hvac-session",
        scenario_id="hvac",
        scenario_label="HVAC 冷水机组",
        dataset_name="kgqa_poc",
        messages=[{"id": "u1", "role": "user", "content": "冷机问题"}],
        state={},
        status="completed",
    )
    upsert_session(
        "elevator-session",
        scenario_id="elevator",
        scenario_label="建筑行业 · 电梯设备",
        dataset_name="elevator_poc",
        messages=[{"id": "u2", "role": "user", "content": "电梯问题"}],
        state={},
        status="completed",
    )

    clear_sessions(scenario_id="hvac")

    assert get_session_payload("hvac-session") is None
    assert get_session_payload("elevator-session") is not None


def test_list_sessions_orders_by_updated_at_desc() -> None:
    upsert_session(
        "older-session",
        scenario_id="hvac",
        scenario_label="HVAC 冷水机组",
        dataset_name="kgqa_poc",
        messages=[{"id": "u1", "role": "user", "content": "第一轮"}],
        state={},
        status="completed",
    )
    upsert_session(
        "newer-session",
        scenario_id="elevator",
        scenario_label="建筑行业 · 电梯设备",
        dataset_name="elevator_poc",
        messages=[{"id": "u2", "role": "user", "content": "第二轮"}],
        state={},
        status="completed",
    )
    upsert_session(
        "older-session",
        scenario_id="hvac",
        scenario_label="HVAC 冷水机组",
        dataset_name="kgqa_poc",
        status="running",
    )

    sessions = list_sessions()

    assert [item.session_id for item in sessions] == ["older-session", "newer-session"]
    assert sessions[0].message_count == 1
    assert sessions[0].status == "running"


def test_scenarios_endpoint_returns_hvac_elevator_and_property() -> None:
    client = TestClient(app)
    response = client.get("/scenarios")
    assert response.status_code == 200
    payload = response.json()
    assert {item["id"] for item in payload} == {"elevator", "hvac", "property"}


def test_schema_graph_endpoint_returns_nodes_and_links() -> None:
    client = TestClient(app)

    response = client.get("/schema/graph?scenario_id=property")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset"] == "property_ops"
    assert len(payload["nodes"]) == 6
    assert len(payload["links"]) == 6
    assert any(node["id"] == "Space" for node in payload["nodes"])
    assert any(link["label"] == "HAS_SPACE" for link in payload["links"])


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


def test_delete_session_endpoint_removes_session() -> None:
    upsert_session(
        "delete-me",
        scenario_id="property",
        scenario_label="物业资产经营",
        dataset_name="property_ops",
        messages=[{"id": "u1", "role": "user", "content": "待删除会话"}],
        state={},
        status="completed",
    )
    client = TestClient(app)

    response = client.delete("/chat/delete-me")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "session_id": "delete-me"}
    assert get_session_payload("delete-me") is None
