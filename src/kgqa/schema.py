from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING
import re

import yaml

from kgqa.config import Settings

if TYPE_CHECKING:
    from kgqa.query import DomainRegistry


class SchemaRegistry:
    def __init__(self, settings: Settings, domain: DomainRegistry | None = None):
        self.settings = settings
        self._schema = self._load_yaml(settings.schema_file)
        self._domain = domain
        self._focus_keywords = self._build_focus_keywords()

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    # ------------------------------------------------------------------
    # Schema context rendering
    # ------------------------------------------------------------------

    def render_schema_context(
        self,
        question: str,
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
            line = f"- ({relation['from']})-[:{relation['name']}]->({relation['to']})"
            desc = str(relation.get("description", "")).strip()
            if desc:
                line += f"  — {desc}"
            lines.append(line)
        paths = self._schema.get("paths", {})
        if paths:
            lines.append("")
            lines.append("## 典型路径")
            for path_group in paths.values():
                for path in path_group:
                    lines.append(f"- {path}")
        if entities or filters:
            lines.append("")
            lines.append(f"## 问题中识别到的实体：{entities}")
            lines.append(f"## 问题中识别到的过滤条件：{filters}")
        return "\n".join(lines)

    def summary(self) -> dict[str, Any]:
        return {
            "dataset": self._schema.get("dataset"),
            "description": self._schema.get("description"),
            "entity_count": len(self._schema.get("entities", [])),
            "relationship_count": len(self._schema.get("relationships", [])),
            "paths": self._schema.get("paths", {}),
        }

    def graph_data(self) -> dict[str, Any]:
        nodes = [
            {
                "id": entity["name"],
                "entity_name": entity["name"],
                "label": entity.get("description") or entity["name"],
                "description": entity.get("description", ""),
                "properties": list(entity.get("properties", {}).keys()),
            }
            for entity in self._schema.get("entities", [])
        ]
        links = [
            {
                "source": relation["from"],
                "target": relation["to"],
                "label": relation["name"],
                "cardinality": relation.get("cardinality", ""),
                "description": relation.get("description", ""),
            }
            for relation in self._schema.get("relationships", [])
        ]
        return {
            "dataset": self._schema.get("dataset"),
            "description": self._schema.get("description"),
            "nodes": nodes,
            "links": links,
        }

    def extract_active_types(self, cypher: str) -> dict[str, list[str]]:
        entity_names = {str(entity.get("name", "")).strip() for entity in self._schema.get("entities", [])}
        relationship_names = {
            str(relation.get("name", "")).strip() for relation in self._schema.get("relationships", [])
        }
        entities = {
            match
            for match in re.findall(r"\([^)]*:(?P<label>[A-Za-z_][A-Za-z0-9_]*)", cypher)
            if match in entity_names
        }
        relationships = {
            match
            for match in re.findall(r"\[[^\]]*:(?P<name>[A-Za-z_][A-Za-z0-9_]*)", cypher)
            if match in relationship_names
        }
        return {
            "entities": sorted(entities),
            "relationships": sorted(relationships),
        }

    # ------------------------------------------------------------------
    # Focus inference – driven by schema + domain registry
    # ------------------------------------------------------------------

    def _build_focus_keywords(self) -> dict[str, set[str]]:
        """Build entity-name → keyword set mapping from schema + domain data."""
        mapping: dict[str, set[str]] = {}
        for entity in self._schema.get("entities", []):
            name = entity["name"]
            keywords: set[str] = set()
            desc = str(entity.get("description", "")).strip()
            if desc:
                keywords.add(desc)
                for token in self._description_tokens(desc):
                    keywords.add(token)
            keywords.add(name)
            for prop in entity.get("filterable_fields", []):
                prop_name = str(prop).strip()
                if prop_name:
                    keywords.add(prop_name)
            mapping[name] = {item for item in keywords if item}

        if self._domain:
            for entity_name, field_map in self._domain.as_dict().items():
                entity_keywords = mapping.setdefault(entity_name, set())
                for field_name, values in field_map.items():
                    entity_keywords.add(field_name)
                    entity_keywords.update(str(value) for value in values if str(value).strip())

        return mapping

    def _infer_focus(self, question: str, entities: list[str]) -> set[str]:
        text = question.replace(" ", "")
        focus: set[str] = set()
        for entity_name, keywords in self._focus_keywords.items():
            if any(kw in text for kw in keywords):
                focus.add(entity_name)
        for entity in entities:
            if entity in {e["name"] for e in self._schema.get("entities", [])}:
                focus.add(entity)
        if not focus:
            return focus
        expanded = set(focus)
        for relation in self._schema.get("relationships", []):
            from_entity = str(relation.get("from", "")).strip()
            to_entity = str(relation.get("to", "")).strip()
            if from_entity in focus and to_entity:
                expanded.add(to_entity)
            if to_entity in focus and from_entity:
                expanded.add(from_entity)
        return expanded

    @staticmethod
    def _description_tokens(description: str) -> set[str]:
        cleaned = description.strip()
        base = re.sub(r"(信息|记录|数据|实体)$", "", cleaned)
        tokens = {cleaned, base}
        for part in re.split(r"[·/、\s]", base):
            if part:
                tokens.add(part)
        return {token for token in tokens if token}
