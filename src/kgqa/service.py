from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from kgqa.config import Settings
from kgqa.generator import AnswerGenerator
from kgqa.llm import LLMClient
from kgqa.models import IntentResult, IntentType, QueryPlan, QueryResponse, SerializedResult
from kgqa.planner import QueryPlanner
from kgqa.query import CypherGenerator, CypherSafetyValidator, Neo4jExecutor, normalize_key
from kgqa.router import IntentRouter
from kgqa.schema import SchemaRegistry
from kgqa.serializer import ResultSerializer


@dataclass
class ExecutionArtifact:
    cypher: str | None
    plan: QueryPlan | None
    rows: list[dict[str, Any]]
    serialized: SerializedResult


class KGQAService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.schema = SchemaRegistry(settings)
        self.llm_client = LLMClient(settings) if settings.has_llm else None
        self.router = IntentRouter()
        self.planner = QueryPlanner(settings, self.llm_client)
        self.generator = CypherGenerator(settings, self.llm_client)
        self.validator = CypherSafetyValidator()
        self.serializer = ResultSerializer()
        self.answer_generator = AnswerGenerator(settings, self.llm_client)

    def load_seed_data(self) -> None:
        seed_text = self.settings.seed_file.read_text(encoding="utf-8")
        executor = Neo4jExecutor(self.settings)
        try:
            executor.load_seed_data(seed_text)
        finally:
            executor.close()

    def process_question(self, question: str) -> QueryResponse:
        started = time.perf_counter()
        intent_result = self.router.classify_intent(question)
        artifact = self._execute(question, intent_result)
        answer = self.answer_generator.compose_answer(question, intent_result.intent, artifact.serialized)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return QueryResponse(
            question=question,
            intent=intent_result.intent,
            strategy=artifact.plan.strategy if artifact.plan else "single_query",
            cypher=artifact.cypher,
            plan=artifact.plan,
            result_preview=artifact.serialized.preview,
            answer=answer,
            latency_ms=elapsed_ms,
        )

    def _execute(self, question: str, intent_result: IntentResult) -> ExecutionArtifact:
        if intent_result.intent is IntentType.MULTI_STEP:
            plan = self.planner.plan_query(question, intent_result.intent)
            rows = self._execute_plan(plan)
            serialized = self.serializer.serialize(rows, question, intent_result.intent)
            return ExecutionArtifact(cypher=None, plan=plan, rows=rows, serialized=serialized)

        cypher, rows = self._run_single_question(question, intent_result.intent)
        serialized = self.serializer.serialize(rows, question, intent_result.intent)
        return ExecutionArtifact(cypher=cypher, plan=None, rows=rows, serialized=serialized)

    def _execute_plan(self, plan: QueryPlan) -> list[dict[str, Any]]:
        context: dict[str, Any] = {}
        collected_rows: list[dict[str, Any]] = []
        step_rows: dict[str, list[dict[str, Any]]] = {}
        for step in plan.steps[:5]:
            question = step.question.format(**context)
            _, rows = self._run_single_question(question, step.query_type)
            step_rows[step.id] = rows
            collected_rows = rows
            if rows:
                for key, value in rows[0].items():
                    context[f"{step.id}_{normalize_key(str(key))}"] = value
        if len(plan.steps) >= 2 and "step_1" in step_rows and "step_2" in step_rows and step_rows["step_1"]:
            merged = dict(step_rows["step_1"][0])
            if step_rows["step_2"]:
                merged["可替代方案"] = [row.get("型号") for row in step_rows["step_2"] if row.get("型号")]
            return [merged]
        return collected_rows

    def _run_single_question(self, question: str, intent: IntentType) -> tuple[str, list[dict[str, Any]]]:
        schema_context = self.schema.render_schema_context(intent)
        few_shots = self.schema.few_shots_for_intent(intent)
        cypher = self.generator.generate_cypher(question, intent, schema_context, few_shots)
        self.validator.validate(cypher)
        executor = Neo4jExecutor(self.settings)
        try:
            rows = executor.query(cypher)
        finally:
            executor.close()
        return cypher, rows
