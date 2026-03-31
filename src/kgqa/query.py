from __future__ import annotations

import datetime as dt
import re
from typing import Any

from neo4j import Driver, GraphDatabase

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import IntentResult

_DRIVER_CACHE: dict[tuple[str, str, str], Driver] = {}


def normalize_key(text: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text.strip())
    return sanitized.strip("_") or "value"


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
    """Domain-specific entity values loaded dynamically from Neo4j at startup."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._customers: list[str] = []
        self._brands: list[str] = []
        self._cities: list[str] = []
        self._project_types: list[str] = []
        self._project_statuses: list[str] = []
        self._categories: list[str] = []
        self._refrigerants: list[str] = []

    def load(self) -> None:
        executor = Neo4jExecutor(self.settings)
        ds = self.settings.dataset_name
        self._customers = self._flat(executor, f"MATCH (c:Customer {{dataset: '{ds}'}}) RETURN c.name AS v ORDER BY v")
        self._brands = self._flat(executor, f"MATCH (m:Model {{dataset: '{ds}'}}) RETURN DISTINCT m.brand AS v ORDER BY v")
        self._cities = self._flat(executor, f"MATCH (p:Project {{dataset: '{ds}'}}) RETURN DISTINCT p.city AS v ORDER BY v")
        self._project_types = self._flat(executor, f"MATCH (p:Project {{dataset: '{ds}'}}) RETURN DISTINCT p.type AS v ORDER BY v")
        self._project_statuses = self._flat(executor, f"MATCH (p:Project {{dataset: '{ds}'}}) RETURN DISTINCT p.status AS v ORDER BY v")
        self._categories = self._flat(executor, f"MATCH (c:Category {{dataset: '{ds}'}}) WHERE c.parent_id IS NOT NULL RETURN c.name AS v ORDER BY v")
        self._refrigerants = self._flat(executor, f"MATCH (m:Model {{dataset: '{ds}'}}) RETURN DISTINCT m.refrigerant AS v ORDER BY v")

    @staticmethod
    def _flat(executor: Neo4jExecutor, cypher: str) -> list[str]:
        return [str(row["v"]) for row in executor.query(cypher) if row.get("v")]

    @property
    def customers(self) -> list[str]:
        return self._customers

    @property
    def brands(self) -> list[str]:
        return self._brands

    @property
    def cities(self) -> list[str]:
        return self._cities

    @property
    def project_types(self) -> list[str]:
        return self._project_types

    @property
    def project_statuses(self) -> list[str]:
        return self._project_statuses

    @property
    def categories(self) -> list[str]:
        return self._categories

    @property
    def refrigerants(self) -> list[str]:
        return self._refrigerants

    def prompt_summary(self) -> str:
        sections = [
            ("客户名", self.customers),
            ("品牌", self.brands),
            ("城市", self.cities),
            ("项目类型", self.project_types),
            ("项目状态", self.project_statuses),
            ("设备类别", self.categories),
            ("制冷剂", self.refrigerants),
        ]
        lines = ["## 当前图谱中的关键枚举值"]
        for label, values in sections:
            if not values:
                continue
            preview = "、".join(values[:10])
            suffix = " ..." if len(values) > 10 else ""
            lines.append(f"- {label}: {preview}{suffix}")
        return "\n".join(lines)


class CypherGenerator:
    def __init__(self, settings: Settings, llm_client: LLMClient, domain: DomainRegistry | None = None):
        self.settings = settings
        self.llm_client = llm_client
        self.domain = domain or DomainRegistry(settings)

    def generate_with_llm(
        self,
        question: str,
        intent_result: IntentResult,
        schema_context: str,
        few_shots: list[dict[str, str]],
        retry: bool = False,
    ) -> str:
        examples = "\n\n".join(
            f"问题: {item['question']}\nCypher:\n{item['cypher']}" for item in few_shots
        )
        retry_instruction = (
            "\n上一次输出未通过校验。请只输出一条可执行、只读、无 Markdown 包裹的 Cypher。"
            "\n如果涉及时间范围、年份前后、数值比较，必须使用 WHERE 条件，"
            "例如 p.start_date >= date('2024-01-01')，不要写成 {start_date: '>2023'} 这种属性字符串。"
            if retry
            else ""
        )
        domain_summary = self.domain.prompt_summary()
        prompt = (
            f"{schema_context}\n\n"
            f"{domain_summary}\n\n"
            f"## few-shot 示例\n{examples}\n\n"
            f"问题：{question}\n"
            f"意图：{intent_result.intent.value}\n"
            f"实体：{intent_result.entities}\n"
            f"过滤条件：{intent_result.filters}\n"
            f"聚合需求：{intent_result.needs_aggregation}\n"
            f"多步需求：{intent_result.needs_multi_step}\n"
            "请生成单条只读 Cypher，只返回 Cypher 本身。"
            " 如果问题里出现口语化类别、地区或项目状态表达，请优先使用上面枚举摘要中的真实值，不要自造接近但不存在的字面量。"
            f"{retry_instruction}"
        )
        system_prompt = (
            "你是 NL2Cypher 生成器，只能生成单条只读 Cypher。"
            " 涉及日期范围、年份前后、大小比较、时间过滤时，必须使用 WHERE 条件，"
            "例如 p.start_date >= date('2024-01-01')。"
            " 不要把 >2023、<2024、>=6 这类比较表达式写进节点属性 map。"
        )
        if "替代" in question:
            system_prompt += (
                " 对于“X有哪些可替代方案”这类问题，必须从源型号出发使用"
                " (src:Model {name: 'X'})-[:CAN_REPLACE]->(replacement:Model) 的方向，"
                "不要反向写成 replacement 指向源型号。"
            )
        response = self.llm_client.generate(
            prompt=prompt,
            system_prompt=system_prompt,
        )
        text = LLMClient.strip_code_fence(response.content).strip()
        text = re.sub(r"^cypher\s*", "", text, flags=re.IGNORECASE).strip()
        return (
            text.replace("，", ",")
            .replace("（", "(")
            .replace("）", ")")
            .replace("；", ";")
            .replace("：", ":")
        )

    @staticmethod
    def should_treat_empty_as_failure(question: str) -> bool:
        text = question.replace(" ", "")
        no_result_text = text.replace("有没有", "")
        if any(keyword in no_result_text for keyword in ["不存在", "没有", "未找到", "空结果", "火星", "XYZ"]):
            return False
        if any(keyword in text for keyword in ["有哪些可替代方案", "可替代方案有哪些", "可替代的设备"]):
            return True
        if "替代" in text:
            return False
        return True

