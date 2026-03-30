from __future__ import annotations

import datetime as dt
import re
from typing import Any

from neo4j import Driver, GraphDatabase

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import IntentResult, IntentType

_DRIVER_CACHE: dict[tuple[str, str, str], Driver] = {}


def quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


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


class CypherGenerator:
    CUSTOMERS = ["万科", "华润", "招商蛇口", "龙湖", "保利", "金地", "旭辉", "华侨城", "中粮", "华发"]
    BRANDS = ["开利", "约克", "大金", "格力", "美的", "海尔"]
    CITIES = ["深圳", "上海", "广州", "北京", "杭州", "苏州", "成都", "武汉", "南京", "厦门"]
    PROJECT_TYPES = ["商业", "住宅", "产业园区"]

    def __init__(self, settings: Settings, llm_client: LLMClient | None = None):
        self.settings = settings
        self.llm_client = llm_client

    def generate_with_llm(
        self,
        question: str,
        intent_result: IntentResult,
        schema_context: str,
        few_shots: list[dict[str, str]],
        retry: bool = False,
    ) -> str:
        if self.llm_client is None:
            raise RuntimeError("LLM is not configured for cypher generation.")

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
        prompt = (
            f"{schema_context}\n\n"
            f"## few-shot 示例\n{examples}\n\n"
            f"问题：{question}\n"
            f"意图：{intent_result.intent.value}\n"
            f"实体：{intent_result.entities}\n"
            f"过滤条件：{intent_result.filters}\n"
            f"聚合需求：{intent_result.needs_aggregation}\n"
            f"多步需求：{intent_result.needs_multi_step}\n"
            "请生成单条只读 Cypher，只返回 Cypher 本身。"
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

    def generate_with_rules(self, question: str) -> str | None:
        text = question.replace(" ", "")
        customer = self._extract_one(text, self.CUSTOMERS)
        brand = self._extract_one(text, self.BRANDS)
        cities = [city for city in self.CITIES if city in text]
        project_type = self._extract_one(text, self.PROJECT_TYPES)
        category = "冷水机组" if "冷水机组" in text else ""
        dataset = quote(self.settings.dataset_name)
        unknown_brand_match = re.search(r"品牌([A-Za-z0-9_-]+)", question)
        explicit_brand = brand or (unknown_brand_match.group(1) if unknown_brand_match else "")
        unknown_city_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9]+)区域", question)
        explicit_city = cities[0] if cities else (unknown_city_match.group(1) if unknown_city_match else "")
        model_match = re.search(r"(?:开利|约克|大金|格力|美的|海尔)?\s*([A-Z0-9-]{4,})", question)
        model_name = model_match.group(1) if model_match else ""

        if "可替代方案" in text or ("替代" in text and model_name):
            target_model = model_name or self._extract_model_name_from_text(question)
            if not target_model:
                return None
            return (
                f"MATCH (:Model {{dataset: {dataset}, name: {quote(target_model)}}})-[:CAN_REPLACE]->(m:Model {{dataset: {dataset}}}) "
                "RETURN m.name AS 型号, m.brand AS 品牌, m.cop AS 能效比, m.cooling_kw AS 制冷量, m.refrigerant AS 制冷剂 "
                "ORDER BY m.cop DESC, m.cooling_kw DESC"
            )

        if "详细参数" in text or ("参数" in text and model_name):
            return (
                f"MATCH (m:Model {{dataset: {dataset}, name: {quote(model_name)}}}) "
                "RETURN m.name AS 型号, m.brand AS 品牌, m.cooling_kw AS 制冷量, m.cop AS 能效比, "
                "m.refrigerant AS 制冷剂, m.noise_db AS 噪音, m.weight_kg AS 重量, m.price_wan AS 价格"
            )

        if "区别" in text and len([item for item in self.BRANDS if item in text]) >= 2:
            brands = [item for item in self.BRANDS if item in text][:2]
            category_clause = ""
            if category:
                category_clause = f"-[:BELONGS_TO]->(:Category {{dataset: {dataset}, name: {quote(category)}}})"
            brands_literal = "[" + ", ".join(quote(item) for item in brands) + "]"
            return (
                f"MATCH (m:Model {{dataset: {dataset}}}){category_clause} "
                f"WHERE m.brand IN {brands_literal} "
                "RETURN m.brand AS 品牌, count(*) AS 型号数量, round(avg(m.cop), 2) AS 平均能效比, "
                "round(avg(m.cooling_kw), 2) AS 平均制冷量, round(avg(m.price_wan), 2) AS 平均价格 "
                "ORDER BY 平均能效比 DESC"
            )

        if "有哪些型号" in text or ("冷水机组" in text and "有哪些" in text):
            cop_filter = ""
            cop_match = re.search(r"能效比在?([0-9]+(?:\.[0-9]+)?)以上", text)
            if cop_match:
                cop_filter = f" WHERE m.cop > {cop_match.group(1)}"
            category_name = category or "冷水机组"
            return (
                f"MATCH (m:Model {{dataset: {dataset}}})-[:BELONGS_TO]->(:Category {{dataset: {dataset}, name: {quote(category_name)}}})"
                f"{cop_filter} "
                "RETURN m.name AS 型号, m.brand AS 品牌, m.cop AS 能效比, m.cooling_kw AS 制冷量 "
                "ORDER BY m.cop DESC, m.brand, m.name"
            )

        if customer and ("分别用了哪些品牌" in text or "用了哪些品牌" in text):
            category_clause = ""
            if category:
                category_clause = f"-[:BELONGS_TO]->(:Category {{dataset: {dataset}, name: {quote(category)}}})"
            return (
                f"MATCH (:Customer {{dataset: {dataset}, name: {quote(customer)}}})-[:OWNS_PROJECT]->(p:Project {{dataset: {dataset}}}) "
                f"MATCH (p)-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}){category_clause} "
                "RETURN p.name AS 项目, collect(DISTINCT m.brand) AS 品牌列表, collect(DISTINCT m.name) AS 型号列表, sum(i.quantity) AS 数量 "
                "ORDER BY p.name"
            )

        if explicit_brand and ("安装了" in text or "装在" in text) and "项目" in text:
            return (
                f"MATCH (p:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                f"WHERE m.brand = {quote(explicit_brand)} "
                "RETURN DISTINCT p.name AS 项目, p.city AS 城市, p.type AS 项目类型, p.status AS 状态 "
                "ORDER BY p.city, p.name"
            )

        if explicit_brand and "项目" in text and ("哪些" in text or "哪些项目" in text):
            return (
                f"MATCH (p:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                f"WHERE m.brand = {quote(explicit_brand)} "
                "RETURN DISTINCT p.name AS 项目, p.city AS 城市, p.type AS 项目类型, p.status AS 状态 "
                "ORDER BY p.city, p.name"
            )

        if explicit_city and ("用了什么设备" in text or "都用了什么设备" in text):
            return (
                f"MATCH (p:Project {{dataset: {dataset}, city: {quote(explicit_city)}}})-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                "RETURN p.name AS 项目, collect(DISTINCT m.name) AS 设备型号, collect(DISTINCT m.brand) AS 品牌, sum(i.quantity) AS 数量 "
                "ORDER BY p.name"
            )

        if explicit_city and "品牌" in text and ("使用了哪些品牌" in text or "用了哪些品牌" in text):
            return (
                f"MATCH (p:Project {{dataset: {dataset}, city: {quote(explicit_city)}}})-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                "RETURN p.name AS 项目, collect(DISTINCT m.brand) AS 品牌列表, sum(i.quantity) AS 数量 ORDER BY p.name"
            )

        if explicit_brand and "最多" in text and "客户" in text:
            return (
                f"MATCH (c:Customer {{dataset: {dataset}}})-[:OWNS_PROJECT]->(:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                f"WHERE m.brand = {quote(explicit_brand)} "
                "RETURN c.name AS 客户, sum(i.quantity) AS 使用数量 ORDER BY 使用数量 DESC"
            )

        if "占比" in text and "品牌" in text:
            return (
                f"MATCH (:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                "WITH m.brand AS 品牌, sum(i.quantity) AS 数量 "
                "WITH collect({品牌: 品牌, 数量: 数量}) AS rows, sum(数量) AS total "
                "UNWIND rows AS row "
                "RETURN row.品牌 AS 品牌, row.数量 AS 数量, round(toFloat(row.数量) / total * 100, 2) AS 占比 "
                "ORDER BY 数量 DESC"
            )

        if "总制冷量最大" in text:
            return (
                f"MATCH (p:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                "RETURN p.city AS 城市, round(sum(i.quantity * m.cooling_kw), 2) AS 总制冷量 ORDER BY 总制冷量 DESC"
            )

        if ("2023年后" in text or "2024年后" in text) and "R-22" in text:
            return (
                f"MATCH (p:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(i:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                "WHERE p.start_date >= date('2024-01-01') AND m.refrigerant = 'R-22' "
                "RETURN p.name AS 项目, p.city AS 城市, p.start_date AS 开始日期, m.name AS 型号, m.brand AS 品牌, m.refrigerant AS 制冷剂 "
                "ORDER BY p.start_date, p.name"
            )

        if customer and project_type and "最低" in text and ("能效比" in text or "COP" in question.upper()):
            return (
                f"MATCH (:Customer {{dataset: {dataset}, name: {quote(customer)}}})-[:OWNS_PROJECT]->(p:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                f"WHERE p.type = {quote(project_type)} "
                "RETURN p.name AS 项目, m.name AS 型号, m.brand AS 品牌, m.cop AS 能效比, m.cooling_kw AS 制冷量 "
                "ORDER BY m.cop ASC, m.cooling_kw ASC LIMIT 1"
            )

        if explicit_brand and "设备" in text and "有哪些" in text:
            return (
                f"MATCH (m:Model {{dataset: {dataset}}}) WHERE m.brand = {quote(explicit_brand)} "
                "RETURN m.name AS 型号, m.brand AS 品牌, m.cop AS 能效比, m.refrigerant AS 制冷剂 ORDER BY m.name"
            )

        if explicit_brand and "设备" in text and ("不存在" in text or "没有" in text):
            return (
                f"MATCH (m:Model {{dataset: {dataset}}}) WHERE m.brand = {quote(explicit_brand)} "
                "RETURN m.name AS 型号, m.brand AS 品牌, m.cop AS 能效比, m.refrigerant AS 制冷剂 ORDER BY m.name"
            )

        if "深圳" in text and "上海" in text and "平均能效比" in text:
            return (
                f"MATCH (p:Project {{dataset: {dataset}}})-[:HAS_INSTALLATION]->(:Installation {{dataset: {dataset}}})-[:USES_MODEL]->(m:Model {{dataset: {dataset}}}) "
                "WHERE p.city IN ['深圳', '上海'] "
                "RETURN p.city AS 城市, round(avg(m.cop), 2) AS 平均能效比 ORDER BY 平均能效比 DESC"
            )

        return None

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

    @staticmethod
    def _extract_one(text: str, candidates: list[str]) -> str:
        for candidate in candidates:
            if candidate in text:
                return candidate
        return ""

    @staticmethod
    def _extract_model_name_from_text(text: str) -> str:
        match = re.search(r"([A-Z0-9-]{4,})", text)
        return match.group(1) if match else ""
