from __future__ import annotations

from kgqa.config import get_settings
from kgqa.query import DomainRegistry
from kgqa.schema import SchemaRegistry
from kgqa.tools import KGQAToolbox


class _DummyLLMClient:
    def generate(self, prompt: str, system_prompt: str = ""):  # type: ignore[no-untyped-def]
        raise AssertionError("LLM should not be called in this test")


def _build_toolbox() -> KGQAToolbox:
    settings = get_settings()
    domain = DomainRegistry(settings)
    domain._values = {
        "Customer": {"name": ["万科", "华润"]},
        "Project": {"city": ["深圳", "上海"], "type": ["商业", "住宅"], "status": ["建设中", "运营中"]},
        "Category": {"name": ["冷水机组"]},
        "Model": {"brand": ["开利", "大金"], "refrigerant": ["R-22", "R-410A"]},
    }
    schema = SchemaRegistry(settings, domain=domain)
    return KGQAToolbox(settings, schema, domain, _DummyLLMClient())  # type: ignore[arg-type]


def test_list_domain_values_returns_expected_kind() -> None:
    toolbox = _build_toolbox()
    payload = toolbox.list_domain_values("Project.status")
    assert payload == {"Project": {"status": ["建设中", "运营中"]}}


def test_format_results_returns_table_renderer_for_preview_rows() -> None:
    toolbox = _build_toolbox()
    result = toolbox.format_results(
        question="哪些客户的项目用了大金的设备？",
        rows=[
            {"客户": "万科", "项目": "万科深圳湾商业中心", "品牌": "大金"},
            {"客户": "华润", "项目": "华润深圳前海万象城", "品牌": "大金"},
        ],
    )
    assert result["renderer"] == "table"
    assert result["row_count"] == 2
    assert len(result["preview"]) == 2


def test_tool_specs_include_dataset_and_entity_field_examples() -> None:
    toolbox = _build_toolbox()
    specs = {item["name"]: item for item in toolbox.tool_specs()}
    assert "kgqa_poc" in specs["validate_cypher"]["description"]
    assert "Entity.field" in specs["list_domain_values"]["description"]
    assert "Customer.name" in specs["list_domain_values"]["description"]
    assert "match_value" in specs
    assert "diagnose_error" in specs


def test_match_value_returns_exact_match() -> None:
    toolbox = _build_toolbox()
    payload = toolbox.match_value("Model", "brand", "大金")
    assert payload["status"] == "ok"
    assert payload["exact_match"] == "大金"
    assert payload["fuzzy_matches"] == []


def test_match_value_returns_fuzzy_candidates() -> None:
    toolbox = _build_toolbox()
    toolbox.domain._values["Category"]["name"] = ["乘客电梯", "载货电梯"]
    payload = toolbox.match_value("Category", "name", "客梯")
    assert payload["status"] == "ok"
    assert payload["exact_match"] is None
    assert "乘客电梯" in payload["fuzzy_matches"]


def test_diagnose_error_returns_structured_property_advice() -> None:
    toolbox = _build_toolbox()
    payload = toolbox.diagnose_error(
        "MATCH (m:Model {dataset: 'kgqa_poc'}) WHERE m.cooling > 100 RETURN m.name AS name",
        {"code": "unknown_property", "message": "属性 Model.cooling 不存在于当前 schema 中。", "details": {"entity": "Model", "property": "cooling"}},
    )
    assert payload["status"] == "ok"
    assert payload["error_type"] == "invalid_property"
    assert payload["entity"] == "Model"
    assert "brand" in payload["available_properties"]
