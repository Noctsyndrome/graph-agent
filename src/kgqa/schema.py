from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kgqa.config import Settings
from kgqa.models import IntentType


class SchemaRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._schema = self._load_yaml(settings.schema_file)
        self._few_shots = self._load_yaml(settings.few_shots_file)

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    @property
    def few_shots(self) -> dict[str, Any]:
        return self._few_shots

    def render_schema_context(
        self,
        question: str,
        intent: IntentType | None = None,
        entities: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> str:
        entities = entities or []
        filters = filters or {}
        focus = self._infer_focus(question, entities)
        lines = ["## 图谱 Schema", ""]
        for entity in self._schema["entities"]:
            if focus and entity["name"] not in focus:
                continue
            field_text = ", ".join(f"{key}: {value}" for key, value in entity["properties"].items())
            lines.append(f"- {entity['name']}: {field_text}")
        lines.append("")
        lines.append("## 关系类型")
        for relation in self._schema["relationships"]:
            if focus and relation["from"] not in focus and relation["to"] not in focus:
                continue
            lines.append(f"- ({relation['from']})-[:{relation['name']}]->({relation['to']})")
        if intent:
            lines.append("")
            lines.append(f"## 典型路径（{intent.value}）")
            for path in self._schema["paths"].get(intent.value, []):
                lines.append(f"- {path}")
        if entities or filters:
            lines.append("")
            lines.append(f"## 问题中识别到的实体：{entities}")
            lines.append(f"## 问题中识别到的过滤条件：{filters}")
        return "\n".join(lines)

    def few_shots_for_intent(self, intent: IntentType, question: str | None = None) -> list[dict[str, str]]:
        cases = self._few_shots.get(intent.value, [])
        if not question:
            return cases
        text = question.replace(" ", "")
        scored: list[tuple[int, dict[str, str]]] = []
        for item in cases:
            score = 0
            sample = item["question"].replace(" ", "")
            if "冷水机组" in text and "冷水机组" in sample:
                score += 2
            if "项目" in text and "项目" in sample:
                score += 1
            if "R-22" in text and "R-22" in sample:
                score += 3
            if "占比" in text and "占比" in sample:
                score += 3
            if "替代" in text and "替代" in sample:
                score += 3
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = [item for _, item in scored[:4]]
        return top or cases[:4]

    def summary(self) -> dict[str, Any]:
        return {
            "dataset": self._schema.get("dataset"),
            "description": self._schema.get("description"),
            "entity_count": len(self._schema.get("entities", [])),
            "relationship_count": len(self._schema.get("relationships", [])),
            "paths": self._schema.get("paths", {}),
        }

    @staticmethod
    def _infer_focus(question: str, entities: list[str]) -> set[str]:
        text = question.replace(" ", "")
        focus: set[str] = set()
        if any(keyword in text for keyword in ["客户", "万科", "华润", "招商蛇口"]):
            focus.add("Customer")
        if any(keyword in text for keyword in ["项目", "城市", "区域", "商业", "住宅"]):
            focus.add("Project")
        if any(keyword in text for keyword in ["设备", "型号", "品牌", "制冷剂", "冷水机组", "参数"]):
            focus.add("Model")
        if any(keyword in text for keyword in ["安装", "数量", "用了", "使用"]):
            focus.add("Installation")
        if any(keyword in text for keyword in ["类别", "冷水机组", "类型"]):
            focus.add("Category")

        for entity in entities:
            if entity in {"Customer", "Project", "Category", "Model", "Installation"}:
                focus.add(entity)
        return focus
