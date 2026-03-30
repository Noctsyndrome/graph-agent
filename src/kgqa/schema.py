from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from kgqa.config import Settings
from kgqa.models import IntentType

if TYPE_CHECKING:
    from kgqa.query import DomainRegistry


class SchemaRegistry:
    def __init__(self, settings: Settings, domain: DomainRegistry | None = None):
        self.settings = settings
        self._schema = self._load_yaml(settings.schema_file)
        self._few_shots = self._load_yaml(settings.few_shots_file)
        self._domain = domain
        self._focus_keywords = self._build_focus_keywords()
        self._scoring_keywords = self._build_scoring_keywords()

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    @property
    def few_shots(self) -> dict[str, Any]:
        return self._few_shots

    # ------------------------------------------------------------------
    # Schema context rendering
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Few-shot selection with auto-derived scoring
    # ------------------------------------------------------------------

    def few_shots_for_intent(self, intent: IntentType, question: str | None = None) -> list[dict[str, str]]:
        cases = self._few_shots.get(intent.value, [])
        if not question:
            return cases
        text = question.replace(" ", "")
        scored: list[tuple[int, dict[str, str]]] = []
        for item in cases:
            score = 0
            sample = item["question"].replace(" ", "")
            for keyword, weight in self._scoring_keywords.items():
                if keyword in text and keyword in sample:
                    score += weight
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

    # ------------------------------------------------------------------
    # Focus inference – driven by schema + domain registry
    # ------------------------------------------------------------------

    def _build_focus_keywords(self) -> dict[str, set[str]]:
        """Build entity-name → keyword set mapping from schema + domain data."""
        mapping: dict[str, set[str]] = {}
        for entity in self._schema.get("entities", []):
            name = entity["name"]
            keywords: set[str] = set()
            # Add Chinese descriptions from schema
            desc = entity.get("description", "")
            if desc:
                keywords.add(desc.replace("信息", "").replace("记录", ""))
            # Add property names that are meaningful query terms
            for prop in entity.get("filterable_fields", []):
                keywords.add(prop)
            mapping[name] = keywords

        # Schema-level generic keywords per entity type
        mapping.setdefault("Customer", set()).update(["客户"])
        mapping.setdefault("Project", set()).update(["项目", "城市", "区域"])
        mapping.setdefault("Model", set()).update(["设备", "型号", "品牌", "参数"])
        mapping.setdefault("Installation", set()).update(["安装", "数量", "用了", "使用"])
        mapping.setdefault("Category", set()).update(["类别", "类型"])

        # Inject domain-specific values from Neo4j (customer names, brands, etc.)
        if self._domain:
            mapping.setdefault("Customer", set()).update(self._domain.customers)
            mapping.setdefault("Model", set()).update(self._domain.brands)
            mapping.setdefault("Project", set()).update(self._domain.cities)
            for pt in self._domain.project_types:
                mapping.setdefault("Project", set()).add(pt)
            for cat in self._domain.categories:
                mapping.setdefault("Category", set()).add(cat)
                mapping.setdefault("Model", set()).add(cat)

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
        return focus

    # ------------------------------------------------------------------
    # Few-shot scoring keywords – auto-derived from schema
    # ------------------------------------------------------------------

    def _build_scoring_keywords(self) -> dict[str, int]:
        """Auto-derive keyword → weight mapping from schema + domain data."""
        weights: dict[str, int] = {}
        scoring_config = self._schema.get("few_shot_scoring", {})
        entity_weight = int(scoring_config.get("entity_description_weight", 1))
        alias_weight = int(scoring_config.get("column_alias_weight", 2))
        domain_weight = int(scoring_config.get("domain_value_weight", 3))
        relationship_weight = int(scoring_config.get("relationship_weight", 2))

        for entity in self._schema.get("entities", []):
            desc = entity.get("description", "")
            if desc:
                clean = desc.replace("信息", "").replace("记录", "")
                if len(clean) >= 2:
                    weights[clean] = max(weights.get(clean, 0), entity_weight)

        for rel in self._schema.get("relationships", []):
            rel_name = rel.get("name", "")
            if rel_name:
                humanized = rel_name.replace("_", " ").strip()
                if humanized:
                    weights[humanized] = max(weights.get(humanized, 0), relationship_weight)

        for canonical, alias_def in self._schema.get("column_aliases", {}).items():
            if len(canonical) >= 2:
                weights[canonical] = max(weights.get(canonical, 0), alias_weight)
            for alias in alias_def.get("zh", []) + alias_def.get("en", []):
                if len(alias) >= 2:
                    weights[alias] = max(weights.get(alias, 0), alias_weight)

        if self._domain:
            for cat in self._domain.categories:
                if len(cat) >= 2:
                    weights[cat] = max(weights.get(cat, 0), domain_weight)
            for refrigerant in self._domain.refrigerants:
                if len(refrigerant) >= 2:
                    weights[refrigerant] = max(weights.get(refrigerant, 0), domain_weight)
            for project_type in self._domain.project_types:
                if len(project_type) >= 2:
                    weights[project_type] = max(weights.get(project_type, 0), alias_weight)
            for brand in self._domain.brands:
                if len(brand) >= 2:
                    weights[brand] = max(weights.get(brand, 0), domain_weight)

        return weights
