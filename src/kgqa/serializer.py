from __future__ import annotations

from typing import Any

from kgqa.models import IntentType, SerializedResult


class ResultSerializer:
    def serialize(self, rows: list[dict[str, Any]], question: str, intent: IntentType) -> SerializedResult:
        if not rows:
            return SerializedResult(format="empty", markdown="图谱中未找到相关信息。", preview=[], row_count=0)

        if len(rows) == 1 and len(rows[0]) > 2 and not any(keyword in question for keyword in ["区别", "对比", "占比", "最多", "最大", "平均"]):
            markdown = self._as_key_value(rows[0])
            return SerializedResult(format="key_value", markdown=markdown, preview=rows[:5], row_count=1)

        if any(keyword in question for keyword in ["区别", "对比", "占比", "最多", "最大", "平均"]) or intent in {IntentType.AGGREGATION, IntentType.MULTI_STEP}:
            markdown = self._as_table(rows)
            return SerializedResult(format="markdown_table", markdown=markdown, preview=rows[:10], row_count=len(rows))

        if any(isinstance(value, list) for row in rows for value in row.values()):
            markdown = self._as_grouped_list(rows)
            return SerializedResult(format="numbered_list", markdown=markdown, preview=rows[:10], row_count=len(rows))

        markdown = self._as_table(rows)
        return SerializedResult(format="table", markdown=markdown, preview=rows[:10], row_count=len(rows))

    @staticmethod
    def _as_key_value(row: dict[str, Any]) -> str:
        return "\n".join(f"- **{key}**: {value}" for key, value in row.items())

    @staticmethod
    def _as_table(rows: list[dict[str, Any]]) -> str:
        headers = list(rows[0].keys())
        header_line = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"
        body = []
        for row in rows:
            values = []
            for header in headers:
                value = row.get(header, "")
                if isinstance(value, list):
                    value = "、".join(str(item) for item in value)
                values.append(str(value))
            body.append("| " + " | ".join(values) + " |")
        return "\n".join([header_line, separator, *body])

    @staticmethod
    def _as_grouped_list(rows: list[dict[str, Any]]) -> str:
        lines = []
        for index, row in enumerate(rows, start=1):
            parts = []
            for key, value in row.items():
                if isinstance(value, list):
                    value = "、".join(str(item) for item in value)
                parts.append(f"{key}: {value}")
            lines.append(f"{index}. " + "；".join(parts))
        return "\n".join(lines)

