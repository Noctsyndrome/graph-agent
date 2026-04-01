from __future__ import annotations

import datetime as dt
import difflib
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


def inspect_dataset_readiness(
    settings: Settings,
    schema: dict[str, Any],
    required_entities: list[str] | None = None,
) -> dict[str, Any]:
    executor = Neo4jExecutor(settings)
    schema_entities = [
        str(entity.get("name", "")).strip()
        for entity in schema.get("entities", [])
        if str(entity.get("name", "")).strip()
    ]
    required = required_entities or schema_entities[:2]
    counts: dict[str, int] = {
        "__all__": executor.count_dataset_nodes(settings.dataset_name),
    }
    for entity_name in required:
        counts[entity_name] = executor.count_entity_nodes(entity_name)
    missing_entities = [
        entity_name
        for entity_name in required
        if entity_name in schema_entities and counts.get(entity_name, 0) == 0
    ]
    ready = counts["__all__"] > 0 and not missing_entities
    return {
        "ready": ready,
        "dataset": settings.dataset_name,
        "counts": counts,
        "required_entities": [entity_name for entity_name in required if entity_name in schema_entities],
        "missing_entities": missing_entities,
    }


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

    def count_dataset_nodes(self, dataset_name: str) -> int:
        rows = self.query(f"MATCH (n) WHERE n.dataset = '{dataset_name}' RETURN count(n) AS count")
        if not rows:
            return 0
        return int(rows[0].get("count", 0) or 0)

    def count_entity_nodes(self, entity_name: str) -> int:
        rows = self.query(
            f"MATCH (n:{entity_name} {{dataset: '{self.settings.dataset_name}'}}) RETURN count(n) AS count"
        )
        if not rows:
            return 0
        return int(rows[0].get("count", 0) or 0)

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
    _NODE_PATTERN = re.compile(
        r"\(\s*(?P<var>[A-Za-z_]\w*)?\s*(?::\s*(?P<label>[A-Za-z_]\w*))?\s*(?:\{(?P<props>[^{}]*)\})?\s*\)"
    )
    _REL_PATTERN = re.compile(r"\[\s*(?:[A-Za-z_]\w*)?\s*:\s*(?P<name>[A-Za-z_]\w*)")
    _RELATION_TRIPLE_PATTERN = re.compile(
        r"(?P<left>\(\s*(?P<left_var>[A-Za-z_]\w*)?\s*(?::\s*(?P<left_label>[A-Za-z_]\w*))?\s*(?:\{[^{}]*\})?\s*\))"
        r"\s*(?P<left_arrow><-|-)\s*"
        r"\[\s*(?:[A-Za-z_]\w*)?\s*:\s*(?P<name>[A-Za-z_]\w*)(?:\s*\{[^{}]*\})?\s*\]"
        r"\s*(?P<right_arrow>->|-)\s*"
        r"(?P<right>\(\s*(?P<right_var>[A-Za-z_]\w*)?\s*(?::\s*(?P<right_label>[A-Za-z_]\w*))?\s*(?:\{[^{}]*\})?\s*\))"
    )
    _PROPERTY_REF_PATTERN = re.compile(r"\b(?P<var>[A-Za-z_]\w*)\.(?P<prop>[A-Za-z_]\w*)\b")

    FORBIDDEN = ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "CALL DBMS", "DROP", "LOAD CSV")
    ALLOWED_START = ("MATCH", "WITH", "UNWIND")

    def __init__(self, dataset_name: str = "", schema: dict[str, Any] | None = None):
        self.dataset_name = dataset_name
        self.schema = schema or {}

    def validate(self, cypher: str) -> None:
        normalized = cypher.strip().upper()
        if ";" in cypher.strip().rstrip(";"):
            raise CypherValidationError("multi_statement", "只允许执行单条 Cypher 语句。")
        if not normalized.startswith(self.ALLOWED_START):
            raise CypherValidationError("invalid_start", "Cypher 必须以 MATCH、WITH 或 UNWIND 开头。")
        for token in self.FORBIDDEN:
            pattern = r"\b" + re.escape(token) + r"\b"
            if re.search(pattern, normalized):
                raise CypherValidationError("forbidden_operation", f"检测到不允许的操作: {token}")
        if self._has_comparator_literal_in_property_map(cypher):
            raise CypherValidationError(
                "invalid_property_comparator",
                "检测到将范围/比较条件写成属性字符串，请改用 WHERE + 比较表达式。",
            )
        self._validate_dataset_filters(cypher)
        self._validate_schema_compliance(cypher)

    @staticmethod
    def _has_comparator_literal_in_property_map(cypher: str) -> bool:
        return bool(re.search(r"\{[^{}]*:\s*['\"]\s*[<>]=?.+?['\"][^{}]*\}", cypher))

    def _validate_dataset_filters(self, cypher: str) -> None:
        if not self.dataset_name:
            return
        missing_patterns: list[str] = []
        seen_variables: set[str] = set()
        for node in self._extract_node_patterns(cypher):
            var_name = node.get("var")
            label_name = node.get("label")
            props = node.get("props", "")
            if not var_name and not label_name:
                continue
            if self._node_has_dataset_constraint(cypher, var_name, props):
                if var_name:
                    seen_variables.add(var_name)
                continue
            if var_name and var_name in seen_variables:
                continue
            missing_patterns.append(node.get("pattern", ""))
            if var_name:
                seen_variables.add(var_name)
        if missing_patterns:
            raise CypherValidationError(
                "missing_dataset_filter",
                f"Cypher 缺少当前场景数据集 {self.dataset_name} 的过滤条件。",
                details={
                    "dataset": self.dataset_name,
                    "patterns": missing_patterns,
                },
                hint=(
                    "请为每个 MATCH 中的节点增加 dataset 过滤，例如 "
                    f"(m:Model {{dataset: '{self.dataset_name}'}}) 或 WHERE m.dataset = '{self.dataset_name}'。"
                ),
            )

    def _node_has_dataset_constraint(self, cypher: str, var_name: str | None, props: str) -> bool:
        property_value = self._extract_dataset_value(props)
        if property_value is not None:
            if property_value != self.dataset_name:
                raise CypherValidationError(
                    "wrong_dataset_filter",
                    f"Cypher 使用了错误的数据集过滤: {property_value}。",
                    details={"expected": self.dataset_name, "actual": property_value},
                    hint=f"请将 dataset 过滤统一改为 {self.dataset_name}。",
                )
            return True
        if not var_name:
            return False
        patterns = [
            rf"\b{re.escape(var_name)}\.dataset\s*=\s*['\"](?P<value>[^'\"]+)['\"]",
            rf"['\"](?P<value>[^'\"]+)['\"]\s*=\s*{re.escape(var_name)}\.dataset\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, cypher, re.IGNORECASE)
            if not match:
                continue
            actual_value = match.group("value")
            if actual_value != self.dataset_name:
                raise CypherValidationError(
                    "wrong_dataset_filter",
                    f"Cypher 使用了错误的数据集过滤: {actual_value}。",
                    details={"expected": self.dataset_name, "actual": actual_value},
                    hint=f"请将 dataset 过滤统一改为 {self.dataset_name}。",
                )
            return True
        return False

    @staticmethod
    def _extract_dataset_value(props: str) -> str | None:
        if not props:
            return None
        match = re.search(r"\bdataset\s*:\s*['\"](?P<value>[^'\"]+)['\"]", props, re.IGNORECASE)
        if match is None:
            return None
        return match.group("value")

    def _validate_schema_compliance(self, cypher: str) -> None:
        if not self.schema:
            return
        entity_map = {
            str(entity.get("name", "")).strip(): entity
            for entity in self.schema.get("entities", [])
            if str(entity.get("name", "")).strip()
        }
        relationship_names = {
            str(relation.get("name", "")).strip()
            for relation in self.schema.get("relationships", [])
            if str(relation.get("name", "")).strip()
        }
        relationship_defs = {
            str(relation.get("name", "")).strip(): relation
            for relation in self.schema.get("relationships", [])
            if str(relation.get("name", "")).strip()
        }
        variable_labels: dict[str, str] = {}

        for node in self._extract_node_patterns(cypher):
            label_name = node.get("label")
            var_name = node.get("var")
            if label_name:
                if label_name not in entity_map:
                    raise CypherValidationError(
                        "unknown_entity",
                        f"实体 {label_name} 不存在于当前 schema 中。",
                        details={"entity": label_name},
                        hint=f"当前可用实体: {', '.join(sorted(entity_map))}。",
                    )
                if var_name:
                    variable_labels[var_name] = label_name
                self._validate_property_map_keys(node.get("props", ""), label_name, entity_map)

        for relation_name in self._extract_relationship_names(cypher):
            if relation_name not in relationship_names:
                raise CypherValidationError(
                    "unknown_relationship",
                    f"关系 {relation_name} 不存在于当前 schema 中。",
                    details={"relationship": relation_name},
                    hint=f"当前可用关系: {', '.join(sorted(relationship_names))}。",
                )

        self._validate_relationship_semantics(cypher, relationship_defs, variable_labels)

        for match in self._PROPERTY_REF_PATTERN.finditer(cypher):
            var_name = match.group("var")
            property_name = match.group("prop")
            label_name = variable_labels.get(var_name)
            if label_name is None:
                continue
            allowed_properties = set(entity_map[label_name].get("properties", {}).keys())
            if property_name not in allowed_properties:
                similar_properties = _suggest_similar_tokens(property_name, sorted(allowed_properties))
                suggestion_text = f"{label_name} 实体可用属性: {', '.join(sorted(allowed_properties))}。"
                if similar_properties:
                    suggestion_text += f" 可考虑使用相近字段: {', '.join(similar_properties)}。"
                raise CypherValidationError(
                    "unknown_property",
                    f"属性 {label_name}.{property_name} 不存在于当前 schema 中。",
                    details={
                        "entity": label_name,
                        "property": property_name,
                        "allowed_properties": sorted(allowed_properties),
                    },
                    hint=suggestion_text,
                )

    def _validate_relationship_semantics(
        self,
        cypher: str,
        relationship_defs: dict[str, dict[str, Any]],
        variable_labels: dict[str, str],
    ) -> None:
        for match in self._RELATION_TRIPLE_PATTERN.finditer(cypher):
            relation_name = match.group("name")
            relation_def = relationship_defs.get(relation_name)
            if relation_def is None:
                continue

            left_label = (match.group("left_label") or "").strip() or variable_labels.get((match.group("left_var") or "").strip(), "")
            right_label = (match.group("right_label") or "").strip() or variable_labels.get((match.group("right_var") or "").strip(), "")
            if not left_label or not right_label:
                continue

            left_arrow = match.group("left_arrow")
            right_arrow = match.group("right_arrow")
            if left_arrow == "-" and right_arrow == "->":
                actual_from, actual_to = left_label, right_label
            elif left_arrow == "<-" and right_arrow == "-":
                actual_from, actual_to = right_label, left_label
            else:
                continue

            expected_from = str(relation_def.get("from", "")).strip()
            expected_to = str(relation_def.get("to", "")).strip()
            if actual_from == expected_from and actual_to == expected_to:
                continue

            raise CypherValidationError(
                "relationship_semantics_mismatch",
                f"关系 {relation_name} 的方向或连接实体不符合当前 schema。",
                details={
                    "relationship": relation_name,
                    "expected_from": expected_from,
                    "expected_to": expected_to,
                    "actual_from": actual_from,
                    "actual_to": actual_to,
                },
                hint=(
                    f"当前 schema 中 {relation_name} 的合法方向是 "
                    f"({expected_from})-[:{relation_name}]->({expected_to})。"
                ),
            )

    def _validate_property_map_keys(
        self,
        props: str,
        label_name: str,
        entity_map: dict[str, dict[str, Any]],
    ) -> None:
        if not props:
            return
        allowed_properties = set(entity_map[label_name].get("properties", {}).keys())
        for key in re.findall(r"\b([A-Za-z_]\w*)\s*:", props):
            if key not in allowed_properties:
                similar_properties = _suggest_similar_tokens(key, sorted(allowed_properties))
                suggestion_text = f"{label_name} 实体可用属性: {', '.join(sorted(allowed_properties))}。"
                if similar_properties:
                    suggestion_text += f" 可考虑使用相近字段: {', '.join(similar_properties)}。"
                raise CypherValidationError(
                    "unknown_property",
                    f"属性 {label_name}.{key} 不存在于当前 schema 中。",
                    details={
                        "entity": label_name,
                        "property": key,
                        "allowed_properties": sorted(allowed_properties),
                    },
                    hint=suggestion_text,
                )

    @classmethod
    def _extract_node_patterns(cls, cypher: str) -> list[dict[str, str]]:
        patterns: list[dict[str, str]] = []
        for match in cls._NODE_PATTERN.finditer(cypher):
            patterns.append(
                {
                    "pattern": match.group(0),
                    "var": (match.group("var") or "").strip(),
                    "label": (match.group("label") or "").strip(),
                    "props": (match.group("props") or "").strip(),
                }
            )
        return patterns

    @classmethod
    def _extract_relationship_names(cls, cypher: str) -> list[str]:
        return [match.group("name") for match in cls._REL_PATTERN.finditer(cypher)]


class CypherValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        hint: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.hint = hint

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        if self.hint:
            payload["hint"] = self.hint
        return payload


def diagnose_query_error(
    schema: dict[str, Any],
    dataset_name: str,
    cypher: str,
    error: str | dict[str, Any],
) -> dict[str, Any]:
    entity_map = {
        str(entity.get("name", "")).strip(): entity
        for entity in schema.get("entities", [])
        if str(entity.get("name", "")).strip()
    }
    relationship_names = sorted(
        str(relation.get("name", "")).strip()
        for relation in schema.get("relationships", [])
        if str(relation.get("name", "")).strip()
    )
    payload = _normalize_error_payload(error)
    code = str(payload.get("code", "")).strip()
    message = str(payload.get("message", "")).strip()
    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}

    diagnosis = {
        "status": "ok",
        "error_type": "unknown_error",
        "problematic_token": None,
        "entity": None,
        "relationship": None,
        "available_properties": [],
        "available_relationships": relationship_names,
        "suggestion": "请重新检查 schema、字段名、关系名和 dataset 过滤。",
        "suggested_next_action": "修改 Cypher 后重新执行 validate_cypher",
    }

    if code in {"unknown_property"}:
        entity_name = str(details.get("entity") or _extract_entity_from_message(message) or "")
        property_name = str(details.get("property") or _extract_property_from_message(message) or "")
        available_properties = sorted(entity_map.get(entity_name, {}).get("properties", {}).keys())
        similar = _suggest_similar_tokens(property_name, available_properties)
        diagnosis.update(
            {
                "error_type": "invalid_property",
                "problematic_token": property_name or None,
                "entity": entity_name or None,
                "available_properties": available_properties,
                "suggestion": _property_suggestion(entity_name, property_name, available_properties, similar),
            }
        )
        return diagnosis

    if code in {"unknown_entity"}:
        entity_name = str(details.get("entity") or _extract_entity_from_message(message) or "")
        diagnosis.update(
            {
                "error_type": "invalid_entity",
                "problematic_token": entity_name or None,
                "entity": entity_name or None,
                "suggestion": f"当前场景可用实体为: {', '.join(sorted(entity_map))}。",
            }
        )
        return diagnosis

    if code in {"unknown_relationship"}:
        relationship_name = str(details.get("relationship") or _extract_relationship_from_message(message) or "")
        diagnosis.update(
            {
                "error_type": "invalid_relationship",
                "problematic_token": relationship_name or None,
                "relationship": relationship_name or None,
                "suggestion": f"当前场景可用关系为: {', '.join(relationship_names)}。",
            }
        )
        return diagnosis

    if code in {"missing_dataset_filter", "wrong_dataset_filter"}:
        diagnosis.update(
            {
                "error_type": "dataset_filter_error",
                "problematic_token": dataset_name,
                "suggestion": f"所有 MATCH 节点都必须显式限制 dataset = '{dataset_name}'。",
            }
        )
        return diagnosis

    property_match = re.search(r"Property '([^']+)' does not exist on node with label '([^']+)'", message)
    if property_match:
        property_name = property_match.group(1)
        entity_name = property_match.group(2)
        available_properties = sorted(entity_map.get(entity_name, {}).get("properties", {}).keys())
        similar = _suggest_similar_tokens(property_name, available_properties)
        diagnosis.update(
            {
                "error_type": "invalid_property",
                "problematic_token": property_name,
                "entity": entity_name,
                "available_properties": available_properties,
                "suggestion": _property_suggestion(entity_name, property_name, available_properties, similar),
            }
        )
        return diagnosis

    relationship_match = re.search(r"Unknown relationship type '([^']+)'", message)
    if relationship_match:
        relationship_name = relationship_match.group(1)
        diagnosis.update(
            {
                "error_type": "invalid_relationship",
                "problematic_token": relationship_name,
                "relationship": relationship_name,
                "suggestion": f"当前场景可用关系为: {', '.join(relationship_names)}。",
            }
        )
        return diagnosis

    variable_match = re.search(r"Variable `?([A-Za-z_]\w*)`? not defined", message, re.IGNORECASE)
    if variable_match:
        variable_name = variable_match.group(1)
        diagnosis.update(
            {
                "error_type": "variable_not_defined",
                "problematic_token": variable_name,
                "suggestion": f"变量 {variable_name} 未定义，请检查 MATCH/WITH/RETURN 中的变量名是否一致。",
            }
        )
        return diagnosis

    return diagnosis


