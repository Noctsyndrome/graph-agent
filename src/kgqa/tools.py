from __future__ import annotations

from typing import Any

from kgqa.config import Settings
from kgqa.generator import AnswerGenerator
from kgqa.llm import LLMClient
from kgqa.models import SerializedResult
from kgqa.query import (
    CypherSafetyValidator,
    CypherValidationError,
    DomainRegistry,
    Neo4jExecutor,
    diagnose_query_error,
)
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
        self.validator = CypherSafetyValidator(settings.dataset_name, schema.schema)
        self.serializer = ResultSerializer()
        self.answer_generator = AnswerGenerator(settings, llm_client)

    def tool_specs(self) -> list[dict[str, Any]]:
        entity_names = [str(entity.get("name", "")).strip() for entity in self.schema.schema.get("entities", [])]
        relationship_names = [
            str(relation.get("name", "")).strip()
            for relation in self.schema.schema.get("relationships", [])
        ]
        domain_examples: list[str] = []
        for entity_name, field_map in self.domain.as_dict().items():
            for field_name in field_map:
                domain_examples.append(f"{entity_name}.{field_name}")
                if len(domain_examples) >= 4:
                    break
            if len(domain_examples) >= 4:
                break
        domain_example_text = ", ".join(domain_examples) if domain_examples else "Model.brand"
        return [
            {
                "name": "get_schema_context",
                "description": (
                    "读取当前图谱的 schema、关系路径和字段信息。"
                    f"当前实体: {', '.join(entity_names)}。"
                    f"当前关系: {', '.join(relationship_names)}。"
                ),
                "args_schema": {"question": "string"},
            },
            {
                "name": "list_domain_values",
                "description": (
                    "读取图谱中各实体 filterable_fields 的真实枚举值。"
                    f"kind 参数格式必须为 Entity.field，示例: {domain_example_text}。"
                    "如果用户说的是简称、别名或模糊值，优先改用 match_value。"
                ),
                "args_schema": {"kind": "string | null"},
            },
            {
                "name": "match_value",
                "description": (
                    "对单个用户提到的模糊值做匹配，返回精确值或最接近的候选值。"
                    "适合类别简称、品牌简称、模糊别名等场景。"
                ),
                "args_schema": {"entity": "string", "field": "string", "keyword": "string"},
            },
            {
                "name": "inspect_recent_executions",
                "description": (
                    "读取当前会话最近几次成功的 execute_cypher 记录，返回对应的用户问题、Cypher 和结果摘要。"
                    "当当前问题依赖前序查询的约束、排序或结果集合时，先调用它，不要只根据自然语言回答猜测约束。"
                ),
                "args_schema": {"limit": "integer | null"},
            },
            {
                "name": "plan_query",
                "description": (
                    "在构造 Cypher 前，用自然语言明确当前问题的图语义理解，包括目标实体、路径、约束和歧义处理。"
                    "needs_clarification：若问题存在无法从上下文和 schema 消解的结构性歧义，设为 true；否则设为 false。"
                ),
                "args_schema": {"question": "string", "description": "string", "needs_clarification": "boolean | null"},
            },
            {
                "name": "validate_cypher",
                "description": (
                    "校验生成的 Cypher 是否只读、是否符合当前 schema，"
                    f"以及是否为当前数据集 {self.settings.dataset_name} 显式添加 dataset 过滤。"
                ),
                "args_schema": {"cypher": "string"},
            },
            {
                "name": "execute_cypher",
                "description": (
                    "执行只读 Cypher，返回查询结果行。"
                    f"执行前请先 validate_cypher，且所有 MATCH 节点必须限定 dataset = '{self.settings.dataset_name}'。"
                    "如果执行报错且需要理解修复方向，可调用 diagnose_error。"
                ),
                "args_schema": {"cypher": "string"},
            },
            {
                "name": "diagnose_error",
                "description": (
                    "解析 validate_cypher 或 execute_cypher 的错误，结合当前 schema 输出结构化修复建议。"
                ),
                "args_schema": {"cypher": "string", "error": "string | object"},
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

    def match_value(self, entity: str, field: str, keyword: str) -> dict[str, Any]:
        return self.domain.match_value(entity, field, keyword)

    def inspect_recent_executions(
        self,
        limit: int | None = None,
        tool_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        history = tool_history if isinstance(tool_history, list) else []
        max_items = max(1, int(limit or 3))
        executions: list[dict[str, Any]] = []

        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            if str(item.get("tool_name", "")) != "execute_cypher":
                continue
            if str(item.get("status", "")) != "ok":
                continue

            tool_args = item.get("tool_args", {}) if isinstance(item.get("tool_args"), dict) else {}
            tool_result = item.get("tool_result", {}) if isinstance(item.get("tool_result"), dict) else {}
            execution: dict[str, Any] = {
                "user_question": item.get("user_question"),
                "cypher": tool_args.get("cypher"),
                "row_count": tool_result.get("row_count"),
                "columns": tool_result.get("columns", []),
                "rows_preview": tool_result.get("rows_preview", []),
            }
            if isinstance(tool_result.get("rows"), list):
                execution["rows"] = tool_result.get("rows")
            executions.append(execution)
            if len(executions) >= max_items:
                break

        return {"status": "ok", "executions": executions}

    def plan_query(self, question: str, description: str, needs_clarification: bool = False) -> dict[str, Any]:
        return {
            "status": "ok",
            "question": question,
            "description": description,
            "needs_clarification": bool(needs_clarification),
        }

    def validate_cypher(self, cypher: str) -> dict[str, Any]:
        try:
            self.validator.validate(cypher)
            return {"valid": True, "cypher": cypher, "status": "ok"}
        except CypherValidationError as exc:
            return {"valid": False, "cypher": cypher, "status": "error", "error": exc.to_payload()}
        except Exception as exc:
            return {
                "valid": False,
                "cypher": cypher,
                "status": "error",
                "error": {"code": "validation_failed", "message": str(exc)},
            }

    def execute_cypher(self, cypher: str) -> dict[str, Any]:
        executor = Neo4jExecutor(self.settings)
        try:
            rows = executor.query(cypher)
        except Exception as exc:
            return {
                "status": "error",
                "error": {
                    "code": "execution_failed",
                    "message": str(exc),
                    "hint": "请根据报错修正 Cypher 后重新执行。",
                },
            }
        finally:
            executor.close()
        return {
            "status": "ok",
            "row_count": len(rows),
            "rows": rows,
        }

    def diagnose_error(self, cypher: str, error: str | dict[str, Any]) -> dict[str, Any]:
        return diagnose_query_error(
            schema=self.schema.schema,
            dataset_name=self.settings.dataset_name,
            cypher=cypher,
            error=error,
        )

    def format_results(self, question: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        serialized = self.serializer.serialize(rows, question=question)
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
        return self.answer_generator.compose_with_llm(
            question=question,
            serialized_result=serialized,
            trace_summary="agent tool execution",
        )

    @staticmethod
    def _infer_renderer(serialized: SerializedResult) -> str:
        if serialized.format == "key_value" and serialized.preview:
            return "metric_cards"
        if serialized.format == "empty":
            return "raw_json"
        if serialized.preview:
            return "table"
        return "raw_json"
