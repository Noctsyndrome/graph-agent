from __future__ import annotations

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import IntentType, QueryPlan, QueryPlanStep


class QueryPlanner:
    def __init__(self, settings: Settings, llm_client: LLMClient | None = None):
        self.settings = settings
        self.llm_client = llm_client

    def plan_query(self, question: str, intent: IntentType) -> QueryPlan:
        if intent is not IntentType.MULTI_STEP:
            return QueryPlan(
                strategy="rule_based_single_step",
                steps=[QueryPlanStep(id="step_1", goal="执行查询", query_type=intent, question=question)],
            )

        if self.settings.has_llm and self.llm_client is not None:
            try:
                return self._plan_with_llm(question)
            except Exception:
                pass
        return self._plan_with_rules(question)

    def _plan_with_llm(self, question: str) -> QueryPlan:
        system_prompt = (
            "你是知识图谱问答查询规划器。请把复杂问题拆成最多5步的 JSON，"
            "输出格式为 {\"strategy\": str, \"steps\": [{\"id\": str, \"goal\": str, \"query_type\": str, "
            "\"question\": str, \"depends_on\": [str]}]}。query_type 只能使用 SINGLE_DOMAIN、CROSS_DOMAIN、AGGREGATION、MULTI_STEP。"
        )
        payload = self.llm_client.generate_json(prompt=question, system_prompt=system_prompt)
        steps = [
            QueryPlanStep(
                id=item["id"],
                goal=item["goal"],
                query_type=IntentType(item["query_type"]),
                question=item["question"],
                depends_on=item.get("depends_on", []),
            )
            for item in payload.get("steps", [])[:5]
        ]
        return QueryPlan(strategy=str(payload.get("strategy", "llm_react")), steps=steps)

    def _plan_with_rules(self, question: str) -> QueryPlan:
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

    @staticmethod
    def _extract_one(text: str, candidates: list[str]) -> str:
        for candidate in candidates:
            if candidate in text:
                return candidate
        return ""
