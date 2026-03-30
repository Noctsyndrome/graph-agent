from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from kgqa.api import app


def load_cases() -> list[dict[str, object]]:
    payload = yaml.safe_load(Path("tests/test_scenarios.yaml").read_text(encoding="utf-8"))
    return payload["baseline"][:2] + payload["challenge"][:2]


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_schema() -> None:
    client = TestClient(app)
    response = client.get("/schema")
    assert response.status_code == 200
    payload = response.json()
    assert payload["entity_count"] == 5
    assert payload["relationship_count"] == 6


def test_query_responses_have_expected_shape() -> None:
    client = TestClient(app)
    cases = load_cases()
    for case in cases:
        response = client.post("/query", json={"question": case["question"]})
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            payload = response.json()
            assert payload["intent"]
            assert "answer" in payload
            assert "trace" in payload
            assert "intent" in payload["trace"]