def _normalize_error_payload(error: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(error, dict):
        return {
            "code": error.get("code", ""),
            "message": error.get("message", ""),
            "details": error.get("details", {}),
            "hint": error.get("hint"),
        }
    return {"code": "", "message": str(error), "details": {}, "hint": None}


def _extract_entity_from_message(message: str) -> str | None:
    match = re.search(r"属性\s+([A-Za-z_]\w*)\.([A-Za-z_]\w+)", message)
    if match:
        return match.group(1)
    match = re.search(r"实体\s+([A-Za-z_]\w+)", message)
    if match:
        return match.group(1)
    return None


def _extract_property_from_message(message: str) -> str | None:
    match = re.search(r"属性\s+[A-Za-z_]\w+\.([A-Za-z_]\w+)", message)
    if match:
        return match.group(1)
    return None


def _extract_relationship_from_message(message: str) -> str | None:
    match = re.search(r"关系\s+([A-Za-z_]\w+)", message)
    if match:
        return match.group(1)
    return None


def _suggest_similar_tokens(token: str, candidates: list[str]) -> list[str]:
    if not token:
        return []
    return difflib.get_close_matches(token, candidates, n=3, cutoff=0.35)


def _property_suggestion(
    entity_name: str,
    property_name: str,
    available_properties: list[str],
    similar: list[str],
) -> str:
    if similar:
        return (
            f"当前场景中 {entity_name} 没有 {property_name} 属性。"
            f"可考虑使用相近字段: {', '.join(similar)}。"
        )
    return f"{entity_name} 实体可用属性: {', '.join(available_properties)}。"


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
            property_types = {
                str(field_name).strip(): str(field_type).strip().lower()
                for field_name, field_type in (entity.get("properties", {}) or {}).items()
                if str(field_name).strip()
            }
            field_map: dict[str, list[str]] = {}
            for field_name in entity.get("filterable_fields", []):
                field = str(field_name).strip()
                field_type = property_types.get(field, "")
                if not field or not self._should_load_field(field, field_type):
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
    def _should_load_field(field_name: str, field_type: str = "") -> bool:
        normalized = field_name.strip().lower()
        if normalized in {"id", "dataset"}:
            return False
        if normalized.endswith("_id"):
            return False
        return field_type in {"", "string"}

    def as_dict(self) -> dict[str, dict[str, list[str]]]:
        return {
            entity_name: {field_name: list(values) for field_name, values in field_map.items()}
            for entity_name, field_map in self._values.items()
        }

    def get_values(self, entity_name: str, field_name: str) -> list[str]:
        return list(self._values.get(entity_name, {}).get(field_name, []))

    def resolve_entity_name(self, entity_key: str) -> str | None:
        return self._resolve_entity_name(entity_key)

    def resolve_field_name(self, entity_name: str, field_key: str) -> str | None:
        return self._resolve_field_name(entity_name, field_key)

    def get_entity_fields(self, entity_name: str) -> list[str]:
        return list(self._values.get(entity_name, {}).keys())

    def get_filtered(self, key: str) -> dict[str, dict[str, list[str]]]:
        normalized = key.strip()
        if not normalized:
            return {}
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

    def match_value(self, entity_key: str, field_key: str, keyword: str) -> dict[str, Any]:
        entity_name = self._resolve_entity_name(entity_key)
        if entity_name is None:
            return {
                "status": "error",
                "entity": entity_key,
                "field": field_key,
                "keyword": keyword,
                "exact_match": None,
                "fuzzy_matches": [],
                "hint": f"未知实体 {entity_key}。当前可用实体: {', '.join(sorted(self._values))}。",
            }
        field_name = self._resolve_field_name(entity_name, field_key)
        if field_name is None:
            return {
                "status": "error",
                "entity": entity_name,
                "field": field_key,
                "keyword": keyword,
                "exact_match": None,
                "fuzzy_matches": [],
                "hint": f"{entity_name} 可用字段: {', '.join(self.get_entity_fields(entity_name))}。",
            }
        values = self.get_values(entity_name, field_name)
        if not keyword.strip():
            return {
                "status": "error",
                "entity": entity_name,
                "field": field_name,
                "keyword": keyword,
                "exact_match": None,
                "fuzzy_matches": [],
                "hint": "keyword 不能为空。",
            }
        normalized_keyword = self._normalize_match_text(keyword)
        for value in values:
            if self._normalize_match_text(value) == normalized_keyword:
                return {
                    "status": "ok",
                    "entity": entity_name,
                    "field": field_name,
                    "keyword": keyword,
                    "exact_match": value,
                    "fuzzy_matches": [],
                    "hint": "已找到精确匹配，可直接使用该值。",
                }
        scored = []
        for value in values:
            normalized_value = self._normalize_match_text(value)
            contains = normalized_keyword in normalized_value or normalized_value in normalized_keyword
            similarity = difflib.SequenceMatcher(None, normalized_keyword, normalized_value).ratio()
            scored.append((1 if contains else 0, similarity, value))
        scored.sort(key=lambda item: (item[0], item[1], len(item[2])), reverse=True)
        fuzzy_matches = [value for _, similarity, value in scored if similarity > 0.25][:5]
        hint = "未找到精确匹配，建议使用候选值。" if fuzzy_matches else "未找到可用候选，请先查看完整枚举值。"
        return {
            "status": "ok",
            "entity": entity_name,
            "field": field_name,
            "keyword": keyword,
            "exact_match": None,
            "fuzzy_matches": fuzzy_matches,
            "hint": hint,
        }

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        normalized = value.strip().lower()
        normalized = re.sub(r"[\s\-_/]+", "", normalized)
        return normalized

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

