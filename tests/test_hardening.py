from __future__ import annotations

import json

from kgqa.agent import KGQAAgent
from kgqa.config import get_settings
from kgqa.models import ChatRequest
from kgqa.query import DomainRegistry, inspect_dataset_readiness
from kgqa.scenario import build_scenario_settings, get_scenario_definition
from kgqa.schema import SchemaRegistry
from kgqa.serializer import ResultSerializer
from kgqa.tools import KGQAToolbox


class _FakeLLMClient:
    def generate(self, prompt: str, system_prompt: str = ""):  # type: ignore[no-untyped-def]
        raise AssertionError("LLM should not be called in this test")


def _build_elevator_toolbox() -> KGQAToolbox:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    scenario_settings = build_scenario_settings(settings, scenario)
    domain = DomainRegistry(scenario_settings)
    domain._values = {
        "Customer": {"name": ["中海"]},
        "Project": {"city": ["武汉"], "status": ["建设中"]},
        "Category": {"name": ["乘客电梯"]},
        "Model": {"brand": ["三菱"], "drive_type": ["永磁同步"]},
    }
    schema = SchemaRegistry(scenario_settings, domain=domain)
    return KGQAToolbox(scenario_settings, schema, domain, _FakeLLMClient())  # type: ignore[arg-type]


def test_validate_cypher_rejects_missing_dataset_filter() -> None:
    toolbox = _build_elevator_toolbox()
    payload = toolbox.validate_cypher("MATCH (m:Model) RETURN m.name AS name")
    assert payload["valid"] is False
    assert payload["error"]["code"] == "missing_dataset_filter"


def test_validate_cypher_rejects_unknown_schema_property() -> None:
    toolbox = _build_elevator_toolbox()
    payload = toolbox.validate_cypher(
        "MATCH (m:Model {dataset: 'elevator_poc'}) WHERE m.cooling_kw > 100 RETURN m.name AS name"
    )
    assert payload["valid"] is False
    assert payload["error"]["code"] == "unknown_property"
    assert "load_kg" in payload["error"]["hint"]


def test_validate_cypher_rejects_wrong_relationship_direction() -> None:
    toolbox = _build_elevator_toolbox()
    payload = toolbox.validate_cypher(
        "MATCH (c:Category {name: '乘客电梯', dataset: 'elevator_poc'})-[:BELONGS_TO]->(m:Model {dataset: 'elevator_poc'}) "
        "RETURN m.name AS model_name"
    )
    assert payload["valid"] is False
    assert payload["error"]["code"] == "relationship_semantics_mismatch"
    assert "(Model)-[:BELONGS_TO]->(Category)" in payload["error"]["hint"]


def test_validate_cypher_allows_reusing_previously_filtered_variables() -> None:
    toolbox = _build_elevator_toolbox()
    payload = toolbox.validate_cypher(
        (
            "MATCH (m:Model {dataset: 'elevator_poc'})-[:BELONGS_TO]->"
            "(cat:Category {name: '乘客电梯', dataset: 'elevator_poc'}) "
            "MATCH (i:Installation {dataset: 'elevator_poc'})-[:USES_MODEL]->(m) "
            "MATCH (p:Project {dataset: 'elevator_poc'})-[:HAS_INSTALLATION]->(i) "
            "MATCH (c:Customer {dataset: 'elevator_poc'})-[:OWNS_PROJECT]->(p) "
            "RETURN c.name AS customer, SUM(i.quantity) AS total_quantity "
            "ORDER BY total_quantity DESC LIMIT 10"
        )
    )
    assert payload["valid"] is True
    assert payload["status"] == "ok"


def test_serializer_uses_structure_not_question_keywords() -> None:
    serializer = ResultSerializer()

    key_value_result = serializer.serialize(
        [{"型号": "MONOSPACE-1200", "载重": 1200, "速度": 4.0, "价格": 132}],
        question="请告诉我具体参数",
    )
    aggregation_result = serializer.serialize(
        [{"品牌": "三菱", "数量": 8}, {"品牌": "通力", "数量": 5}],
        question="请列出品牌统计",
    )

    assert key_value_result.format == "key_value"
    assert aggregation_result.format == "markdown_table"


