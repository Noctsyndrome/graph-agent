from __future__ import annotations

from typing import Any

from kgqa.models import SerializedResult


class ResultSerializer:
    AGGREGATION_KEYWORDS = (
        "count",
        "cnt",
        "avg",
        "sum",
        "max",
        "min",
        "total",
        "rank",
        "ratio",
        "percent",
        "数量",
        "总",
        "平均",
        "占比",
        "最大",
        "最小",
        "排名",
    )

    def serialize(self, rows: list[dict[str, Any]], question: str = "", intent_hint: Any | None = None) -> SerializedResult:
        del question, intent_hint
        if not rows:
            return SerializedResult(format="empty", markdown="图谱中未找到相关信息。", preview=[], row_count=0)

        normalized_rows = self._normalize_rows(rows)
        preview = normalized_rows[:10]
        headers = list(normalized_rows[0].keys()) if normalized_rows else []

        if self._is_key_value_result(normalized_rows):
            markdown = self._as_key_value(normalized_rows[0])
            return SerializedResult(format="key_value", markdown=markdown, preview=preview, row_count=len(normalized_rows))

        if self._has_sequence_values(normalized_rows):
            markdown = self._as_grouped_list(normalized_rows)
            return SerializedResult(format="numbered_list", markdown=markdown, preview=preview, row_count=len(normalized_rows))

        if self._looks_like_aggregation(headers):
            markdown = self._as_table(normalized_rows)
            return SerializedResult(format="markdown_table", markdown=markdown, preview=preview, row_count=len(normalized_rows))

        markdown = self._as_table(normalized_rows)
        return SerializedResult(format="table", markdown=markdown, preview=preview, row_count=len(normalized_rows))

    def _normalize_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            normalized_rows.append(self._normalize_row(row))
        return normalized_rows

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if len(row) == 1:
            only_value = next(iter(row.values()))
            if self._is_node_payload(only_value):
                return {
                    key: self._normalize_cell(value)
                    for key, value in dict(only_value.get("properties", {})).items()
                }
        return {
            key: self._normalize_cell(value)
            for key, value in row.items()
        }

    def _normalize_cell(self, value: Any) -> Any:
        if self._is_node_payload(value):
            return {
                key: self._normalize_cell(item)
                for key, item in dict(value.get("properties", {})).items()
            }
        if isinstance(value, list):
            return [self._normalize_cell(item) for item in value]
        if isinstance(value, dict):
            return {key: self._normalize_cell(item) for key, item in value.items()}
        return value

    @staticmethod
    def _is_node_payload(value: Any) -> bool:
        return isinstance(value, dict) and value.get("__type__") == "node" and isinstance(value.get("properties"), dict)

    @staticmethod
    def _has_sequence_values(rows: list[dict[str, Any]]) -> bool:
        return any(isinstance(value, list) for row in rows for value in row.values())

    def _looks_like_aggregation(self, headers: list[str]) -> bool:
        normalized = [header.lower() for header in headers]
        return any(keyword in header for header in normalized for keyword in self.AGGREGATION_KEYWORDS)

    def _is_key_value_result(self, rows: list[dict[str, Any]]) -> bool:
        if len(rows) != 1:
            return False
        row = rows[0]
        if len(row) < 2:
            return False
        if self._has_sequence_values(rows):
            return False
        headers = list(row.keys())
        return not self._looks_like_aggregation(headers)

    @staticmethod
    def _as_key_value(row: dict[str, Any]) -> str:
        return "\n".join(f"- **{key}**: {ResultSerializer._render_value(value)}" for key, value in row.items())

    @staticmethod
    def _as_table(rows: list[dict[str, Any]]) -> str:
        headers = list(rows[0].keys())
        header_line = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"
        body = []
        for row in rows:
            values = [ResultSerializer._render_value(row.get(header, "")) for header in headers]
            body.append("| " + " | ".join(values) + " |")
        return "\n".join([header_line, separator, *body])

    @staticmethod
    def _as_grouped_list(rows: list[dict[str, Any]]) -> str:
        lines = []
        for index, row in enumerate(rows, start=1):
            parts = [f"{key}: {ResultSerializer._render_value(value)}" for key, value in row.items()]
            lines.append(f"{index}. " + "；".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _render_value(value: Any) -> str:
        if isinstance(value, list):
            return "、".join(str(item) for item in value)
        if isinstance(value, dict):
            return ", ".join(f"{key}={item}" for key, item in value.items())
        return str(value)
