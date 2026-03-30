from __future__ import annotations

import html
from pathlib import Path

import yaml

from kgqa.config import get_settings
from kgqa.service import KGQAService


def run_evaluation() -> Path:
    settings = get_settings()
    scenarios = yaml.safe_load(settings.evaluation_file.read_text(encoding="utf-8"))
    service = KGQAService(settings)
    rows = []

    for group_name in ("baseline", "challenge"):
        for case in scenarios.get(group_name, []):
            rows.append(run_case(service, group_name, case))

    total = len(rows)
    passed = sum(1 for row in rows if row["generalization_pass"])
    score = round((passed / total) * 100, 2) if total else 0.0
    report = _build_html(rows, passed, total, score)
    settings.report_file.write_text(report, encoding="utf-8")
    return settings.report_file


def run_case(service: KGQAService, group_name: str, case: dict[str, object]) -> dict[str, object]:
    try:
        response = service.process_question(str(case["question"]))
        text = response.answer + "\n" + str(response.result_preview)
        generalization_pass = all(keyword in text for keyword in case.get("must_include", []))
        if case.get("allow_empty"):
            generalization_pass = "图谱中未找到相关信息" in response.answer

        llm_stage_used = ",".join(
            name
            for name, source in (
                ("intent", response.trace.intent.source.value),
                ("plan", response.trace.plan.source.value),
                ("cypher", response.trace.cypher.source.value),
                ("answer", response.trace.answer.source.value),
            )
            if source == "llm"
        )
        rule_fallback_used = "yes" if response.trace.fallbacks else "no"
        query_success = response.trace.query_success
        answer_quality = "pass" if response.answer and "编造" not in response.answer else "fail"

        return {
            "group": group_name,
            "id": case["id"],
            "question": case["question"],
            "intent": response.intent.value,
            "strategy": response.strategy,
            "llm_stage_used": llm_stage_used or "none",
            "rule_fallback_used": rule_fallback_used,
            "query_success": query_success,
            "answer_quality": answer_quality,
            "generalization_pass": generalization_pass,
            "latency_ms": response.latency_ms,
            "answer": response.answer,
        }
    except Exception as exc:
        return {
            "group": group_name,
            "id": case["id"],
            "question": case["question"],
            "intent": "ERROR",
            "strategy": "ERROR",
            "llm_stage_used": "error",
            "rule_fallback_used": "error",
            "query_success": False,
            "answer_quality": "fail",
            "generalization_pass": False,
            "latency_ms": -1,
            "answer": str(exc),
        }


def _build_html(rows: list[dict[str, object]], passed: int, total: int, score: float) -> str:
    body = []
    for row in rows:
        pass_color = "#d1fae5" if row["generalization_pass"] else "#fee2e2"
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row['group']))}</td>"
            f"<td>{html.escape(str(row['id']))}</td>"
            f"<td>{html.escape(str(row['question']))}</td>"
            f"<td>{html.escape(str(row['intent']))}</td>"
            f"<td>{html.escape(str(row['strategy']))}</td>"
            f"<td>{html.escape(str(row['llm_stage_used']))}</td>"
            f"<td>{html.escape(str(row['rule_fallback_used']))}</td>"
            f"<td>{html.escape(str(row['query_success']))}</td>"
            f"<td>{html.escape(str(row['answer_quality']))}</td>"
            f"<td style='background:{pass_color}'>{html.escape(str(row['generalization_pass']))}</td>"
            f"<td>{html.escape(str(row['latency_ms']))}</td>"
            f"<td>{html.escape(str(row['answer']))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>KG-QA PoC Evaluation</title>
  <style>
    body {{ font-family: 'Segoe UI', sans-serif; margin: 24px; background: #f7fafc; color: #1f2937; }}
    h1 {{ margin-bottom: 8px; }}
    .summary {{ display: flex; gap: 16px; margin-bottom: 24px; }}
    .card {{ background: white; padding: 16px; border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08); min-width: 160px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 10px; vertical-align: top; text-align: left; }}
    th {{ background: #eff6ff; }}
  </style>
</head>
<body>
  <h1>KG-QA PoC 评估报告</h1>
  <div class="summary">
    <div class="card"><strong>总用例</strong><br />{total}</div>
    <div class="card"><strong>通过数</strong><br />{passed}</div>
    <div class="card"><strong>通过率</strong><br />{score}%</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>组别</th>
        <th>ID</th>
        <th>问题</th>
        <th>意图</th>
        <th>策略</th>
        <th>LLM阶段</th>
        <th>规则回退</th>
        <th>查询成功</th>
        <th>回答质量</th>
        <th>泛化通过</th>
        <th>耗时(ms)</th>
        <th>回答摘要</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body)}
    </tbody>
  </table>
</body>
</html>
"""


if __name__ == "__main__":
    path = run_evaluation()
    print(f"Report generated at {path}")
