from __future__ import annotations

import datetime as dt
from typing import Any

import yaml
from neo4j import Driver, GraphDatabase
from neo4j.graph import Node, Path, Relationship

from kgqa.config import Settings
import re

_DRIVER_CACHE: dict[tuple[str, str, str], Driver] = {}


def get_neo4j_driver(settings: Settings) -> Driver:
    key = (settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password)
    driver = _DRIVER_CACHE.get(key)
    if driver is None:
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        _DRIVER_CACHE[key] = driver
    return driver


def close_all_neo4j_drivers() -> None:
    for driver in _DRIVER_CACHE.values():
        driver.close()
    _DRIVER_CACHE.clear()


def load_seed_data(settings: Settings) -> None:
    script = settings.seed_file.read_text(encoding="utf-8")
    executor = Neo4jExecutor(settings)
    executor.load_seed_data(script)


class Neo4jExecutor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = get_neo4j_driver(settings)

    def close(self) -> None:
        # Driver is process-scoped and intentionally reused.
        return None

    def warmup(self) -> None:
        self.driver.verify_connectivity()
        with self.driver.session() as session:
            session.run("RETURN 1 AS ok").consume()

    def explain(self, cypher: str) -> None:
        with self.driver.session() as session:
            session.run(f"EXPLAIN {cypher}").consume()

    def query(self, cypher: str) -> list[dict[str, Any]]:
        if self.settings.neo4j_validate_with_explain:
            self.explain(cypher)
        with self.driver.session() as session:
            result = session.run(cypher)
            return [
                {key: self._normalize_value(value) for key, value in record.items()}
                for record in result
            ]

    def load_seed_data(self, script: str) -> None:
        lines = []
        for raw_line in script.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("//") or not stripped:
                continue
            lines.append(raw_line)
        statements = [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]
        with self.driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.dataset = $dataset DETACH DELETE n",
                dataset=self.settings.dataset_name,
            ).consume()
            for statement in statements:
                session.run(statement).consume()

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._normalize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._normalize_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._normalize_value(item) for item in value)
        if isinstance(value, Node):
            return {
                "__type__": "node",
                "element_id": value.element_id,
                "labels": sorted(str(label) for label in value.labels),
                "properties": {
                    key: cls._normalize_value(item)
                    for key, item in value.items()
                },
            }
        if isinstance(value, Relationship):
            rel_type = value.type if not callable(value.type) else value.type()
            return {
                "__type__": "relationship",
                "element_id": value.element_id,
                "relationship_type": str(rel_type),
                "start_node_element_id": value.start_node.element_id,
                "end_node_element_id": value.end_node.element_id,
                "properties": {
                    key: cls._normalize_value(item)
                    for key, item in value.items()
                },
            }
        if isinstance(value, Path):
            return {
                "__type__": "path",
                "nodes": [cls._normalize_value(node) for node in value.nodes],
                "relationships": [cls._normalize_value(rel) for rel in value.relationships],
            }
        if isinstance(value, (dt.date, dt.datetime, dt.time)):
            return value.isoformat()
        if value.__class__.__module__.startswith("neo4j.time"):
            return str(value)
        return value


class CypherSafetyValidator:
    FORBIDDEN = ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "CALL DBMS", "DROP", "LOAD CSV")
    ALLOWED_START = ("MATCH", "WITH", "UNWIND")

    def validate(self, cypher: str) -> None:
        normalized = cypher.strip().upper()
        if ";" in cypher.strip().rstrip(";"):
            raise ValueError("只允许执行单条 Cypher 语句。")
        if not normalized.startswith(self.ALLOWED_START):
            raise ValueError("Cypher 必须以 MATCH、WITH 或 UNWIND 开头。")
        for token in self.FORBIDDEN:
            pattern = r"\b" + re.escape(token) + r"\b"
            if re.search(pattern, normalized):
                raise ValueError(f"检测到不允许的操作: {token}")
        if self._has_comparator_literal_in_property_map(cypher):
            raise ValueError("检测到将范围/比较条件写成属性字符串，请改用 WHERE + 比较表达式。")

    @staticmethod
    def _has_comparator_literal_in_property_map(cypher: str) -> bool:
        return bool(re.search(r"\{[^{}]*:\s*['\"]\s*[<>]=?.+?['\"][^{}]*\}", cypher))


