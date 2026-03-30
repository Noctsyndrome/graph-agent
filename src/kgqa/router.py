from __future__ import annotations

import re

from kgqa.models import IntentResult, IntentType


class IntentRouter:
    def classify_intent(self, question: str) -> IntentResult:
        text = re.sub(r"\s+", "", question)

        if any(keyword in text for keyword in ["替代方案", "可替代", "R-22", "2023年后"]):
            return IntentResult(intent=IntentType.MULTI_STEP, confidence=0.95, reason="命中复杂条件或替代推理关键词")
        if "对比" in text and "平均能效比" in text:
            return IntentResult(intent=IntentType.MULTI_STEP, confidence=0.9, reason="跨城市比较问题归入多步策略")
        if any(keyword in text for keyword in ["占比", "最多", "最大", "排名", "平均"]):
            return IntentResult(intent=IntentType.AGGREGATION, confidence=0.9, reason="命中聚合统计关键词")
        if any(keyword in text for keyword in ["项目", "客户", "区域", "城市", "安装了", "用了什么设备"]):
            return IntentResult(intent=IntentType.CROSS_DOMAIN, confidence=0.86, reason="命中跨域关联关键词")
        return IntentResult(intent=IntentType.SINGLE_DOMAIN, confidence=0.82, reason="默认单域精确查询")

