from __future__ import annotations

import argparse
import html
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from kgqa.agent import get_kgqa_agent
from kgqa.config import Settings, get_settings
from kgqa.models import ChatRequest
from kgqa.scenario import build_scenario_settings, get_scenario_definition
from kgqa.schema import SchemaRegistry
from kgqa.session import clear_sessions, get_session_payload


def run_evaluation(scenario_id: str | None = None) -> Path:
    base_settings = get_settings()
    scenario = get_scenario_definition(scenario_id)
    settings = build_scenario_settings(base_settings, scenario)
    scenarios = yaml.safe_load(settings.evaluation_file.read_text(encoding="utf-8"))
    rows = _run_all(settings, scenario.scenario_id, scenarios)
    report = _build_html(rows)
    report_path = settings.report_file.with_name(f"{settings.report_file.stem}-{scenario.scenario_id}{settings.report_file.suffix}")
    report_path.write_text(report, encoding="utf-8")
    return report_path


def _run_all(
    settings: Settings,
    scenario_id: str,
    scenarios: dict,
) -> list[dict[str, object]]:
    clear_sessions()
    agent = get_kgqa_agent(get_settings(), get_scenario_definition(scenario_id))
    rows: list[dict[str, object]] = []
    all_cases: list[tuple[str, dict]] = []
    for group_name in ("baseline", "challenge", "generalization"):
        for case in scenarios.get(group_name, []):
            all_cases.append((group_name, case))
    total = len(all_cases)
    for idx, (group_name, case) in enumerate(all_cases, 1):
        print(f"  ({idx}/{total}) {case['id']}: {case['question'][:40]}...", flush=True)
        result = run_case(agent, settings, scenario_id, group_name, case)
        status = "PASS" if result["generalization_pass"] else "FAIL"
        latency = result["latency_ms"]
        print(f"           → {status}  {latency}ms", flush=True)
        rows.append(result)
    return rows


def run_case(agent, settings: Settings, scenario_id: str, group_name: str, case: dict[str, object]) -> dict[str, object]:
    session_id = f"eval-{case['id']}-{uuid.uuid4()}"
    try:
        started = time.perf_counter()
        stream_events = list(
            agent.stream_chat(
                ChatRequest(
                    threadId=session_id,
                    scenarioId=scenario_id,
                    messages=[{"id": "u1", "role": "user", "content": str(case["question"])}],
                    state={},
                )
            )
        )
        payload = get_session_payload(session_id)
        if payload is None:
            raise RuntimeError("Agent session payload not found after chat run.")
        answer = _latest_assistant_answer(payload.messages)
        result_preview = _latest_result_preview(payload.state)
        generalization_pass = _matches_expectations(settings, answer, result_preview, case)
        if case.get("allow_empty"):
            generalization_pass = "图谱中未找到相关信息" in answer

        tool_history = list(payload.state.get("toolHistory", [])) if isinstance(payload.state, dict) else []
        query_success = bool(result_preview) or "图谱中未找到相关信息" in answer
        answer_quality = "pass" if answer and "编造" not in answer else "fail"
        run_error = _run_error_message(stream_events)
        latency_ms = int((time.perf_counter() - started) * 1000)

        return {
            "group": group_name,
            "id": case["id"],
            "question": case["question"],
            "intent": "AGENT",
            "strategy": "chat_agent",
            "llm_stage_used": "agent",
            "query_success": query_success,
            "answer_quality": answer_quality,
            "generalization_pass": generalization_pass,
            "latency_ms": latency_ms,
            "answer": answer if not run_error else run_error,
            "tool_count": len(tool_history),
        }
    except Exception as exc:
        return {
            "group": group_name,
            "id": case["id"],
            "question": case["question"],
            "intent": "ERROR",
            "strategy": "ERROR",
            "llm_stage_used": "error",
            "query_success": False,
            "answer_quality": "fail",
            "generalization_pass": False,
            "latency_ms": -1,
            "answer": str(exc),
            "tool_count": 0,
        }


def _matches_expectations(
    settings: Settings,
    answer: str,
    preview: list[dict[str, Any]],
    case: dict[str, object],
) -> bool:
    text = answer + "\n" + str(preview)
    schema = SchemaRegistry(settings)
    alias_lookup = _build_alias_lookup(schema.schema.get("column_aliases", {}))
    return all(_keyword_matches(keyword, text, alias_lookup) for keyword in case.get("must_include", []))


