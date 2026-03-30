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
    passed = 0

    for case in scenarios["cases"]:
        try:
            response = service.process_question(case["question"])
            text = response.answer + "\n" + str(response.result_preview)
            ok = all(keyword in text for keyword in case.get("must_include", []))
            if case.get("allow_empty"):
                ok = "图谱中未找到相关信息" in response.answer
            rows.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "intent": response.intent.value,
                    "strategy": response.strategy,
                    "pass": ok,
                    "answer": response.answer,
                    "latency_ms": response.latency_ms,
                }
            )
            if ok:
                passed += 1
        except Exception as exc:
            rows.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "intent": "ERROR",
                    "strategy": "ERROR",
                    "pass": False,
                    "answer": str(exc),
                    "latency_ms": -1,
                }
            )

    total = len(rows)
    score = round((passed / total) * 100, 2) if total else 0.0
    report = _build_html(rows, passed, total, score)
    settings.report_file.write_text(report, encoding="utf-8")
    return settings.report_file


def _build_html(rows: list[dict[str, object]], passed: int, total: int, score: float) -> str:
    tr = []
    for row in rows:
        color = "#d1fae5" if row["pass"] else "#fee2e2"
        tr.append(
            "<tr>"
            f"<td>{html.escape(str(row['id']))}</td>"
            f"<td>{html.escape(str(row['question']))}</td>"
            f"<td>{html.escape(str(row['intent']))}</td>"
            f"<td>{html.escape(str(row['strategy']))}</td>"
            f"<td style='background:{color}'>{html.escape(str(row['pass']))}</td>"
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
        <th>ID</th>
        <th>问题</th>
        <th>意图</th>
        <th>策略</th>
        <th>是否通过</th>
        <th>耗时(ms)</th>
        <th>回答摘要</th>
      </tr>
    </thead>
    <tbody>
      {''.join(tr)}
    </tbody>
  </table>
</body>
</html>
"""


if __name__ == "__main__":
    path = run_evaluation()
    print(f"Report generated at {path}")

