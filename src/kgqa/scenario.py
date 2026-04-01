from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kgqa.config import ROOT, Settings

DEFAULT_SCENARIO_ID = "hvac"


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    label: str
    description: str
    dataset_name: str
    schema_file: Path
    seed_file: Path
    evaluation_file: Path

    def to_payload(self) -> dict[str, str]:
        return {
            "id": self.scenario_id,
            "label": self.label,
            "description": self.description,
            "dataset_name": self.dataset_name,
        }


_SCENARIOS: dict[str, ScenarioDefinition] = {
    "hvac": ScenarioDefinition(
        scenario_id="hvac",
        label="HVAC 冷水机组",
        description="现有暖通知识图谱演示场景。",
        dataset_name="kgqa_poc",
        schema_file=ROOT / "data" / "schema.yaml",
        seed_file=ROOT / "data" / "seed_data.cypher",
        evaluation_file=ROOT / "tests" / "test_scenarios.yaml",
    ),
    "elevator": ScenarioDefinition(
        scenario_id="elevator",
        label="建筑行业 · 电梯设备",
        description="与 HVAC 同构的电梯设备知识图谱场景。",
        dataset_name="elevator_poc",
        schema_file=ROOT / "data" / "schema_elevator.yaml",
        seed_file=ROOT / "data" / "seed_data_elevator.cypher",
        evaluation_file=ROOT / "tests" / "test_scenarios_elevator.yaml",
    ),
    "property": ScenarioDefinition(
        scenario_id="property",
        label="物业资产经营",
        description="面向经营项目、空间、租户、合同与付款的异构知识图谱场景。",
        dataset_name="property_ops",
        schema_file=ROOT / "data" / "schema_property.yaml",
        seed_file=ROOT / "data" / "seed_data_property.cypher",
        evaluation_file=ROOT / "tests" / "test_scenarios_property.yaml",
    ),
}


def list_scenarios() -> list[ScenarioDefinition]:
    return [_SCENARIOS[key] for key in sorted(_SCENARIOS)]


def get_scenario_definition(scenario_id: str | None = None) -> ScenarioDefinition:
    key = scenario_id or DEFAULT_SCENARIO_ID
    try:
        return _SCENARIOS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown scenario: {key}") from exc


def build_scenario_settings(settings: Settings, scenario: ScenarioDefinition) -> Settings:
    return settings.model_copy(
        update={
            "dataset_name": scenario.dataset_name,
            "schema_file": scenario.schema_file,
            "seed_file": scenario.seed_file,
            "evaluation_file": scenario.evaluation_file,
        }
    )
