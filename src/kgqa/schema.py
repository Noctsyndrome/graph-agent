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

    def render_schema_context(self, intent: IntentType | None = None) -> str:
        lines = ["## 图谱 Schema", ""]
        for entity in self._schema["entities"]:
            field_text = ", ".join(f"{key}: {value}" for key, value in entity["properties"].items())
            lines.append(f"- {entity['name']}: {field_text}")
        lines.append("")
        lines.append("## 关系类型")
        for relation in self._schema["relationships"]:
            lines.append(f"- ({relation['from']})-[:{relation['name']}]->({relation['to']})")
        if intent:
            lines.append("")
            lines.append(f"## 典型路径（{intent.value}）")
            for path in self._schema["paths"].get(intent.value, []):
                lines.append(f"- {path}")
        return "\n".join(lines)

    def few_shots_for_intent(self, intent: IntentType) -> list[dict[str, str]]:
        return self._few_shots.get(intent.value, [])

    def summary(self) -> dict[str, Any]:
        return {
            "dataset": self._schema.get("dataset"),
            "description": self._schema.get("description"),
            "entity_count": len(self._schema.get("entities", [])),
            "relationship_count": len(self._schema.get("relationships", [])),
            "paths": self._schema.get("paths", {}),
        }

