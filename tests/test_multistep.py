from __future__ import annotations

import datetime as dt

import pytest

from kgqa.config import get_settings
from kgqa.models import IntentType, QueryPlan, QueryPlanStep
from kgqa.planner import QueryPlanner
from kgqa.query import CypherSafetyValidator, DomainRegistry, Neo4jExecutor
from kgqa.router import IntentRouter
from kgqa.service import KGQAService


def test_nested_multistep_plan_is_rejected() -> None:
    plan = QueryPlan(
        strategy="bad_plan",
        steps=[
            QueryPlanStep(
                id="step_1",
                goal="bad nested step",
                query_type=IntentType.MULTI_STEP,
                question="哪台设备的能效比最低？",
            )
        ],
    )
    with pytest.raises(ValueError, match="nested MULTI_STEP step"):
        QueryPlanner._validate_plan(plan)


def test_llm_plan_normalization_rewrites_nested_multistep() -> None:
    plan = QueryPlan(
        strategy="llm_multistep",
        steps=[
            QueryPlanStep(
                id="step_1",
                goal="find the lowest cop device",
                query_type=IntentType.MULTI_STEP,
                question="万科名下的商业项目中，哪台设备的能效比最低？",
            )
        ],
    )
    normalized = QueryPlanner._normalize_plan(plan)
    assert normalized.steps[0].query_type == IntentType.CROSS_DOMAIN


def test_multistep_merge_uses_replacement_aliases() -> None:
    service = KGQAService(get_settings())
    merged = service._merge_multistep_rows(
        {"项目": "万科深圳湾商业中心", "型号": "30X-325", "品牌": "开利", "能效比": 4.98},
        [{"可替代设备": "EWAD-395", "品牌": "大金", "能效比": 5.34}],
    )
    assert merged["可替代方案"] == ["EWAD-395"]
    assert merged["替代品牌"] == ["大金"]


def test_multistep_step_type_is_downgraded_to_direct_query() -> None:
    assert KGQAService._normalize_step_query_type(
        IntentType.MULTI_STEP,
        "万科名下的商业项目中，哪台设备的能效比最低？",
    ) == IntentType.CROSS_DOMAIN
    assert KGQAService._normalize_step_query_type(
        IntentType.MULTI_STEP,
        "深圳和上海的项目相比，哪边平均能效更高？",
    ) == IntentType.AGGREGATION


def test_intent_filter_normalizes_project_type_variants() -> None:
    domain = DomainRegistry(get_settings())
    domain._project_types = ["商业", "住宅", "产业园区"]
    router = IntentRouter(None, domain=domain)
    filters = router._normalize_filters({"project_type": "商业项目", "type": "产业园项目"})
    assert filters["project_type"] == "商业"
    assert filters["type"] == "产业园区"


def test_context_keys_include_common_aliases() -> None:
    settings = get_settings()
    service = KGQAService(settings)
    keys = service._context_keys_for("step_1", "设备名称")
    assert "step_1_设备名称" in keys
    assert "step_1_型号" in keys
    assert "step_1_name" in keys


def test_replacement_list_question_treats_empty_as_failure() -> None:
    from kgqa.query import CypherGenerator
    assert CypherGenerator.should_treat_empty_as_failure("30X-325有哪些可替代方案？") is True
    assert CypherGenerator.should_treat_empty_as_failure("这台设备有没有可替代方案？") is False


def test_yes_no_question_is_not_mistaken_for_explicit_no_result() -> None:
    from kgqa.query import CypherGenerator
    assert CypherGenerator.should_treat_empty_as_failure("2023年后的项目中，有没有还在用R-22制冷剂设备的？") is True


def test_validator_rejects_comparator_literal_in_property_map() -> None:
    validator = CypherSafetyValidator()
    with pytest.raises(ValueError, match="范围/比较条件"):
        validator.validate(
            "MATCH (p:Project {dataset: 'kgqa_poc', start_date: '>2023'}) RETURN p.name AS 项目"
        )


def test_neo4j_executor_normalizes_date_like_values() -> None:
    normalized = Neo4jExecutor._normalize_value(
        {
            "开始日期": dt.date(2024, 3, 1),
            "列表": [dt.date(2024, 3, 2)],
        }
    )
    assert normalized["开始日期"] == "2024-03-01"
    assert normalized["列表"] == ["2024-03-02"]


def test_domain_registry_exposes_dynamic_refrigerants() -> None:
    domain = DomainRegistry(get_settings())
    domain._refrigerants = ["R-22", "R-410A"]
    assert domain.refrigerants == ["R-22", "R-410A"]


def test_domain_registry_exposes_dynamic_project_statuses_in_prompt_summary() -> None:
    domain = DomainRegistry(get_settings())
    domain._project_statuses = ["建设中", "规划", "运营中"]
    summary = domain.prompt_summary()
    assert domain.project_statuses == ["建设中", "规划", "运营中"]
    assert "项目状态" in summary
    assert "建设中" in summary
