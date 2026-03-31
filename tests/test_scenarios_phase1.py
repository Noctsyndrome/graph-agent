from __future__ import annotations

from fastapi.testclient import TestClient

from kgqa.api import app
from kgqa.config import get_settings
from kgqa.query import DomainRegistry
from kgqa.scenario import build_scenario_settings, get_scenario_definition


def test_scenario_registry_builds_elevator_settings() -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    scenario_settings = build_scenario_settings(settings, scenario)
    assert scenario_settings.dataset_name == "elevator_poc"
    assert scenario_settings.schema_file.name == "schema_elevator.yaml"
    assert scenario_settings.evaluation_file.name == "test_scenarios_elevator.yaml"


def test_domain_registry_returns_schema_driven_values() -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    scenario_settings = build_scenario_settings(settings, scenario)
    registry = DomainRegistry(scenario_settings)
    registry._values = {
        "Customer": {"name": ["绿城", "中海"]},
        "Project": {"status": ["建设中", "运营中"]},
        "Model": {"brand": ["奥的斯", "三菱"], "drive_type": ["永磁同步", "液压"]},
    }
    assert registry.as_dict()["Model"]["drive_type"] == ["永磁同步", "液压"]
    assert registry.get_filtered("Project") == {"Project": {"status": ["建设中", "运营中"]}}
    assert registry.get_filtered("Model.drive_type") == {"Model": {"drive_type": ["永磁同步", "液压"]}}
    assert registry.get_filtered("model.drive_type") == {"Model": {"drive_type": ["永磁同步", "液压"]}}
    assert registry.get_filtered("unknown") == {}


def test_schema_and_examples_endpoints_support_scenario_query(monkeypatch) -> None:
    class _FakeAgent:
        def __init__(self):
            self.domain = None

    monkeypatch.setattr("kgqa.api.get_kgqa_agent", lambda settings, scenario=None: _FakeAgent())
    client = TestClient(app)
    schema_response = client.get("/schema", params={"scenario_id": "elevator"})
    examples_response = client.get("/examples", params={"scenario_id": "elevator"})

    assert schema_response.status_code == 200
    assert examples_response.status_code == 200
    assert schema_response.json()["dataset"] == "elevator_poc"
    assert "baseline" in examples_response.json()
