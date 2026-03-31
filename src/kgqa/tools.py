from __future__ import annotations

from typing import Any

from kgqa.config import Settings
from kgqa.generator import AnswerGenerator
from kgqa.llm import LLMClient
from kgqa.models import IntentResult, IntentType, SerializedResult
from kgqa.query import CypherSafetyValidator, DomainRegistry, Neo4jExecutor
from kgqa.schema import SchemaRegistry
from kgqa.serializer import ResultSerializer


class KGQAToolbox:
    def __init__(
        self,
        settings: Settings,
        schema: SchemaRegistry,
        domain: DomainRegistry,
        llm_client: LLMClient,
    ):
        self.settings = settings
        self.schema = schema
        self.domain = domain
        self.validator = CypherSafetyValidator()
        self.serializer = ResultSerializer()
        self.answer_generator = AnswerGenerator(settings, llm_client)

    @staticmethod
    def tool_specs() -> list[dict[str, Any]]:
        return [
            {
                "name": "get_schema_context",
                "description": "读取当前图谱的 schema、关系路径和字段信息，帮助后续生成查询。",
                "args_schema": {"question": "string"},
            },
            {
                "name": "list_domain_values",
                "description": "读取图谱中各实体 filterable_fields 的真实枚举值。kind 可选，格式为 Entity.field。",
                "args_schema": {"kind": "string | null"},
            },
            {
                "name": "validate_cypher",
                "description": "校验生成的 Cypher 是否只读且符合安全限制。",
                "args_schema": {"cypher": "string"},
            },
            {
                "name": "execute_cypher",
                "description": "执行只读 Cypher，返回查询结果行。",
                "args_schema": {"cypher": "string"},
            },
            {
                "name": "format_results",
                "description": "将查询结果整理为 markdown、表格预览和可视化占位 payload。",
                "args_schema": {"question": "string", "rows": "array"},
            },
        ]

    def invoke(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, tool_name, None)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return handler(**args)

    def get_schema_context(self, question: str = "") -> dict[str, Any]:
        text = question or "当前问题"
        rendered = self.schema.render_schema_context(question=text)
        return {
            "schema_context": rendered,
            "summary": self.schema.summary(),
        }

    def list_domain_values(self, kind: str | None = None) -> dict[str, Any]:
        if not kind:
            return self.domain.as_dict()
        return self.domain.get_filtered(str(kind))

    def validate_cypher(self, cypher: str) -> dict[str, Any]:
        try:
            self.validator.validate(cypher)
            return {"valid": True, "cypher": cypher}
        except Exception as exc:
            return {"valid": False, "cypher": cypher, "error": str(exc)}

    def execute_cypher(self, cypher: str) -> dict[str, Any]:
        executor = Neo4jExecutor(self.settings)
        try:
            rows = executor.query(cypher)
        finally:
            executor.close()
        return {
            "row_count": len(rows),
            "rows": rows,
        }

    def format_results(self, question: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        intent = self._infer_intent(question)
        serialized = self.serializer.serialize(rows, question, intent)
        return {
            "renderer": self._infer_renderer(serialized),
            "payload": serialized.preview,
            "markdown": serialized.markdown,
            "row_count": serialized.row_count,
            "format": serialized.format,
            "preview": serialized.preview,
        }

    def compose_answer(self, question: str, formatted_result: dict[str, Any]) -> str:
        serialized = SerializedResult(
            format=str(formatted_result.get("format", "raw_json")),
            markdown=str(formatted_result.get("markdown", "")),
            preview=list(formatted_result.get("preview", [])),
            row_count=int(formatted_result.get("row_count", 0)),
        )
        intent = self._infer_intent(question)
        intent_result = IntentResult(intent=intent)
        return self.answer_generator.compose_with_llm(
            question=question,
            intent_result=intent_result,
            serialized_result=serialized,
            trace_summary="agent tool execution",
        )

    @staticmethod
    def _infer_renderer(serialized: SerializedResult) -> str:
        if serialized.format in {"key_value"} and serialized.preview:
            return "metric_cards"
        if serialized.preview:
            return "table"
        return "raw_json"

    @staticmethod
    def _infer_intent(question: str) -> IntentType:
        text = question.replace(" ", "")
        if any(keyword in text for keyword in ["平均", "占比", "最多", "最大", "最少", "总", "排名", "比较", "对比"]):
            return IntentType.AGGREGATION
        if any(keyword in text for keyword in ["替代", "有没有可替代", "多步", "之后还有没有"]):
            return IntentType.MULTI_STEP
        if any(keyword in text for keyword in ["客户", "项目", "品牌", "城市", "区域"]):
            return IntentType.CROSS_DOMAIN
        return IntentType.SINGLE_DOMAIN
