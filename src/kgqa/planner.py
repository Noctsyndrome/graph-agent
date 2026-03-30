from __future__ import annotations

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import IntentResult, IntentType, QueryPlan, QueryPlanStep


class QueryPlanner:
    def __init__(self, settings: Settings, llm_client: LLMClient | None = None):
        self.settings = settings
        self.llm_client = llm_client

    def plan_query(self, question: str, intent_result: IntentResult, schema_context: str) -> QueryPlan:
        if intent_result.intent is not IntentType.MULTI_STEP:
            return QueryPlan(
                strategy="llm_single_step",
                steps=[QueryPlanStep(id="step_1", goal="执行单步查询", query_type=intent_result.intent, question=question)],
            )

        if self.llm_client is not None:
            return self._plan_with_llm(question, intent_result, schema_context)
        return self.plan_with_rules(question)

    def plan_with_rules(self, question: str) -> QueryPlan:
        text = question.replace(" ", "")
        if "替代" in text:
            customer = self._extract_one(text, ["万科", "华润", "招商蛇口", "龙湖", "保利", "金地", "旭辉", "华侨城", "中粮", "华发"]) or "万科"
            project_type = self._extract_one(text, ["商业", "住宅", "产业园区"]) or "商业"
            return QueryPlan(
                strategy="rule_based_multistep",
                steps=[
                    QueryPlanStep(
                        id="step_1",
                        goal="查找目标范围内能效比最低的设备",
                        query_type=IntentType.CROSS_DOMAIN,
                        question=f"查找{customer}{project_type}项目中能效比最低的设备",
                    ),
                    QueryPlanStep(
                        id="step_2",
                        goal="查询该设备的可替代方案",
                        query_type=IntentType.SINGLE_DOMAIN,
                        question="查找 {step_1_型号} 的可替代方案",
                        depends_on=["step_1"],
                    ),
                ],
            )
        if "R-22" in text and "2023年后" in text:
            return QueryPlan(
                strategy="rule_based_multistep",
                steps=[
                    QueryPlanStep(
                        id="step_1",
                        goal="查找2024年及以后项目中使用R-22设备的记录",
                        query_type=IntentType.CROSS_DOMAIN,
                        question="查询2023年后的项目中使用R-22制冷剂设备的情况",
                    )
                ],
            )
        if "深圳" in text and "上海" in text and "平均能效比" in text:
            return QueryPlan(
                strategy="rule_based_multistep",
                steps=[
                    QueryPlanStep(
                        id="step_1",
                        goal="计算深圳和上海项目的平均能效比并比较",
                        query_type=IntentType.AGGREGATION,
                        question="对比深圳和上海的项目设备平均能效比",
                    )
                ],
            )
        return QueryPlan(
            strategy="rule_based_multistep",
            steps=[QueryPlanStep(id="step_1", goal="执行复杂查询", query_type=IntentType.MULTI_STEP, question=question)],
        )

    def _plan_with_llm(self, question: str, intent_result: IntentResult, schema_context: str) -> QueryPlan:
        base_system_prompt = (
            "你是知识图谱问答查询规划器。根据问题、意图和 schema，输出最多5步的 JSON："
            "{\"strategy\": str, \"steps\": [{\"id\": str, \"goal\": str, \"query_type\": str, "
            "\"question\": str, \"depends_on\": [str]}]}。"
            "query_type 只能使用 SINGLE_DOMAIN、CROSS_DOMAIN、AGGREGATION、MULTI_STEP。"
            "所有 step.question 必须是中文、可直接单独提问给 NL2Cypher 的自然语言子问题。"
            "如果某一步依赖前一步的结果，必须使用 Python format 风格占位符，例如 {step_1_型号}，"
            "不能写成 'found in step_1' 或其它英语描述。"
            "必须保留 schema 中的精确枚举值，例如项目类型只能用 商业、住宅、产业园区，"
            "不要擅自改成 商业项目、住宅项目。"
            "对于“最低设备 + 替代方案”这类问题，优先拆成两步：先定位设备，再查询替代方案。"
            "只输出 JSON。"
        )
        prompt = (
            f"{schema_context}\n\n"
            f"问题：{question}\n"
            f"意图：{intent_result.intent.value}\n"
            f"实体：{intent_result.entities}\n"
            f"过滤条件：{intent_result.filters}\n"
            "请生成结构化查询计划。"
        )
        last_error = "unknown planner failure"
        for strict_retry in (False, True):
            try:
                system_prompt = base_system_prompt
                if strict_retry:
                    system_prompt += (
                        " 上一次计划不可执行。请严格确保每个子问题都能直接映射到 schema，"
                        "并且依赖字段只允许使用 {step_x_字段名} 占位符。"
                    )
                payload = self.llm_client.generate_json(prompt=prompt, system_prompt=system_prompt)
                steps = [
                    QueryPlanStep(
                        id=str(item["id"]),
                        goal=str(item["goal"]),
                        query_type=IntentType(item["query_type"]),
                        question=str(item["question"]),
                        depends_on=[str(dep) for dep in item.get("depends_on", [])],
                    )
                    for item in payload.get("steps", [])[:5]
                ]
                if not steps:
                    raise ValueError("LLM planner returned no steps.")
                plan = self._normalize_plan(QueryPlan(strategy=str(payload.get("strategy", "llm_multistep")), steps=steps))
                self._validate_plan(plan)
                return plan
            except Exception as exc:
                last_error = str(exc)
        raise ValueError(last_error)

    @staticmethod
    def _extract_one(text: str, candidates: list[str]) -> str:
        for candidate in candidates:
            if candidate in text:
                return candidate
        return ""

    @staticmethod
    def _validate_plan(plan: QueryPlan) -> None:
        english_markers = ("Which ", "What ", "For ", "Find ", "Retrieve ", "Identify ")
        for step in plan.steps:
            question = step.question.strip()
            if not question:
                raise ValueError("Planner returned empty step question.")
            if step.query_type is IntentType.MULTI_STEP:
                raise ValueError("Planner returned nested MULTI_STEP step, which is not directly executable.")
            if question.startswith(english_markers):
                raise ValueError("Planner returned English step question.")
            if "step_" in question and "{" not in question:
                raise ValueError("Planner returned unresolved step reference without placeholder.")
            if "{step_" in question and "}" not in question:
                raise ValueError("Planner returned malformed placeholder.")

    @classmethod
    def _normalize_plan(cls, plan: QueryPlan) -> QueryPlan:
        normalized_steps: list[QueryPlanStep] = []
        for step in plan.steps:
            query_type = step.query_type
            if query_type is IntentType.MULTI_STEP:
                query_type = cls._infer_direct_query_type(step.question)
            normalized_steps.append(
                QueryPlanStep(
                    id=step.id,
                    goal=step.goal,
                    query_type=query_type,
                    question=step.question,
                    depends_on=step.depends_on,
                )
            )
        return QueryPlan(strategy=plan.strategy, steps=normalized_steps)

    @staticmethod
    def _infer_direct_query_type(question: str) -> IntentType:
        text = question.replace(" ", "")
        if any(keyword in text for keyword in ["平均", "占比", "最多", "最大", "最少", "总", "排名", "对比", "比较"]):
            return IntentType.AGGREGATION
        return IntentType.CROSS_DOMAIN
