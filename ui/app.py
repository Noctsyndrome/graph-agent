from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import streamlit as st
import yaml

API_BASE_URL = os.getenv("KGQA_API_BASE_URL", "http://localhost:8000").rstrip("/")

_SCENARIOS_PATH = Path(__file__).parent.parent / "tests" / "test_scenarios.yaml"

_GROUP_META: dict[str, dict[str, str]] = {
    "baseline": {
        "label": "基准用例 Baseline",
        "desc": "核心场景验证，覆盖四种查询类型",
    },
    "challenge": {
        "label": "挑战用例 Challenge",
        "desc": "同义改写、边界条件与空结果测试",
    },
    "generalization": {
        "label": "泛化用例 Generalization",
        "desc": "规则未覆盖的新角度与新路径",
    },
}


# ------------------------------------------------------------------
# Data loaders
# ------------------------------------------------------------------


@st.cache_data
def load_test_scenarios() -> dict[str, list[dict[str, Any]]]:
    """Load test scenarios from the shared YAML file used by eval framework."""
    if not _SCENARIOS_PATH.exists():
        return {}
    return yaml.safe_load(_SCENARIOS_PATH.read_text(encoding="utf-8")) or {}


@st.cache_data(ttl=15)
def fetch_health() -> dict[str, Any]:
    response = httpx.get(f"{API_BASE_URL}/health", timeout=10.0)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=30)
def fetch_schema_summary() -> dict[str, Any]:
    response = httpx.get(f"{API_BASE_URL}/schema", timeout=15.0)
    response.raise_for_status()
    return response.json()


