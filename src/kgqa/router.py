from __future__ import annotations

import re

from kgqa.llm import LLMClient
from kgqa.models import IntentResult, IntentType


class RuleIntentRouter:
    def classify_intent(self, question: str) -> IntentResult:
        text = re.sub(r"\s+", "", question)

        if any(keyword in text for keyword in ["替代方案", "可替代", "R-22", "2023年后"]):
            return IntentResult(
                intent=IntentType.MULTI_STEP,
                confidence=0.95,
                reason="命中复杂条件或替代推理关键词",
                needs_multi_step=True,
            )
        if "对比" in text and "平均能效比" in text:
            return IntentResult(
                intent=IntentType.MULTI_STEP,
                confidence=0.9,
                reason="跨城市比较问题归入多步策略",
                needs_multi_step=True,
                needs_aggregation=True,
            )
        if any(keyword in text for keyword in ["占比", "最多", "最大", "排名", "平均"]):
            return IntentResult(
                intent=IntentType.AGGREGATION,
                confidence=0.9,
                reason="命中聚合统计关键词",
                needs_aggregation=True,
            )
        if any(keyword in text for keyword in ["项目", "客户", "区域", "城市", "安装了", "用了什么设备"]):
            return IntentResult(intent=IntentType.CROSS_DOMAIN, confidence=0.86, reason="命中跨域关联关键词")
        return IntentResult(intent=IntentType.SINGLE_DOMAIN, confidence=0.82, reason="默认单域精确查询")


class IntentRouter:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client
        self.rule_router = RuleIntentRouter()

    def classify_intent(self, question: str) -> IntentResult:
        if self.llm_client is not None:
            return self._classify_with_llm(question)
        return self.rule_router.classify_intent(question)

    def classify_with_rules(self, question: str) -> IntentResult:
        return self.rule_router.classify_intent(question)

    def _classify_with_llm(self, question: str) -> IntentResult:
        system_prompt = (
            "你是知识图谱问答意图分类器。请输出 JSON："
            "{\"intent\": \"SINGLE_DOMAIN|CROSS_DOMAIN|AGGREGATION|MULTI_STEP\", "
            "\"entities\": [str], \"filters\": {str: str}, "
            "\"needs_aggregation\": bool, \"needs_multi_step\": bool, "
            "\"confidence\": float, \"reason\": str}。"
            "判定原则："
            "1) 单纯问型号、参数、列表，通常是 SINGLE_DOMAIN；"
            "2) 涉及客户、项目、品牌、设备跨域关联，通常是 CROSS_DOMAIN；"
            "3) 涉及最多、占比、平均、排名、总量，通常是 AGGREGATION；"
            "4) 涉及替代方案、对比推理、多步筛选，通常是 MULTI_STEP。"
            "只输出 JSON，不要解释。"
        )
        payload = self.llm_client.generate_json(prompt=question, system_prompt=system_prompt)
        filters = self._normalize_filters({str(key): value for key, value in payload.get("filters", {}).items()})
        return IntentResult(
            intent=IntentType(payload["intent"]),
            confidence=float(payload.get("confidence", 0.0)),
            reason=str(payload.get("reason", "")),
            entities=[str(item) for item in payload.get("entities", [])],
            filters=filters,
            needs_aggregation=bool(payload.get("needs_aggregation", False)),
            needs_multi_step=bool(payload.get("needs_multi_step", False)),
        )

    @staticmethod
    def _normalize_filters(filters: dict[str, object]) -> dict[str, object]:
        normalized = dict(filters)
        for key in ("project_type", "type"):
            value = normalized.get(key)
            if isinstance(value, str):
                compact = re.sub(r"\s+", "", value)
                if compact in {"商业项目", "商业类项目"}:
                    normalized[key] = "商业"
                elif compact in {"住宅项目", "住宅类项目"}:
                    normalized[key] = "住宅"
                elif compact in {"产业园", "产业园项目", "园区项目"}:
                    normalized[key] = "产业园区"
        return normalized
