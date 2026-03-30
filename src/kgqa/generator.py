from __future__ import annotations

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import IntentResult, SerializedResult


class AnswerGenerator:
    def __init__(self, settings: Settings, llm_client: LLMClient):
        self.settings = settings
        self.llm_client = llm_client

    def compose_with_llm(self, question: str, intent_result: IntentResult, serialized_result: SerializedResult, trace_summary: str) -> str:
        if serialized_result.row_count == 0:
            return "图谱中未找到相关信息。"

        prompt = (
            "请基于以下结构化结果，用中文给出准确、简洁、不可编造的回答。\n"
            "如果是对比或聚合问题，请明确点出关键数值。\n"
            "如果结果为空，请明确说明图谱中未找到相关信息。\n\n"
            "不要质疑输入结果的完整性，也不要添加“无法判断”“仅有一条记录因此不能判断”等保留性说明。\n"
            "如果结构化结果已经是筛选、排序或多步查询后的最终结果，直接按结果给出结论即可。\n\n"
            f"问题：{question}\n"
            f"意图：{intent_result.intent.value}\n"
            f"实体：{intent_result.entities}\n"
            f"过滤条件：{intent_result.filters}\n"
            f"执行摘要：{trace_summary}\n"
            f"结构化结果：\n{serialized_result.markdown}"
        )
        response = self.llm_client.generate(
            prompt=prompt,
            system_prompt="你是知识图谱问答助手，只能根据提供的数据回答。",
        )
        return response.content.strip()