def _latest_assistant_answer(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return str(message.get("content", "")).strip()
    return ""


def _latest_result_preview(state: dict[str, Any]) -> list[dict[str, Any]]:
    latest = state.get("latestResult", {})
    if isinstance(latest, dict):
        preview = latest.get("preview", latest.get("payload", []))
        if isinstance(preview, list):
            return preview
    return []


def _run_error_message(stream_events: list[str]) -> str | None:
    for raw in stream_events:
        if not raw.startswith("data: "):
            continue
        payload = json.loads(raw[6:])
        if payload.get("type") == "RUN_ERROR":
            return str(payload.get("message", "Agent run failed."))
    return None


def _build_alias_lookup(column_aliases: dict[str, dict[str, list[str]]]) -> dict[str, set[str]]:
    lookup: dict[str, set[str]] = {}
    for canonical, alias_def in column_aliases.items():
        alias_group = {canonical}
        alias_group.update(str(item) for item in alias_def.get("zh", []))
        alias_group.update(str(item) for item in alias_def.get("en", []))
        for alias in alias_group:
            lookup[alias] = set(alias_group)
    return lookup


def _keyword_matches(keyword: object, text: str, alias_lookup: dict[str, set[str]]) -> bool:
    target = str(keyword)
    if target in text:
        return True
    return any(alias in text for alias in alias_lookup.get(target, set()))


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _group_stats(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Compute per-group statistics."""
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["group"]), []).append(row)

    stats: dict[str, dict[str, object]] = {}
    for group_name, group_rows in groups.items():
        total = len(group_rows)
        passed = sum(1 for r in group_rows if r["generalization_pass"])
        latencies = [int(r["latency_ms"]) for r in group_rows if int(r["latency_ms"]) > 0]
        stats[group_name] = {
            "total": total,
            "passed": passed,
            "rate": round(passed / total * 100, 1) if total else 0,
            "latency_p50": round(statistics.median(latencies)) if latencies else 0,
            "latency_p90": round(sorted(latencies)[int(len(latencies) * 0.9)] if latencies else 0),
            "latency_max": max(latencies) if latencies else 0,
        }
    return stats


def _stage_stats(rows: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    """Compute coarse agent-vs-other stats for the current agent-only runner."""
    stages = {
        "agent": {"llm": 0, "other": 0},
        "tools": {"llm": 0, "other": 0},
    }
    for row in rows:
        stages["agent"]["llm"] += 1
        if int(row.get("tool_count", 0)) > 0:
            stages["tools"]["llm"] += 1
        else:
            stages["tools"]["other"] += 1
    return stages


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

_CSS = """\
body { font-family: 'Segoe UI', sans-serif; margin: 24px; background: #f7fafc; color: #1f2937; }
h1 { margin-bottom: 4px; }
h2 { margin-top: 32px; color: #1e40af; }
.mode-tag { display: inline-block; padding: 2px 10px; border-radius: 6px; font-size: 13px; margin-left: 8px; background: #fef3c7; color: #92400e; }
.summary { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.card { background: white; padding: 16px; border-radius: 12px; box-shadow: 0 2px 8px rgba(15,23,42,.08); min-width: 140px; }
.card strong { display: block; margin-bottom: 4px; font-size: 13px; color: #6b7280; }
.card .value { font-size: 22px; font-weight: 700; }
.card .sub { font-size: 12px; color: #9ca3af; }
table { width: 100%; border-collapse: collapse; background: white; margin-bottom: 24px; }
th, td { border: 1px solid #e5e7eb; padding: 8px 10px; vertical-align: top; text-align: left; font-size: 13px; }
th { background: #eff6ff; white-space: nowrap; }
.pass { background: #d1fae5; }
.fail { background: #fee2e2; }
.stage-table td:nth-child(2), .stage-table td:nth-child(3) { text-align: right; }
"""


def _build_html(rows: list[dict[str, object]]) -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r["generalization_pass"])
    score = round(passed / total * 100, 1) if total else 0
    group_stats = _group_stats(rows)
    stage = _stage_stats(rows)

    latencies = [int(r["latency_ms"]) for r in rows if int(r["latency_ms"]) > 0]
    p50 = round(statistics.median(latencies)) if latencies else 0
    error_count = sum(1 for r in rows if r["intent"] == "ERROR")

    # Summary cards
    cards = f"""\
<div class="summary">
  <div class="card"><strong>总用例</strong><div class="value">{total}</div></div>
  <div class="card"><strong>通过数</strong><div class="value">{passed}</div></div>
  <div class="card"><strong>通过率</strong><div class="value">{score}%</div></div>
  <div class="card"><strong>错误数</strong><div class="value">{error_count}</div></div>
  <div class="card"><strong>延迟 P50</strong><div class="value">{p50 / 1000:.1f}s</div></div>
</div>"""

    # Per-group table
    group_rows_html = ""
    for gname, gs in group_stats.items():
        group_rows_html += (
            f"<tr><td>{html.escape(gname)}</td><td>{gs['total']}</td><td>{gs['passed']}</td>"
            f"<td><b>{gs['rate']}%</b></td><td>{gs['latency_p50']}ms</td>"
            f"<td>{gs['latency_p90']}ms</td><td>{gs['latency_max']}ms</td></tr>"
        )
    group_table = f"""\
<h2>按组别统计</h2>
<table><thead><tr><th>组别</th><th>用例数</th><th>通过</th><th>通过率</th><th>P50</th><th>P90</th><th>Max</th></tr></thead>
<tbody>{group_rows_html}</tbody></table>"""

    # Stage LLM success table
    stage_rows_html = ""
    for sname in ("agent", "tools"):
        s = stage[sname]
        s_total = s["llm"] + s["other"]
        llm_pct = round(s["llm"] / s_total * 100, 1) if s_total else 0
        stage_rows_html += (
            f"<tr><td>{sname}</td><td>{s['llm']}</td>"
            f"<td>{s['other']}</td><td><b>{llm_pct}%</b></td></tr>"
        )
    stage_table = f"""\
<h2>各阶段 LLM 使用率</h2>
<table class="stage-table"><thead><tr><th>阶段</th><th>LLM</th><th>其他</th><th>LLM 占比</th></tr></thead>
<tbody>{stage_rows_html}</tbody></table>"""

    # Detail table
    detail_rows = _detail_rows_html(rows)

    return f"""\
<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><title>KG-QA PoC Evaluation</title><style>{_CSS}</style></head>
<body>
  <h1>KG-QA PoC 评估报告 <span class="mode-tag">纯 LLM 模式</span></h1>
  {cards}
  {group_table}
  {stage_table}
  <h2>用例明细</h2>
  <table><thead><tr>
    <th>组别</th><th>ID</th><th>问题</th><th>意图</th><th>策略</th>
    <th>LLM阶段</th><th>查询成功</th><th>回答质量</th>
    <th>泛化通过</th><th>耗时(ms)</th><th>回答摘要</th>
  </tr></thead><tbody>{detail_rows}</tbody></table>
</body></html>"""


def _detail_rows_html(rows: list[dict[str, object]]) -> str:
    body = []
    for row in rows:
        pass_color = "pass" if row["generalization_pass"] else "fail"
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row['group']))}</td>"
            f"<td>{html.escape(str(row['id']))}</td>"
            f"<td>{html.escape(str(row['question']))}</td>"
            f"<td>{html.escape(str(row['intent']))}</td>"
            f"<td>{html.escape(str(row['strategy']))}</td>"
            f"<td>{html.escape(str(row['llm_stage_used']))}</td>"
            f"<td>{html.escape(str(row['query_success']))}</td>"
            f"<td>{html.escape(str(row['answer_quality']))}</td>"
            f"<td class='{pass_color}'>{html.escape(str(row['generalization_pass']))}</td>"
            f"<td>{html.escape(str(row['latency_ms']))}</td>"
            f"<td>{html.escape(str(row['answer']))}</td>"
            "</tr>"
        )
    return "".join(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KG-QA PoC Evaluation")
    parser.add_argument("--scenario", default=None, help="Scenario id to evaluate, e.g. hvac or elevator")
    args = parser.parse_args()
    path = run_evaluation(scenario_id=args.scenario)
    print(f"Report generated at {path}")
