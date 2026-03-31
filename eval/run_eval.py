from __future__ import annotations

import argparse
import html
import os
import statistics
from pathlib import Path
from typing import Any

import yaml

from kgqa.config import Settings, get_settings
from kgqa.service import KGQAService, get_kgqa_service


def run_evaluation() -> Path:
    settings = get_settings()
    scenarios = yaml.safe_load(settings.evaluation_file.read_text(encoding="utf-8"))
    rows = _run_all(settings, scenarios)
    report = _build_html(rows)
    settings.report_file.write_text(report, encoding="utf-8")
    return settings.report_file


def _run_all(
    settings: Settings,
    scenarios: dict,
) -> list[dict[str, object]]:
    service = get_kgqa_service(settings)
    rows: list[dict[str, object]] = []
    all_cases: list[tuple[str, dict]] = []
    for group_name in ("baseline", "challenge", "generalization"):
        for case in scenarios.get(group_name, []):
            all_cases.append((group_name, case))
    total = len(all_cases)
    for idx, (group_name, case) in enumerate(all_cases, 1):
        print(f"  ({idx}/{total}) {case['id']}: {case['question'][:40]}...", flush=True)
        result = run_case(service, group_name, case)
        status = "PASS" if result["generalization_pass"] else "FAIL"
        latency = result["latency_ms"]
        print(f"           → {status}  {latency}ms", flush=True)
        rows.append(result)
    return rows


def run_case(service: KGQAService, group_name: str, case: dict[str, object]) -> dict[str, object]:
    try:
        response = service.process_question(str(case["question"]))
        generalization_pass = _matches_expectations(service, response.answer, response.result_preview, case)
        if case.get("allow_empty"):
            generalization_pass = "图谱中未找到相关信息" in response.answer

        llm_stages: dict[str, str] = {
            "intent": response.trace.intent.source.value,
            "plan": response.trace.plan.source.value,
            "cypher": response.trace.cypher.source.value,
            "answer": response.trace.answer.source.value,
        }
        llm_stage_used = ",".join(name for name, source in llm_stages.items() if source == "llm")
        query_success = response.trace.query_success
        answer_quality = "pass" if response.answer and "编造" not in response.answer else "fail"

        return {
            "group": group_name,
            "id": case["id"],
            "question": case["question"],
            "intent": response.intent.value,
            "strategy": response.strategy,
            "llm_stage_used": llm_stage_used or "none",
            "query_success": query_success,
            "answer_quality": answer_quality,
            "generalization_pass": generalization_pass,
            "latency_ms": response.latency_ms,
            "answer": response.answer,
            "stage_sources": llm_stages,
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
            "stage_sources": {},
        }


def _matches_expectations(
    service: KGQAService,
    answer: str,
    preview: list[dict[str, Any]],
    case: dict[str, object],
) -> bool:
    text = answer + "\n" + str(preview)
    alias_lookup = _build_alias_lookup(service.schema.schema.get("column_aliases", {}))
    return all(_keyword_matches(keyword, text, alias_lookup) for keyword in case.get("must_include", []))


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
    """Compute per-stage LLM success rate."""
    stages = {"intent": {"llm": 0, "other": 0},
              "plan": {"llm": 0, "other": 0},
              "cypher": {"llm": 0, "other": 0},
              "answer": {"llm": 0, "other": 0}}
    for row in rows:
        sources = row.get("stage_sources", {})
        for stage_name in stages:
            source = sources.get(stage_name, "none") if sources else "none"
            if source == "llm":
                stages[stage_name]["llm"] += 1
            else:
                stages[stage_name]["other"] += 1
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
    for sname in ("intent", "plan", "cypher", "answer"):
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
    args = parser.parse_args()
    path = run_evaluation()
    print(f"Report generated at {path}")
