from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE_URL = os.getenv("KGQA_API_BASE_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="KG-QA PoC", page_icon="🔎", layout="wide")
st.title("知识图谱智能问答 PoC")
st.caption(f"当前 API 地址：{API_BASE_URL}")

if "query_payload" not in st.session_state:
    st.session_state.query_payload = None
if "query_error" not in st.session_state:
    st.session_state.query_error = ""
if "seed_message" not in st.session_state:
    st.session_state.seed_message = ""

with st.form("query_form", clear_on_submit=False):
    question = st.text_input("请输入问题", value="万科的项目分别用了哪些品牌的冷水机组？")
    run_query = st.form_submit_button("执行查询", type="primary", use_container_width=True)

if st.button("导入种子数据", use_container_width=True):
    try:
        with st.spinner("正在导入种子数据..."):
            response = httpx.post(f"{API_BASE_URL}/seed/load", timeout=120.0)
            response.raise_for_status()
        st.session_state.seed_message = "种子数据导入完成。"
    except Exception as exc:
        st.session_state.seed_message = f"导入失败: {exc}"

if st.session_state.seed_message:
    if "失败" in st.session_state.seed_message:
        st.error(st.session_state.seed_message)
    else:
        st.success(st.session_state.seed_message)

if run_query:
    if not question.strip():
        st.session_state.query_error = "请输入问题后再执行查询。"
        st.session_state.query_payload = None
    else:
        try:
            with st.spinner("正在查询知识图谱，请稍候..."):
                response = httpx.post(
                    f"{API_BASE_URL}/query",
                    json={"question": question},
                    timeout=120.0,
                )
                response.raise_for_status()
                st.session_state.query_payload = response.json()
                st.session_state.query_error = ""
        except Exception as exc:
            st.session_state.query_error = f"查询失败: {exc}"
            st.session_state.query_payload = None

if st.session_state.query_error:
    st.error(st.session_state.query_error)

payload = st.session_state.query_payload
if payload:
    left, right = st.columns([1, 1])
    with left:
        st.subheader("执行信息")
        st.json(
            {
                "intent": payload["intent"],
                "strategy": payload["strategy"],
                "latency_ms": payload["latency_ms"],
            }
        )
        if payload.get("cypher"):
            st.subheader("Cypher")
            st.code(payload["cypher"], language="cypher")
        if payload.get("plan"):
            st.subheader("Plan")
            st.json(payload["plan"])
    with right:
        st.subheader("最终回答")
        st.markdown(payload["answer"])
        st.subheader("结果预览")
        st.dataframe(payload["result_preview"], use_container_width=True)