def test_serializer_unwraps_single_node_payload() -> None:
    serializer = ResultSerializer()
    result = serializer.serialize(
        [
            {
                "m": {
                    "__type__": "node",
                    "properties": {"name": "MONOSPACE-1200", "brand": "三菱", "load_kg": 1200},
                }
            }
        ]
    )
    assert result.format == "key_value"
    assert "MONOSPACE-1200" in result.markdown


def test_schema_focus_expands_to_neighbor_entities() -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    scenario_settings = build_scenario_settings(settings, scenario)
    registry = SchemaRegistry(scenario_settings)

    rendered = registry.render_schema_context("安装数量超过 5 台的有哪些？")

    assert "- Installation:" in rendered
    assert "- Project:" in rendered


def test_agent_returns_scenario_not_loaded_before_loop(monkeypatch) -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")

    monkeypatch.setattr("kgqa.agent.DomainRegistry.load", lambda self: None)
    monkeypatch.setattr(
        "kgqa.agent.inspect_dataset_readiness",
        lambda settings, schema: {
            "ready": False,
            "dataset": "elevator_poc",
            "counts": {"__all__": 0, "Model": 0, "Project": 0},
            "required_entities": ["Model", "Project"],
            "missing_entities": ["Model", "Project"],
        },
    )

    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    events = list(
        agent.stream_chat(
            ChatRequest(
                scenarioId="elevator",
                messages=[{"id": "u1", "role": "user", "content": "你好"}],
                state={},
            )
        )
    )
    payloads = [json.loads(event.removeprefix("data: ").strip()) for event in events]

    assert payloads[0]["type"] == "RUN_ERROR"
    assert payloads[0]["code"] == "scenario_not_loaded"


def test_diagnose_error_parses_raw_neo4j_message() -> None:
    toolbox = _build_elevator_toolbox()
    payload = toolbox.diagnose_error(
        "MATCH (m:Model {dataset: 'elevator_poc'}) WHERE m.cooling_kw > 100 RETURN m.name AS name",
        "Property 'cooling_kw' does not exist on node with label 'Model'",
    )
    assert payload["status"] == "ok"
    assert payload["error_type"] == "invalid_property"
    assert payload["problematic_token"] == "cooling_kw"
    assert payload["entity"] == "Model"
    assert "load_kg" in payload["available_properties"]


def test_match_value_is_scenario_scoped() -> None:
    toolbox = _build_elevator_toolbox()
    payload = toolbox.match_value("Model", "brand", "通力")
    assert payload["status"] == "ok"
    assert payload["exact_match"] is None
    assert payload["fuzzy_matches"] == []


