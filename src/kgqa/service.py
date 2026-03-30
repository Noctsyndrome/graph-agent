from __future__ import annotations

import time
from typing import Any

from kgqa.config import Settings
from kgqa.generator import AnswerGenerator
from kgqa.llm import LLMClient
from kgqa.models import (
    AnswerTrace,
    CypherTrace,
    ExecutionTrace,
    FallbackRecord,
    IntentResult,
    IntentType,
    IntentTrace,
    PlanTrace,
    QueryPlan,
    QueryResponse,
    SerializedResult,
    SourceType,
)
from kgqa.planner import QueryPlanner
from kgqa.query import CypherGenerator, CypherSafetyValidator, Neo4jExecutor, normalize_key
from kgqa.router import IntentRouter
from kgqa.schema import SchemaRegistry
from kgqa.serializer import ResultSerializer


class KGQAService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.schema = SchemaRegistry(settings)
        self.llm_client = LLMClient(settings) if settings.has_llm else None
        self.router = IntentRouter(self.llm_client)
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
        fallbacks: list[FallbackRecord] = []

        intent_result, intent_trace = self._resolve_intent(question, fallbacks)
        schema_context = self.schema.render_schema_context(
            question=question,
            intent=intent_result.intent,
            entities=intent_result.entities,
            filters=intent_result.filters,
        )
        few_shots = self.schema.few_shots_for_intent(intent_result.intent, question=question)
        plan, plan_trace = self._resolve_plan(question, intent_result, schema_context, fallbacks)
        cypher_text, rows, cypher_trace = self._execute_plan(question, plan, intent_result, schema_context, few_shots, fallbacks)

        serialized = self.serializer.serialize(rows, question, intent_result.intent)
        answer, answer_trace = self._resolve_answer(question, intent_result, serialized, fallbacks)
        total_latency_ms = int((time.perf_counter() - started) * 1000)
        trace = ExecutionTrace(
            intent=intent_trace,
            plan=plan_trace,
            cypher=cypher_trace,
            answer=answer_trace,
            fallbacks=fallbacks,
            query_success=bool(rows) or not self.generator.should_treat_empty_as_failure(question),
            query_row_count=len(rows),
            total_latency_ms=total_latency_ms,
        )

        strategy = plan.strategy
        if fallbacks:
            strategy = "llm_with_rule_fallback"

        return QueryResponse(
            question=question,
            intent=intent_result.intent,
            strategy=strategy,
            cypher=cypher_text,
            plan=plan if intent_result.needs_multi_step or intent_result.intent.value == "MULTI_STEP" else None,
            result_preview=serialized.preview,
            answer=answer,
            latency_ms=total_latency_ms,
            trace=trace,
        )

    def _resolve_intent(self, question: str, fallbacks: list[FallbackRecord]) -> tuple[IntentResult, IntentTrace]:
        if self.llm_client is None:
            result = self.router.classify_with_rules(question)
            return result, IntentTrace(
                source=SourceType.RULE,
                reason=result.reason,
                confidence=result.confidence,
                entities=result.entities,
                filters=result.filters,
                needs_aggregation=result.needs_aggregation,
                needs_multi_step=result.needs_multi_step,
            )

        started = time.perf_counter()
        try:
            result = self.router.classify_intent(question)
            return result, IntentTrace(
                source=SourceType.LLM,
                reason=result.reason,
                confidence=result.confidence,
                entities=result.entities,
                filters=result.filters,
                needs_aggregation=result.needs_aggregation,
                needs_multi_step=result.needs_multi_step,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            fallback = self.router.classify_with_rules(question)
            fallbacks.append(
                FallbackRecord(
                    stage="intent",
                    from_source=SourceType.LLM,
                    to_source=SourceType.RULE,
                    reason=str(exc),
                )
            )
            return fallback, IntentTrace(
                source=SourceType.RULE,
                reason=f"LLM intent failed: {exc}; fallback to rules",
                confidence=fallback.confidence,
                entities=fallback.entities,
                filters=fallback.filters,
                needs_aggregation=fallback.needs_aggregation,
                needs_multi_step=fallback.needs_multi_step,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=2,
            )

    def _resolve_plan(
        self,
        question: str,
        intent_result: IntentResult,
        schema_context: str,
        fallbacks: list[FallbackRecord],
    ) -> tuple[QueryPlan, PlanTrace]:
        if intent_result.intent.value != "MULTI_STEP":
            plan = QueryPlan(
                strategy="llm_single_step",
                steps=[
                    self.planner.plan_query(question, intent_result, schema_context).steps[0],
                ],
            )
            return plan, PlanTrace(
                source=SourceType.NONE,
                strategy=plan.strategy,
                steps=plan.steps,
                reason="single-step execution plan",
            )

        started = time.perf_counter()
        if self.llm_client is not None:
            try:
                plan = self.planner.plan_query(question, intent_result, schema_context)
                return plan, PlanTrace(
                    source=SourceType.LLM,
                    strategy=plan.strategy,
                    steps=plan.steps,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    reason="llm planner succeeded",
                    attempts=1,
                )
            except Exception as exc:
                fallbacks.append(
                    FallbackRecord(
                        stage="plan",
                        from_source=SourceType.LLM,
                        to_source=SourceType.RULE,
                        reason=str(exc),
                    )
                )
                plan = self.planner.plan_with_rules(question)
                return plan, PlanTrace(
                    source=SourceType.RULE,
                    strategy=plan.strategy,
                    steps=plan.steps,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    reason=f"LLM planner failed: {exc}",
                    attempts=2,
                )

        plan = self.planner.plan_with_rules(question)
        return plan, PlanTrace(source=SourceType.RULE, strategy=plan.strategy, steps=plan.steps, reason="rule planner")

    def _execute_plan(
        self,
        original_question: str,
        plan: QueryPlan,
        intent_result: IntentResult,
        schema_context: str,
        few_shots: list[dict[str, str]],
        fallbacks: list[FallbackRecord],
    ) -> tuple[str | None, list[dict[str, Any]], CypherTrace]:
        context: dict[str, Any] = {}
        last_cypher: str | None = None
        last_rows: list[dict[str, Any]] = []
        step_rows: dict[str, list[dict[str, Any]]] = {}
        final_trace = CypherTrace(source=SourceType.NONE, reason="no cypher executed")

        for step in plan.steps[:5]:
            try:
                resolved_question = step.question.format(**context)
            except KeyError as exc:
                missing_key = str(exc).strip("'")
                available_keys = ", ".join(sorted(context.keys())) or "none"
                raise ValueError(
                    f"Missing dependency value for placeholder {missing_key}. Available context keys: {available_keys}"
                ) from exc
            step_query_type = self._normalize_step_query_type(step.query_type, resolved_question)
            step_intent_result = IntentResult(
                intent=step_query_type,
                confidence=intent_result.confidence,
                reason=intent_result.reason,
                entities=intent_result.entities,
                filters=intent_result.filters,
                needs_aggregation=step_query_type.value == "AGGREGATION",
                needs_multi_step=False,
            )
            step_schema_context = self.schema.render_schema_context(
                question=resolved_question,
                intent=step_query_type,
                entities=step_intent_result.entities,
                filters=step_intent_result.filters,
            )
            step_few_shots = self.schema.few_shots_for_intent(step_query_type, question=resolved_question)
            cypher, rows, step_trace = self._run_single_question(
                resolved_question,
                step_intent_result,
                step_schema_context,
                step_few_shots,
                fallbacks,
            )
            last_cypher = cypher
            last_rows = rows
            step_rows[step.id] = rows
            final_trace = step_trace
            if rows:
                for key, value in rows[0].items():
                    for context_key in self._context_keys_for(step.id, str(key)):
                        context[context_key] = value

        if len(plan.steps) >= 2 and "step_1" in step_rows and "step_2" in step_rows and step_rows["step_1"]:
            last_rows = [self._merge_multistep_rows(step_rows["step_1"][0], step_rows["step_2"])]

        if not last_rows and self.generator.should_treat_empty_as_failure(original_question):
            final_trace.reason = "query executed but returned empty rows"
        return last_cypher, last_rows, final_trace

    @staticmethod
    def _normalize_step_query_type(intent: IntentType, question: str) -> IntentType:
        if intent is not IntentType.MULTI_STEP:
            return intent
        text = question.replace(" ", "")
        if any(keyword in text for keyword in ["平均", "占比", "最多", "最大", "最少", "总", "排名", "对比", "比较"]):
            return IntentType.AGGREGATION
        return IntentType.CROSS_DOMAIN

    @staticmethod
    def _extract_model_like_value(row: dict[str, Any]) -> Any:
        for key in ("型号", "可替代型号", "可替代设备", "替代型号", "设备型号", "name"):
            value = row.get(key)
            if value:
                return value
        return None

    def _merge_multistep_rows(
        self,
        primary_row: dict[str, Any],
        replacement_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        merged = dict(primary_row)
        replacement_names: list[Any] = []
        replacement_brands: list[Any] = []
        for row in replacement_rows:
            replacement_name = self._extract_model_like_value(row)
            if replacement_name:
                replacement_names.append(replacement_name)
            brand = row.get("品牌")
            if brand:
                replacement_brands.append(brand)
        merged["可替代方案"] = replacement_names
        if replacement_brands:
            merged["替代品牌"] = list(dict.fromkeys(replacement_brands))
        return merged

    @staticmethod
    def _context_keys_for(step_id: str, raw_key: str) -> list[str]:
        normalized = normalize_key(raw_key)
        keys = [f"{step_id}_{normalized}"]
        compact = raw_key.replace(" ", "")
        aliases = {
            "项目": ("项目", "项目名称", "project", "project_name"),
            "型号": ("型号", "设备型号", "设备名称", "机型", "可替代型号", "可替代设备", "name", "model", "model_name"),
            "品牌": ("品牌", "brand"),
            "能效比": ("能效比", "COP", "cop"),
            "制冷量": ("制冷量",),
        }
        english_aliases = {
            "项目": "project_name",
            "型号": "name",
            "品牌": "brand",
            "能效比": "cop",
        }
        for canonical, candidates in aliases.items():
            if any(candidate in compact for candidate in candidates):
                keys.append(f"{step_id}_{canonical}")
                english_alias = english_aliases.get(canonical)
                if english_alias:
                    keys.append(f"{step_id}_{english_alias}")
        return list(dict.fromkeys(keys))

    def _run_single_question(
        self,
        question: str,
        intent_result: IntentResult,
        schema_context: str,
        few_shots: list[dict[str, str]],
        fallbacks: list[FallbackRecord],
    ) -> tuple[str, list[dict[str, Any]], CypherTrace]:
        started = time.perf_counter()
        attempts = 0
        if self.llm_client is not None:
            last_error = "unknown llm failure"
            for retry in (False, True):
                attempts += 1
                try:
                    cypher = self.generator.generate_with_llm(question, intent_result, schema_context, few_shots, retry=retry)
                    self.validator.validate(cypher)
                    executor = Neo4jExecutor(self.settings)
                    try:
                        rows = executor.query(cypher)
                    finally:
                        executor.close()
                    if not rows and self.generator.should_treat_empty_as_failure(question):
                        last_error = "llm cypher returned empty rows"
                        continue
                    return cypher, rows, CypherTrace(
                        source=SourceType.LLM,
                        text=cypher,
                        valid=True,
                        reason="llm cypher succeeded",
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        attempts=attempts,
                    )
                except Exception as exc:
                    last_error = str(exc)
            rule_cypher = self.generator.generate_with_rules(question)
            if rule_cypher:
                fallbacks.append(
                    FallbackRecord(
                        stage="cypher",
                        from_source=SourceType.LLM,
                        to_source=SourceType.RULE,
                        reason=last_error,
                    )
                )
                self.validator.validate(rule_cypher)
                executor = Neo4jExecutor(self.settings)
                try:
                    rows = executor.query(rule_cypher)
                finally:
                    executor.close()
                return rule_cypher, rows, CypherTrace(
                    source=SourceType.RULE,
                    text=rule_cypher,
                    valid=True,
                    reason=f"fallback after llm failure: {last_error}",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=attempts + 1,
                )
            raise ValueError(f"LLM cypher failed and no rule fallback matched: {last_error}")

        rule_cypher = self.generator.generate_with_rules(question)
        if not rule_cypher:
            raise ValueError(f"暂不支持该问题的规则生成: {question}")
        self.validator.validate(rule_cypher)
        executor = Neo4jExecutor(self.settings)
        try:
            rows = executor.query(rule_cypher)
        finally:
            executor.close()
        return rule_cypher, rows, CypherTrace(
            source=SourceType.RULE,
            text=rule_cypher,
            valid=True,
            reason="rule cypher executed",
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=1,
        )

    def _resolve_answer(
        self,
        question: str,
        intent_result: IntentResult,
        serialized: SerializedResult,
        fallbacks: list[FallbackRecord],
    ) -> tuple[str, AnswerTrace]:
        trace_summary = f"intent={intent_result.intent.value}, rows={serialized.row_count}"
        started = time.perf_counter()
        if self.llm_client is not None:
            try:
                answer = self.answer_generator.compose_with_llm(question, intent_result, serialized, trace_summary)
                return answer, AnswerTrace(
                    source=SourceType.LLM,
                    reason="llm answer generation succeeded",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                fallbacks.append(
                    FallbackRecord(
                        stage="answer",
                        from_source=SourceType.LLM,
                        to_source=SourceType.TEMPLATE,
                        reason=str(exc),
                    )
                )
                answer = self.answer_generator.compose_with_template(question, intent_result, serialized)
                return answer, AnswerTrace(
                    source=SourceType.TEMPLATE,
                    reason=f"fallback after llm answer failure: {exc}",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=2,
                )

        answer = self.answer_generator.compose_with_template(question, intent_result, serialized)
        return answer, AnswerTrace(
            source=SourceType.TEMPLATE,
            reason="template answer generator",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
