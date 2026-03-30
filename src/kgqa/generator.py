from __future__ import annotations

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import IntentType, SerializedResult


class AnswerGenerator:
    def __init__(self, settings: Settings, llm_client: LLMClient | None = None):
        self.settings = settings
        self.llm_client = llm_client

    def compose_answer(self, question: str, intent: IntentType, serialized_result: SerializedResult) -> str:
        if serialized_result.row_count == 0:
            return "图谱中未找到相关信息。"

        if self.settings.has_llm and self.llm_client is not None:
            try:
                prompt = (
                    "请基于以下结构化结果，用中文给出准确、简洁、不可编造的回答。\n"
                    "如果是对比或聚合问题，请明确点出关键数值。\n\n"
                    f"问题：{question}\n"
                    f"意图：{intent.value}\n"
                    f"结构化结果：\n{serialized_result.markdown}"
                )
                response = self.llm_client.generate(
                    prompt=prompt,
                    system_prompt="你是知识图谱问答助手，只能根据提供的数据回答。",
                )
                if response.content:
                    return response.content
            except Exception:
                pass

        headline = self._headline(question, intent, serialized_result.row_count)
        return f"{headline}\n\n{serialized_result.markdown}"

    @staticmethod
    def _headline(question: str, intent: IntentType, row_count: int) -> str:
        if intent is IntentType.AGGREGATION:
            return f"已完成聚合统计查询，共得到 {row_count} 条结果。"
        if intent is IntentType.MULTI_STEP:
            return f"已完成多步查询，共整理出 {row_count} 条关键结果。"
        if "详细参数" in question or "参数" in question:
            return "已查询到目标设备的详细参数。"
        return f"已基于图谱返回 {row_count} 条相关结果。"

