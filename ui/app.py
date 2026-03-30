from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

import httpx
import streamlit as st

API_BASE_URL = os.getenv("KGQA_API_BASE_URL", "http://localhost:8000").rstrip("/")

PRESET_QUESTIONS = [
    {
        "category": "单域查询",
        "label": "冷水机组型号列表",
        "question": "冷水机组有哪些型号？",
        "purpose": "验证设备型号清单和基础列表查询。",
    },
    {
        "category": "单域查询",
        "label": "高能效冷水机组",
        "question": "能效比在6以上的冷水机组有哪些？",
        "purpose": "验证数值过滤与排序。",
    },
    {
        "category": "单域查询",
        "label": "查看设备参数",
        "question": "开利 30XA-300 的详细参数是什么？",
        "purpose": "验证指定型号属性查询。",
    },
    {
        "category": "跨域查询",
        "label": "万科项目品牌分布",
        "question": "万科的项目分别用了哪些品牌的冷水机组？",
        "purpose": "验证客户-项目-安装-型号的跨域链路。",
    },
    {
        "category": "跨域查询",
        "label": "深圳项目设备清单",
        "question": "深圳区域的项目都用了什么设备？",
        "purpose": "验证按城市汇总项目设备。",
    },
    {
        "category": "聚合统计",
        "label": "开利设备用量最多客户",
        "question": "哪个客户使用开利设备最多？",
        "purpose": "验证聚合与排序。",
    },
    {
        "category": "聚合统计",
        "label": "品牌占比",
        "question": "各品牌设备在所有项目中的占比是多少？",
        "purpose": "验证占比和聚合输出。",
    },
    {
        "category": "多步推理",
        "label": "最低能效设备 + 替代方案",
        "question": "万科的商业项目中，能效比最低的设备是哪台？有没有可替代方案？",
        "purpose": "验证多步规划、步骤衔接和替代关系查询。",
    },
    {
        "category": "多步推理",
        "label": "R-22 合规检查",
        "question": "2023年后的项目中，有没有还在用R-22制冷剂设备的？",
        "purpose": "验证时间过滤和合规排查场景。",
    },
    {
        "category": "挑战问题",
        "label": "同义改写",
        "question": "万科商业项目里最差 COP 的设备是什么，有替代型号吗？",
        "purpose": "验证泛化问法下的多步处理。",
    },
]


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

    fallback_rows = trace.get("fallbacks", [])
    if fallback_rows:
        st.warning(f"本次请求发生 {len(fallback_rows)} 次 fallback。")
        st.dataframe(fallback_rows, use_container_width=True, hide_index=True)
    else:
        st.success("本次请求未发生 fallback。")


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


st.set_page_config(page_title="KG-QA PoC", page_icon="🔎", layout="wide")

if "query_payload" not in st.session_state:
    st.session_state.query_payload = None
if "query_error" not in st.session_state:
    st.session_state.query_error = ""
if "seed_message" not in st.session_state:
    st.session_state.seed_message = ""
if "question_input" not in st.session_state:
    st.session_state.question_input = PRESET_QUESTIONS[7]["question"]
if "selected_preset" not in st.session_state:
    st.session_state.selected_preset = PRESET_QUESTIONS[7]["label"]

st.title("知识图谱智能问答 PoC")
st.caption(f"当前 API 地址：[${API_BASE_URL}]({API_BASE_URL})".replace("$", ""))

with st.sidebar:
    st.header("预置问题")
    grouped_presets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in PRESET_QUESTIONS:
        grouped_presets[item["category"]].append(item)

    for category, items in grouped_presets.items():
        st.subheader(category)
        for item in items:
            if st.button(item["label"], use_container_width=True, key=f"preset_{item['label']}"):
                st.session_state.question_input = item["question"]
                st.session_state.selected_preset = item["label"]
        with st.expander(f"{category}说明", expanded=False):
            for item in items:
                st.markdown(f"**{item['label']}**")
                st.caption(item["purpose"])

    st.divider()
    if st.button("导入种子数据", use_container_width=True):
        try:
            with st.spinner("正在导入种子数据..."):
                request_seed_load()
            st.session_state.seed_message = "种子数据导入完成。"
            fetch_health.clear()
            fetch_schema_summary.clear()
        except Exception as exc:
            st.session_state.seed_message = f"导入失败: {exc}"

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

with st.form("query_form", clear_on_submit=False):
    st.text_area(
        "请输入问题",
        key="question_input",
        height=100,
        help="可以直接输入自然语言问题，也可以点击左侧预置问题快速填充。",
    )
    run_query = st.form_submit_button("执行查询", type="primary", use_container_width=True)

st.caption(f"当前选中预置问题：{st.session_state.selected_preset}")

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
        st.metric("Fallback 次数", len(trace.get("fallbacks", [])))

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