def test_aux_tools_do_not_consume_main_budget(monkeypatch) -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    monkeypatch.setattr("kgqa.agent.DomainRegistry.load", lambda self: None)
    monkeypatch.setattr(
        "kgqa.agent.inspect_dataset_readiness",
        lambda settings, schema: {
            "ready": True,
            "dataset": "elevator_poc",
            "counts": {"__all__": 1, "Model": 1, "Project": 1},
            "required_entities": ["Model", "Project"],
            "missing_entities": [],
        },
    )
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    decisions = iter(
        [
            {"action": "call_tool", "tool_name": "get_schema_context", "tool_args": {"question": "q"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "match_value", "tool_args": {"entity": "Category", "field": "name", "keyword": "客梯"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "diagnose_error", "tool_args": {"cypher": "MATCH (m:Model) RETURN m", "error": "Property 'cooling_kw' does not exist on node with label 'Model'"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "validate_cypher", "tool_args": {"cypher": "MATCH (m:Model {dataset: 'elevator_poc'}) RETURN m.name AS name"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "execute_cypher", "tool_args": {"cypher": "MATCH (m:Model {dataset: 'elevator_poc'}) RETURN m.name AS name"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "format_results", "tool_args": {"question": "q", "rows": [{"name": "MONOSPACE-1200"}]}, "auto_finish_after_format": True},
        ]
    )

    monkeypatch.setattr(agent, "_decide_next_action", lambda *args, **kwargs: next(decisions))

    def _fake_run_tool(thread_id, messages, state, tool_name, tool_args):  # type: ignore[no-untyped-def]
        if tool_name == "execute_cypher":
            state["_latest_rows"] = [{"name": "MONOSPACE-1200"}]
            result = {"status": "ok", "row_count": 1, "rows": [{"name": "MONOSPACE-1200"}]}
        elif tool_name == "format_results":
            result = {"renderer": "table", "payload": [{"name": "MONOSPACE-1200"}], "markdown": "| name |\n| --- |\n| MONOSPACE-1200 |", "row_count": 1, "format": "table", "preview": [{"name": "MONOSPACE-1200"}]}
        else:
            result = {"status": "ok"}
        return result, "ok", messages, state

    monkeypatch.setattr(agent, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(agent.toolbox, "compose_answer", lambda question, formatted_result: "done")

    events = list(
        agent.stream_chat(
            ChatRequest(
                scenarioId="elevator",
                messages=[{"id": "u1", "role": "user", "content": "奥的斯的客梯有哪些"}],
                state={},
            )
        )
    )
    payloads = [json.loads(event.removeprefix("data: ").strip()) for event in events]
    run_finished = next(item for item in payloads if item["type"] == "RUN_FINISHED")
    assert run_finished["type"] == "RUN_FINISHED"


def test_budget_counters_split_aux_and_main(monkeypatch) -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    monkeypatch.setattr("kgqa.agent.DomainRegistry.load", lambda self: None)
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    state = {"_budget": {"aux_remaining": agent.AUX_BUDGET, "main_remaining": agent.MAIN_BUDGET}}

    agent._consume_budget(state, "match_value")
    agent._consume_budget(state, "diagnose_error")
    agent._consume_budget(state, "execute_cypher")

    assert state["_budget"]["aux_remaining"] == agent.AUX_BUDGET - 2
    assert state["_budget"]["main_remaining"] == agent.MAIN_BUDGET - 1


def test_schema_context_can_be_detected_from_session_history() -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    messages = [
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "get_schema_context", "arguments": "{\"question\":\"哪些客户使用了日立电梯？\"}"},
                }
            ],
        },
        {
            "id": "t1",
            "role": "tool",
            "toolCallId": "tc1",
            "content": "{\"schema_context\":\"...\",\"summary\":{\"dataset\":\"elevator_poc\"}}",
        },
        {"id": "u2", "role": "user", "content": "绿城都有哪些项目，分别都使用了哪些设备？"},
    ]

    assert agent._has_recent_schema_context(messages) is True


def test_build_user_prompt_includes_recent_10_message_transcript() -> None:
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    messages = [
        {"id": f"m{index}", "role": "user" if index % 2 == 0 else "assistant", "content": f"message-{index}"}
        for index in range(12)
    ]

    prompt = agent._build_user_prompt(
        question="这些类型分别对应哪些型号",
        messages=messages,
        observations=[],
        formatted_result=None,
        tool_specs=[],
        candidate_domain_matches=[],
        budget={"aux_remaining": 4, "main_remaining": 8},
        current_phase="阶段 2（查询）",
        recent_errors=[],
    )

    assert "## 对话历史" in prompt
    assert "[user] message-2" in prompt
    assert "[assistant] message-11" in prompt
    assert "[user] message-0\n" not in prompt
    assert "[assistant] message-1\n" not in prompt


def test_schema_context_is_auto_injected_before_first_tool(monkeypatch) -> None:
    """Schema context must be auto-injected as a pre-step on new conversations."""
    settings = get_settings()
    scenario = get_scenario_definition("elevator")
    monkeypatch.setattr("kgqa.agent.DomainRegistry.load", lambda self: None)
    monkeypatch.setattr(
        "kgqa.agent.inspect_dataset_readiness",
        lambda settings, schema: {
            "ready": True,
            "dataset": "elevator_poc",
            "counts": {"__all__": 1, "Model": 1, "Project": 1},
            "required_entities": ["Model", "Project"],
            "missing_entities": [],
        },
    )
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    tool_order: list[str] = []

    def _fake_run_tool(thread_id, messages, state, tool_name, tool_args):  # type: ignore[no-untyped-def]
        tool_order.append(tool_name)
        if tool_name == "get_schema_context":
            return {"schema_context": "## Schema\n...", "summary": {}}, "ok", messages, state
        if tool_name == "format_results":
            return {"renderer": "table", "payload": [{"count": 1}], "markdown": "| count |\n| --- |\n| 1 |", "row_count": 1, "format": "table", "preview": [{"count": 1}]}, "ok", messages, state
        if tool_name == "execute_cypher":
            state["_latest_rows"] = [{"count": 1}]
            return {"status": "ok", "row_count": 1, "rows": [{"count": 1}]}, "ok", messages, state
        if tool_name == "validate_cypher":
            return {"status": "ok", "valid": True, "cypher": tool_args["cypher"]}, "ok", messages, state
        return {"status": "ok"}, "ok", messages, state

    decisions = iter(
        [
            {"action": "call_tool", "tool_name": "validate_cypher", "tool_args": {"cypher": "MATCH (m:Model {dataset: 'elevator_poc'}) RETURN count(m) AS count"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "execute_cypher", "tool_args": {"cypher": "MATCH (m:Model {dataset: 'elevator_poc'}) RETURN count(m) AS count"}, "auto_finish_after_format": False},
            {"action": "call_tool", "tool_name": "format_results", "tool_args": {"question": "日立有几个型号的乘客电梯？", "rows": [{"count": 1}]}, "auto_finish_after_format": True},
        ]
    )

    monkeypatch.setattr(agent, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(agent, "_decide_next_action", lambda *args, **kwargs: next(decisions))
    monkeypatch.setattr(agent.toolbox, "compose_answer", lambda question, formatted_result: "done")

    list(
        agent.stream_chat(
            ChatRequest(
                scenarioId="elevator",
                messages=[{"id": "u1", "role": "user", "content": "日立有几个型号的乘客电梯？"}],
                state={},
            )
        )
    )

    # Schema context should be auto-injected as the first tool call (pre-step)
    assert tool_order[0] == "get_schema_context"
    # LLM-decided tools follow after
    assert tool_order[1] == "validate_cypher"


def test_inspect_dataset_readiness_uses_schema_entity_order(monkeypatch) -> None:
    settings = get_settings()
    scenario = get_scenario_definition("property")
    scenario_settings = build_scenario_settings(settings, scenario)
    schema = SchemaRegistry(scenario_settings).schema

    monkeypatch.setattr("kgqa.query.Neo4jExecutor.count_dataset_nodes", lambda self, dataset_name: 12)

    def _fake_count_entity_nodes(self, entity_name):  # type: ignore[no-untyped-def]
        counts = {"OperatingCompany": 2, "OperatingProject": 4}
        return counts.get(entity_name, 0)

    monkeypatch.setattr("kgqa.query.Neo4jExecutor.count_entity_nodes", _fake_count_entity_nodes)

    readiness = inspect_dataset_readiness(scenario_settings, schema)

    assert readiness["ready"] is True
    assert readiness["required_entities"] == ["OperatingCompany", "OperatingProject"]


def test_validate_decision_rejects_redundant_aux_tool_call(monkeypatch) -> None:
    settings = get_settings()
    scenario = get_scenario_definition("property")
    monkeypatch.setattr("kgqa.agent.DomainRegistry.load", lambda self: None)
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    state = {
        "toolHistory": [
            {
                "tool_name": "list_domain_values",
                "tool_args": {},
                "status": "ok",
                "tool_result": {"status": "ok"},
            }
        ]
    }

    decision_issue = agent._validate_decision(
        {"action": "call_tool", "tool_name": "list_domain_values", "tool_args": {}},
        state,
    )

    assert decision_issue is not None
    assert decision_issue["tool_result"]["error"]["code"] == "redundant_aux_tool_call"


def test_candidate_domain_matches_detects_named_values() -> None:
    settings = get_settings()
    scenario = get_scenario_definition("property")
    agent = KGQAAgent(build_scenario_settings(settings, scenario), scenario)
    matches = agent._candidate_domain_matches("星巴克在哪些项目有门店？")

    assert {"entity": "Tenant", "field": "name", "value": "星巴克"} in matches