def request_query(question: str) -> dict[str, Any]:
    response = httpx.post(
        f"{API_BASE_URL}/query",
        json={"question": question},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()


def request_seed_load() -> dict[str, Any]:
    response = httpx.post(f"{API_BASE_URL}/seed/load", timeout=120.0)
    response.raise_for_status()
    return response.json()


def refresh_system_status() -> None:
    health_error = ""
    schema_error = ""
    health_payload: dict[str, Any] | None = None
    schema_payload: dict[str, Any] | None = None

    try:
        health_payload = fetch_health()
    except Exception as exc:
        health_error = str(exc)

    try:
        schema_payload = fetch_schema_summary()
    except Exception as exc:
        schema_error = str(exc)

    st.session_state.health_payload = health_payload
    st.session_state.schema_payload = schema_payload
    st.session_state.health_error = health_error
    st.session_state.schema_error = schema_error


# ------------------------------------------------------------------
# Rendering helpers
# ------------------------------------------------------------------


def render_trace_summary(trace: dict[str, Any]) -> None:
    intent = trace.get("intent", {})
    plan = trace.get("plan", {})
    cypher = trace.get("cypher", {})
    answer = trace.get("answer", {})

    stage_rows = [
        {
            "阶段": "Intent",
            "来源": intent.get("source", "none"),
            "耗时(ms)": intent.get("latency_ms", 0),
            "尝试次数": intent.get("attempts", 0),
            "备注": intent.get("reason", ""),
        },
        {
            "阶段": "Plan",
            "来源": plan.get("source", "none"),
            "耗时(ms)": plan.get("latency_ms", 0),
            "尝试次数": plan.get("attempts", 0),
            "备注": plan.get("reason", ""),
        },
        {
            "阶段": "Cypher",
            "来源": cypher.get("source", "none"),
            "耗时(ms)": cypher.get("latency_ms", 0),
            "尝试次数": cypher.get("attempts", 0),
            "备注": cypher.get("reason", ""),
        },
        {
            "阶段": "Answer",
            "来源": answer.get("source", "none"),
            "耗时(ms)": answer.get("latency_ms", 0),
            "尝试次数": answer.get("attempts", 0),
            "备注": answer.get("reason", ""),
        },
    ]
    st.dataframe(stage_rows, use_container_width=True, hide_index=True)

    st.success("全链路 LLM 执行完成。")


def render_plan(plan: dict[str, Any] | None) -> None:
    if not plan:
        st.info("当前请求为单步查询，无额外执行计划。")
        return

    st.caption(f"执行策略：{plan.get('strategy', '')}")
    for index, step in enumerate(plan.get("steps", []), start=1):
        depends_on = "、".join(step.get("depends_on", [])) or "无"
        with st.container(border=True):
            st.markdown(f"**Step {index} · {step.get('id', '')}**")
            st.write(f"目标：{step.get('goal', '')}")
            st.write(f"类型：`{step.get('query_type', '')}`")
            st.write(f"问题：{step.get('question', '')}")
            st.write(f"依赖：{depends_on}")


def render_intent(trace: dict[str, Any]) -> None:
    intent = trace.get("intent", {})
    entities = intent.get("entities", [])
    filters = intent.get("filters", {})
    st.write(f"来源：`{intent.get('source', 'none')}`")
    st.write(f"置信度：`{intent.get('confidence', 0)}`")
    st.write(f"聚合需求：`{intent.get('needs_aggregation', False)}`")
    st.write(f"多步需求：`{intent.get('needs_multi_step', False)}`")
    st.write(f"识别实体：{entities or '无'}")
    st.write(f"过滤条件：{filters or '无'}")
    if intent.get("reason"):
        st.caption(intent["reason"])


# ------------------------------------------------------------------
# Page config & session state
# ------------------------------------------------------------------

st.set_page_config(page_title="KG-QA PoC", page_icon="🔎", layout="wide")

scenarios = load_test_scenarios()
total_cases = sum(len(v) for v in scenarios.values())

if "query_payload" not in st.session_state:
    st.session_state.query_payload = None
if "query_error" not in st.session_state:
    st.session_state.query_error = ""
if "seed_message" not in st.session_state:
    st.session_state.seed_message = ""
if "health_payload" not in st.session_state:
    st.session_state.health_payload = None
if "schema_payload" not in st.session_state:
    st.session_state.schema_payload = None
if "health_error" not in st.session_state:
    st.session_state.health_error = ""
if "schema_error" not in st.session_state:
    st.session_state.schema_error = ""
if "question_input" not in st.session_state:
    default_q = ""
    for case in scenarios.get("baseline", []):
        if case["id"] == "S4-2":
            default_q = case["question"]
            break
    st.session_state.question_input = default_q
if "selected_case_id" not in st.session_state:
    st.session_state.selected_case_id = "S4-2"
if st.session_state.health_payload is None and not st.session_state.health_error:
    refresh_system_status()

# ------------------------------------------------------------------
# Title
# ------------------------------------------------------------------

st.title("知识图谱智能问答 PoC")
st.caption(f"当前 API 地址：[${API_BASE_URL}]({API_BASE_URL})".replace("$", ""))

# ------------------------------------------------------------------
# Sidebar — test case selector
# ------------------------------------------------------------------

with st.sidebar:
    st.header("测试用例")
    st.caption(f"共 {total_cases} 条用例，来自 test_scenarios.yaml")

    group_keys = [k for k in _GROUP_META if k in scenarios]

    if group_keys:
        selected_group = st.selectbox(
            "用例分组",
            group_keys,
            format_func=lambda k: f"{_GROUP_META[k]['label']} ({len(scenarios.get(k, []))})",
            key="_sel_group",
        )
        st.caption(_GROUP_META[selected_group]["desc"])

        cases = scenarios.get(selected_group, [])
        case_map: dict[str, dict[str, Any]] = {c["id"]: c for c in cases}

        selected_id = st.selectbox(
            "选择用例",
            [c["id"] for c in cases],
            format_func=lambda cid: f"{cid} | {case_map[cid]['question']}",
            key="_sel_case",
        )

        case = case_map.get(selected_id)
        if case:
            with st.container(border=True):
                st.markdown(f"**{case['id']}**")
                st.write(case["question"])
                tags: list[str] = []
                must = case.get("must_include")
                if must:
                    tags.append(f"期望包含: {', '.join(must)}")
                if case.get("allow_empty"):
                    tags.append("允许空结果")
                if tags:
                    st.caption(" | ".join(tags))

            if st.button("填入此用例", use_container_width=True, type="primary"):
                st.session_state.question_input = case["question"]
                st.session_state.selected_case_id = case["id"]
    else:
        st.warning("未找到测试用例文件 (tests/test_scenarios.yaml)。")

    st.divider()

    st.caption("也可直接在右侧输入框中输入自定义问题。")

    st.divider()

    if st.button("导入种子数据", use_container_width=True):
        try:
            with st.spinner("正在导入种子数据..."):
                request_seed_load()
            st.session_state.seed_message = "种子数据导入完成。"
            fetch_health.clear()
            fetch_schema_summary.clear()
            refresh_system_status()
        except Exception as exc:
            st.session_state.seed_message = f"导入失败: {exc}"

    if st.button("刷新系统状态", use_container_width=True):
        fetch_health.clear()
        fetch_schema_summary.clear()
        refresh_system_status()

# ------------------------------------------------------------------
# Status bar
# ------------------------------------------------------------------

health_error = st.session_state.health_error
schema_error = st.session_state.schema_error
health_payload = st.session_state.health_payload
schema_payload = st.session_state.schema_payload

info_cols = st.columns(4)
with info_cols[0]:
    if health_payload:
        st.metric("API 状态", health_payload.get("status", "unknown"))
    else:
        st.metric("API 状态", "unreachable")
with info_cols[1]:
    st.metric("数据集", health_payload.get("dataset", "-") if health_payload else "-")
with info_cols[2]:
    st.metric("实体数", schema_payload.get("entity_count", "-") if schema_payload else "-")
with info_cols[3]:
    st.metric("关系数", schema_payload.get("relationship_count", "-") if schema_payload else "-")

if health_error:
    st.error(f"API 健康检查失败：{health_error}")
if schema_error:
    st.error(f"Schema 摘要获取失败：{schema_error}")

if st.session_state.seed_message:
    if "失败" in st.session_state.seed_message:
        st.error(st.session_state.seed_message)
    else:
        st.success(st.session_state.seed_message)

# ------------------------------------------------------------------
# Query input form
# ------------------------------------------------------------------

with st.form("query_form", clear_on_submit=False):
    st.text_area(
        "请输入问题",
        key="question_input",
        height=100,
        help="可从左侧选择测试用例自动填入，也可直接输入自定义问题。",
    )
    run_query = st.form_submit_button("执行查询", type="primary", use_container_width=True)

case_id = st.session_state.get("selected_case_id", "")
if case_id:
    st.caption(f"当前选中用例：{case_id}")

# ------------------------------------------------------------------
# Query execution
# ------------------------------------------------------------------

if run_query:
    question = st.session_state.question_input.strip()
    if not question:
        st.session_state.query_error = "请输入问题后再执行查询。"
        st.session_state.query_payload = None
    else:
        try:
            with st.spinner("正在执行知识图谱问答链路，请稍候..."):
                st.session_state.query_payload = request_query(question)
                st.session_state.query_error = ""
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            try:
                detail = exc.response.json().get("detail", detail)
            except Exception:
                pass
            st.session_state.query_error = f"查询失败: {detail}"
            st.session_state.query_payload = None
        except Exception as exc:
            st.session_state.query_error = f"查询失败: {exc}"
            st.session_state.query_payload = None

# ------------------------------------------------------------------
# Results display
# ------------------------------------------------------------------

if st.session_state.query_error:
    st.error(st.session_state.query_error)

payload = st.session_state.query_payload
if payload:
    trace = payload.get("trace", {})
    overview_cols = st.columns(5)
    with overview_cols[0]:
        st.metric("意图", payload.get("intent", "-"))
    with overview_cols[1]:
        st.metric("执行策略", payload.get("strategy", "-"))
    with overview_cols[2]:
        st.metric("总耗时", f"{payload.get('latency_ms', 0)} ms")
    with overview_cols[3]:
        st.metric("结果行数", trace.get("query_row_count", len(payload.get("result_preview", []))))
    with overview_cols[4]:
        st.metric("LLM 阶段", sum(1 for s in ["intent", "plan", "cypher", "answer"] if trace.get(s, {}).get("source") == "llm"))

    top_left, top_right = st.columns([1.15, 1])
    with top_left:
        st.subheader("最终回答")
        st.markdown(payload.get("answer", ""))
        st.subheader("结果预览")
        st.dataframe(payload.get("result_preview", []), use_container_width=True)
    with top_right:
        st.subheader("意图识别")
        render_intent(trace)

    st.subheader("执行轨迹")
    render_trace_summary(trace)

    plan_col, cypher_col = st.columns([1, 1])
    with plan_col:
        st.subheader("执行计划")
        render_plan(payload.get("plan"))
    with cypher_col:
        st.subheader("Cypher")
        if payload.get("cypher"):
            st.code(payload["cypher"], language="cypher")
        else:
            st.info("当前请求未生成 Cypher。")

    with st.expander("原始 Trace JSON", expanded=False):
        st.json(trace)

    with st.expander("Schema 摘要", expanded=False):
        if schema_payload:
            st.json(schema_payload)
        else:
            st.info("当前无法获取 schema 摘要。")
