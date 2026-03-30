from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable

from kgqa.config import Settings
from kgqa.generator import AnswerGenerator
from kgqa.llm import LLMClient
from kgqa.models import (
    AnswerTrace,
    CypherTrace,
    ExecutionTrace,
    IntentResult,
    IntentType,
    IntentTrace,
    PlanTrace,
    QueryPlan,
    QueryPlanStep,
    QueryResponse,
    SerializedResult,
    SourceType,
)
from kgqa.planner import QueryPlanner
from kgqa.query import CypherGenerator, CypherSafetyValidator, DomainRegistry, Neo4jExecutor, normalize_key
from kgqa.router import IntentRouter
from kgqa.schema import SchemaRegistry
from kgqa.serializer import ResultSerializer

_SERVICE_CACHE: dict[tuple[str, str, str, str, str, str, str], "KGQAService"] = {}
_SERVICE_CACHE_LOCK = Lock()


def _service_cache_key(settings: Settings) -> tuple[str, str, str, str, str, str, str]:
    return (
        settings.neo4j_uri,
        settings.neo4j_username,
        settings.neo4j_password,
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.dataset_name,
    )


def get_kgqa_service(settings: Settings) -> "KGQAService":
    key = _service_cache_key(settings)
    service = _SERVICE_CACHE.get(key)
    if service is not None:
        return service
    with _SERVICE_CACHE_LOCK:
        service = _SERVICE_CACHE.get(key)
        if service is None:
            service = KGQAService(settings)
            _SERVICE_CACHE[key] = service
        return service


def close_all_kgqa_services() -> None:
    _SERVICE_CACHE.clear()


class KGQAService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.domain = DomainRegistry(settings)
        self.domain.load()
        self.schema = SchemaRegistry(settings, domain=self.domain)
        self.llm_client = LLMClient(settings)
        self.router = IntentRouter(self.llm_client, domain=self.domain)
        self.planner = QueryPlanner(settings, self.llm_client, domain=self.domain)
        self.generator = CypherGenerator(settings, self.llm_client, domain=self.domain)
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
        self.domain.load()

    @staticmethod
    def _notify_progress(
        progress_callback: Callable[[str, str], None] | None,
        stage: str,
        message: str,
    ) -> None:
        if progress_callback:
            progress_callback(stage, message)

    def process_question(
        self,
        question: str,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> QueryResponse:
        started = time.perf_counter()

        self._notify_progress(progress_callback, "intent_running", "正在识别问题意图")
        intent_result, intent_trace = self._resolve_intent(question)
        self._notify_progress(progress_callback, "intent_done", "意图识别完成，正在准备图谱上下文")
        schema_context = self.schema.render_schema_context(
            question=question,
            intent=intent_result.intent,
            entities=intent_result.entities,
            filters=intent_result.filters,
        )
        few_shots = self.schema.few_shots_for_intent(intent_result.intent, question=question)
        self._notify_progress(progress_callback, "plan_running", "正在生成执行计划")
        plan, plan_trace = self._resolve_plan(question, intent_result, schema_context)
        self._notify_progress(progress_callback, "plan_done", "执行计划已就绪，正在生成并执行 Cypher")
        cypher_text, rows, cypher_trace = self._execute_plan(
            question,
            plan,
            intent_result,
            schema_context,
            few_shots,
            progress_callback=progress_callback,
        )

        serialized = self.serializer.serialize(rows, question, intent_result.intent)
        self._notify_progress(progress_callback, "answer_running", "查询完成，正在生成最终回答")
        answer, answer_trace = self._resolve_answer(question, intent_result, serialized)
        self._notify_progress(progress_callback, "answer_done", "最终回答生成完成")
        total_latency_ms = int((time.perf_counter() - started) * 1000)
        trace = ExecutionTrace(
            intent=intent_trace,
            plan=plan_trace,
            cypher=cypher_trace,
            answer=answer_trace,
            query_success=bool(rows) or not self.generator.should_treat_empty_as_failure(question),
            query_row_count=len(rows),
            total_latency_ms=total_latency_ms,
        )

        return QueryResponse(
            question=question,
            intent=intent_result.intent,
            strategy=plan.strategy,
            cypher=cypher_text,
            plan=plan if intent_result.needs_multi_step or intent_result.intent.value == "MULTI_STEP" else None,
            result_preview=serialized.preview,
            answer=answer,
            latency_ms=total_latency_ms,
            trace=trace,
        )

    def _resolve_intent(self, question: str) -> tuple[IntentResult, IntentTrace]:
        started = time.perf_counter()
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

    def _resolve_plan(
        self,
        question: str,
        intent_result: IntentResult,
        schema_context: str,
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
        plan = self.planner.plan_query(question, intent_result, schema_context)
        return plan, PlanTrace(
            source=SourceType.LLM,
            strategy=plan.strategy,
            steps=plan.steps,
            latency_ms=int((time.perf_counter() - started) * 1000),
            reason="llm planner succeeded",
            attempts=1,
        )

    def _execute_plan(
        self,
        original_question: str,
        plan: QueryPlan,
        intent_result: IntentResult,
        schema_context: str,
        few_shots: list[dict[str, str]],
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], CypherTrace]:
        context: dict[str, Any] = {}
        last_cypher: str | None = None
        last_rows: list[dict[str, Any]] = []
        step_rows: dict[str, list[dict[str, Any]]] = {}
        final_trace = CypherTrace(source=SourceType.NONE, reason="no cypher executed")

        for step in plan.steps[:5]:
            step_index = plan.steps.index(step) + 1
            step_desc = f"步骤 {step_index}/{len(plan.steps)}"
            self._notify_progress(progress_callback, "cypher_running", f"{step_desc}：正在生成 Cypher")
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
            )
            self._notify_progress(progress_callback, "cypher_done", f"{step_desc}：Cypher 执行完成")
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

    def _extract_model_like_value(self, row: dict[str, Any]) -> Any:
        alias_config = self.schema.schema.get("column_aliases", {})
        model_def = alias_config.get("型号", {})
        candidates = ["型号"] + model_def.get("zh", []) + model_def.get("en", [])
        for key in candidates:
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

    def _context_keys_for(self, step_id: str, raw_key: str) -> list[str]:
        normalized = normalize_key(raw_key)
        keys = [f"{step_id}_{normalized}"]
        compact = raw_key.replace(" ", "")
        alias_config = self.schema.schema.get("column_aliases", {})
        for canonical, alias_def in alias_config.items():
            zh_list = alias_def.get("zh", [])
            en_list = alias_def.get("en", [])
            candidates = [canonical] + zh_list + en_list
            if any(candidate in compact for candidate in candidates):
                keys.append(f"{step_id}_{canonical}")
                if en_list:
                    keys.append(f"{step_id}_{en_list[0]}")
        return list(dict.fromkeys(keys))

    def _run_single_question(
        self,
        question: str,
        intent_result: IntentResult,
        schema_context: str,
        few_shots: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]], CypherTrace]:
        started = time.perf_counter()
        attempts = 0
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
        raise ValueError(f"LLM cypher failed after {attempts} attempts: {last_error}")

    def _resolve_answer(
        self,
        question: str,
        intent_result: IntentResult,
        serialized: SerializedResult,
    ) -> tuple[str, AnswerTrace]:
        trace_summary = f"intent={intent_result.intent.value}, rows={serialized.row_count}"
        started = time.perf_counter()
        answer = self.answer_generator.compose_with_llm(question, intent_result, serialized, trace_summary)
        return answer, AnswerTrace(
            source=SourceType.LLM,
            reason="llm answer generation succeeded",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