class DomainRegistry:
    """Schema-driven distinct values loaded dynamically from Neo4j at startup."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._schema = yaml.safe_load(settings.schema_file.read_text(encoding="utf-8")) or {}
        self._values: dict[str, dict[str, list[str]]] = {}

    def load(self) -> None:
        executor = Neo4jExecutor(self.settings)
        ds = self.settings.dataset_name
        values: dict[str, dict[str, list[str]]] = {}
        for entity in self._schema.get("entities", []):
            entity_name = str(entity.get("name", "")).strip()
            if not entity_name:
                continue
            field_map: dict[str, list[str]] = {}
            for field_name in entity.get("filterable_fields", []):
                field = str(field_name).strip()
                if not field or not self._should_load_field(field):
                    continue
                rows = self._flat(
                    executor,
                    f"MATCH (n:{entity_name} {{dataset: '{ds}'}}) RETURN DISTINCT n.{field} AS v ORDER BY v",
                )
                if rows:
                    field_map[field] = rows
            values[entity_name] = field_map
        self._values = values

    @staticmethod
    def _flat(executor: Neo4jExecutor, cypher: str) -> list[str]:
        return [str(row["v"]) for row in executor.query(cypher) if row.get("v")]

    @staticmethod
    def _should_load_field(field_name: str) -> bool:
        normalized = field_name.strip().lower()
        if normalized in {"id", "dataset"}:
            return False
        return not normalized.endswith("_id")

    def as_dict(self) -> dict[str, dict[str, list[str]]]:
        return {
            entity_name: {field_name: list(values) for field_name, values in field_map.items()}
            for entity_name, field_map in self._values.items()
        }

    def get_values(self, entity_name: str, field_name: str) -> list[str]:
        return list(self._values.get(entity_name, {}).get(field_name, []))

    def get_filtered(self, key: str) -> dict[str, dict[str, list[str]]]:
        normalized = key.strip()
        if not normalized:
            return {}
        alias_entity, alias_field = self._resolve_alias(normalized)
        if alias_entity and alias_field:
            return {alias_entity: {alias_field: self.get_values(alias_entity, alias_field)}}
        if "." not in normalized:
            entity_name = self._resolve_entity_name(normalized)
            if entity_name is not None:
                return {entity_name: self.as_dict().get(entity_name, {})}
            return {}
        entity_key, field_key = normalized.split(".", 1)
        entity_name = self._resolve_entity_name(entity_key)
        if entity_name is None:
            return {}
        field_name = self._resolve_field_name(entity_name, field_key)
        if field_name is None:
            return {entity_name: {}}
        values = self.get_values(entity_name, field_name)
        return {entity_name: {field_name: values}}

    @staticmethod
    def _resolve_alias(key: str) -> tuple[str | None, str | None]:
        alias_map = {
            "customers": ("Customer", "name"),
            "brands": ("Model", "brand"),
            "cities": ("Project", "city"),
            "project_types": ("Project", "type"),
            "project_statuses": ("Project", "status"),
            "categories": ("Category", "name"),
            "refrigerants": ("Model", "refrigerant"),
        }
        return alias_map.get(key, (None, None))

    def _resolve_entity_name(self, entity_key: str) -> str | None:
        normalized = entity_key.strip().lower()
        for entity_name in self._values:
            if entity_name.lower() == normalized:
                return entity_name
        return None

    def _resolve_field_name(self, entity_name: str, field_key: str) -> str | None:
        normalized = field_key.strip().lower()
        for field_name in self._values.get(entity_name, {}):
            if field_name.lower() == normalized:
                return field_name
        return None

    @property
    def customers(self) -> list[str]:
        return self.get_values("Customer", "name")

    @property
    def brands(self) -> list[str]:
        return self.get_values("Model", "brand")

    @property
    def cities(self) -> list[str]:
        return self.get_values("Project", "city")

    @property
    def project_types(self) -> list[str]:
        return self.get_values("Project", "type")

    @property
    def project_statuses(self) -> list[str]:
        return self.get_values("Project", "status")

    @property
    def categories(self) -> list[str]:
        return self.get_values("Category", "name")

    @property
    def refrigerants(self) -> list[str]:
        return self.get_values("Model", "refrigerant")

    def prompt_summary(self) -> str:
        lines = ["## 当前图谱中的关键枚举值"]
        descriptions = {
            str(item.get("name", "")): str(item.get("description", item.get("name", "")))
            for item in self._schema.get("entities", [])
        }
        for entity_name, field_map in self._values.items():
            entity_label = descriptions.get(entity_name, entity_name)
            for field_name, values in field_map.items():
                if not values:
                    continue
                preview = "、".join(values[:10])
                suffix = " ..." if len(values) > 10 else ""
                lines.append(f"- {entity_label}.{field_name}: {preview}{suffix}")
        return "\n".join(lines)

