from __future__ import annotations

import re

from kgqa.llm import LLMClient
from kgqa.models import IntentResult, IntentType
from kgqa.query import DomainRegistry


class IntentRouter:
    def __init__(self, llm_client: LLMClient | None, domain: DomainRegistry | None = None):
        self.llm_client = llm_client
        self.domain = domain

    def classify_intent(self, question: str) -> IntentResult:
        return self._classify_with_llm(question)

    def _classify_with_llm(self, question: str) -> IntentResult:
        filter_keys = "customer, brand, city, project_type, project_status, category, refrigerant, project_name, model_name"
        domain_summary = self._domain_prompt_summary()
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
            f" filters 常见字段只允许使用：{filter_keys}。"
            " 如果问题中的短语更像城市、项目类型、项目状态、设备类别或制冷剂枚举值，"
            "优先填入对应枚举字段，不要误归类为 project_name。"
            "只输出 JSON，不要解释。"
        )
        prompt = question
        if domain_summary:
            prompt = f"{domain_summary}\n\n问题：{question}"
        payload = self.llm_client.generate_json(prompt=prompt, system_prompt=system_prompt)
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

    def _normalize_filters(self, filters: dict[str, object]) -> dict[str, object]:
        normalized = dict(filters)
        for key in ("project_type", "type"):
            value = normalized.get(key)
            if isinstance(value, str):
                matched = self._match_project_type(value)
                if matched:
                    normalized[key] = matched
        return normalized

    def _domain_prompt_summary(self) -> str:
        if not self.domain:
            return ""
        sections = [
            ("客户名", self.domain.customers),
            ("品牌", self.domain.brands),
            ("城市", self.domain.cities),
            ("项目类型", self.domain.project_types),
            ("项目状态", self.domain.project_statuses),
            ("设备类别", self.domain.categories),
            ("制冷剂", self.domain.refrigerants),
        ]
        lines = ["当前图谱中的关键枚举值："]
        has_values = False
        for label, values in sections:
            if not values:
                continue
            has_values = True
            preview = "、".join(values[:10])
            suffix = " ..." if len(values) > 10 else ""
            lines.append(f"- {label}: {preview}{suffix}")
        return "\n".join(lines) if has_values else ""

    def _match_project_type(self, value: str) -> str | None:
        if not self.domain:
            return None
        compact = self._compact_type_label(value)
        for candidate in self.domain.project_types:
            candidate_compact = self._compact_type_label(candidate)
            if compact == candidate_compact or compact.startswith(candidate_compact) or candidate_compact.startswith(compact):
                return candidate
        return None

    @staticmethod
    def _compact_type_label(value: str) -> str:
        compact = re.sub(r"\s+", "", value)
        for suffix in ("类项目", "项目"):
            if compact.endswith(suffix):
                compact = compact[: -len(suffix)]
        return compact
