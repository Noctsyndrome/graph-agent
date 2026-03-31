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
    payload = toolbox.list_domain_values("project_statuses")
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
