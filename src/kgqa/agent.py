from __future__ import annotations

import json
import time
import uuid
from copy import deepcopy
from threading import Lock
from typing import Any, Iterator

from kgqa.config import Settings
from kgqa.llm import LLMClient
from kgqa.models import ChatRequest
from kgqa.query import DomainRegistry
from kgqa.schema import SchemaRegistry
from kgqa.session import upsert_session
from kgqa.tools import KGQAToolbox

_AGENT_CACHE: dict[tuple[str, str, str, str, str, str, str], "KGQAAgent"] = {}
_AGENT_CACHE_LOCK = Lock()


def _agent_cache_key(settings: Settings) -> tuple[str, str, str, str, str, str, str]:
    return (
        settings.neo4j_uri,
        settings.neo4j_username,
        settings.neo4j_password,
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.dataset_name,
    )


def get_kgqa_agent(settings: Settings) -> "KGQAAgent":
    key = _agent_cache_key(settings)
    agent = _AGENT_CACHE.get(key)
    if agent is not None:
        return agent
    with _AGENT_CACHE_LOCK:
        agent = _AGENT_CACHE.get(key)
        if agent is None:
            agent = KGQAAgent(settings)
            _AGENT_CACHE[key] = agent
        return agent


def close_all_kgqa_agents() -> None:
    _AGENT_CACHE.clear()


class KGQAAgent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm_client = LLMClient(settings)
        self.domain = DomainRegistry(settings)
        self.domain.load()
        self.schema = SchemaRegistry(settings, domain=self.domain)
        self.toolbox = KGQAToolbox(settings, self.schema, self.domain, self.llm_client)

    def stream_chat(self, request: ChatRequest) -> Iterator[str]:
        thread_id = request.threadId or str(uuid.uuid4())
        run_id = request.runId or str(uuid.uuid4())
        messages = deepcopy(request.messages)
        state = deepcopy(request.state)
        state.setdefault("toolHistory", [])
        upsert_session(thread_id, messages=messages, state=self._public_state(state), status="running")

        question = self._extract_latest_user_message(messages)
        if not question:
            yield self._sse({"type": "RUN_ERROR", "message": "No user message found.", "code": "missing_user_message"})
            upsert_session(thread_id, messages=messages, state=self._public_state(state), status="failed")
            return

        yield self._sse(
            {
                "type": "RUN_STARTED",
                "threadId": thread_id,
                "runId": run_id,
                "timestamp": time.time(),
                "input": request.model_dump(),
            }
        )

        observations: list[dict[str, Any]] = []
        formatted_result: dict[str, Any] | None = None
        final_answer: str | None = None

        try:
            for step_index in range(1, 6):
                step_name = f"agent_step_{step_index}"
                yield self._sse({"type": "STEP_STARTED", "stepName": step_name, "timestamp": time.time()})
                decision = self._decide_next_action(question, messages, observations, formatted_result)

                if decision.get("action") == "finish":
                    final_answer = decision.get("final_answer")
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    break

                tool_name = str(decision.get("tool_name", "")).strip()
                tool_args = decision.get("tool_args") or {}
                if not tool_name:
                    final_answer = "当前无法从图谱中推导出稳定结论。"
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    break

                tool_result, messages, state = self._run_tool(
                    thread_id=thread_id,
                    messages=messages,
                    state=state,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                for event in self.drain_buffered_events(state):
                    yield self._sse(event)
                observations.append(
                    {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_result": self._summarize_observation(tool_result),
                    }
                )
                if tool_name == "format_results":
                    formatted_result = tool_result
                upsert_session(thread_id, messages=messages, state=self._public_state(state), status="running")
                yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})

                if formatted_result and decision.get("auto_finish_after_format", True):
                    break

            if formatted_result is None:
                rows_payload = self._last_rows_from_observations(observations)
                if rows_payload is not None:
                    formatted_result = self.toolbox.format_results(question=question, rows=rows_payload)
                    state["latestResult"] = formatted_result

            if formatted_result is not None:
                state["latestResult"] = formatted_result
                final_answer = self.toolbox.compose_answer(question, formatted_result)
            elif not final_answer:
                final_answer = "图谱中未找到相关信息。"

            assistant_message_id = str(uuid.uuid4())
            messages.append({"id": assistant_message_id, "role": "assistant", "content": final_answer})
            for event in self._text_message_events(assistant_message_id, final_answer):
                yield self._sse(event)

            upsert_session(thread_id, messages=messages, state=self._public_state(state), status="completed")
            yield self._sse(
                {
                    "type": "RUN_FINISHED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "timestamp": time.time(),
                    "result": {"session_id": thread_id, "message_id": assistant_message_id},
                }
            )
        except Exception as exc:
            upsert_session(thread_id, messages=messages, state=self._public_state(state), status="failed")
            yield self._sse({"type": "RUN_ERROR", "message": str(exc), "code": "agent_failed", "timestamp": time.time()})

    def _decide_next_action(
        self,
        question: str,
        messages: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        formatted_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tool_specs = self.toolbox.tool_specs()
        transcript = self._messages_for_prompt(messages)
        observations_text = json.dumps(observations[-6:], ensure_ascii=False, indent=2)
        prompt = (
            f"当前问题：{question}\n\n"
            f"会话消息：\n{transcript}\n\n"
            f"工具观察：\n{observations_text}\n\n"
            f"是否已有格式化结果：{formatted_result is not None}\n"
            f"可用工具：\n{json.dumps(tool_specs, ensure_ascii=False, indent=2)}\n\n"
            "请输出 JSON："
            '{"thought": str, "action": "call_tool|finish", "tool_name": str | null, '
            '"tool_args": object, "final_answer": str | null, "auto_finish_after_format": bool}。'
        )
        system_prompt = (
            "你是企业知识图谱问答 Agent。"
            "你的职责是通过工具逐步完成问题求解，而不是凭空回答。"
            "原则：如果对图谱结构不确定，先读取 schema 或 domain values；"
            "如果要执行 Cypher，先 validate_cypher，再 execute_cypher；"
            "拿到 rows 后优先调用 format_results。"
            "只有在已有足够结果时才能 finish。"
            "只输出 JSON，不要解释。"
        )
        try:
            payload = self.llm_client.generate_json(prompt=prompt, system_prompt=system_prompt)
        except Exception:
            if not observations:
                return {
                    "thought": "需要先读取 schema。",
                    "action": "call_tool",
                    "tool_name": "get_schema_context",
                    "tool_args": {"question": question},
                    "auto_finish_after_format": False,
                }
            if formatted_result is not None:
                return {"thought": "已有最终结构化结果。", "action": "finish", "final_answer": None}
            return {
                "thought": "需要读取领域枚举值。",
                "action": "call_tool",
                "tool_name": "list_domain_values",
                "tool_args": {},
                "auto_finish_after_format": False,
            }

        return {
            "thought": str(payload.get("thought", "")),
            "action": str(payload.get("action", "finish")),
            "tool_name": payload.get("tool_name"),
            "tool_args": payload.get("tool_args") or {},
            "final_answer": payload.get("final_answer"),
            "auto_finish_after_format": bool(payload.get("auto_finish_after_format", True)),
        }

    def _run_tool(
        self,
        thread_id: str,
        messages: list[dict[str, Any]],
        state: dict[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        tool_call_id = str(uuid.uuid4())
        tool_parent_message_id = str(uuid.uuid4())
        tool_result_message_id = str(uuid.uuid4())

        assistant_tool_message = {
            "id": tool_parent_message_id,
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_args, ensure_ascii=False),
                    },
                }
            ],
        }
        messages.append(assistant_tool_message)

        tool_result = self.toolbox.invoke(tool_name, tool_args)
        result_text = json.dumps(tool_result, ensure_ascii=False)
        messages.append(
            {
                "id": tool_result_message_id,
                "role": "tool",
                "toolCallId": tool_call_id,
                "content": result_text,
            }
        )

        state["toolHistory"] = list(state.get("toolHistory", [])) + [
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": self._summarize_observation(tool_result),
                "timestamp": time.time(),
            }
        ]
        if tool_name == "format_results":
            state["latestResult"] = tool_result
        clean_state = {key: value for key, value in state.items() if key != "_event_buffer"}

        event_bundle = [
            {"type": "TOOL_CALL_START", "toolCallId": tool_call_id, "toolCallName": tool_name, "parentMessageId": tool_parent_message_id, "timestamp": time.time()},
            {"type": "TOOL_CALL_ARGS", "toolCallId": tool_call_id, "delta": json.dumps(tool_args, ensure_ascii=False), "timestamp": time.time()},
            {"type": "TOOL_CALL_END", "toolCallId": tool_call_id, "timestamp": time.time()},
            {"type": "TOOL_CALL_RESULT", "messageId": tool_result_message_id, "toolCallId": tool_call_id, "content": result_text, "role": "tool", "timestamp": time.time()},
            {"type": "STATE_SNAPSHOT", "snapshot": clean_state, "timestamp": time.time()},
        ]
        if tool_name == "format_results":
            event_bundle.append(
                {
                    "type": "CUSTOM",
                    "name": "kgqa_ui_payload",
                    "value": tool_result,
                    "timestamp": time.time(),
                }
            )
        for event in event_bundle:
            # Yield marker events by storing them inside state for the API layer to flush.
            state.setdefault("_event_buffer", []).append(event)

        return tool_result, messages, state

    @staticmethod
    def drain_buffered_events(state: dict[str, Any]) -> list[dict[str, Any]]:
        buffered = list(state.get("_event_buffer", []))
        state["_event_buffer"] = []
        return buffered

    @staticmethod
    def _extract_latest_user_message(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
            return str(content).strip()
        return ""

    @staticmethod
    def _messages_for_prompt(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages[-10:]:
            role = str(message.get("role", "unknown"))
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _public_state(state: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in state.items() if key != "_event_buffer"}

    @staticmethod
    def _summarize_observation(value: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(value, ensure_ascii=False)
        if len(text) <= 600:
            return value
        return {"summary": text[:600] + "..."}

    @staticmethod
    def _last_rows_from_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        for item in reversed(observations):
            result = item.get("tool_result", {})
            if isinstance(result, dict) and "rows" in result:
                rows = result.get("rows")
                if isinstance(rows, list):
                    return rows
        return None

    @staticmethod
    def _text_message_events(message_id: str, content: str, chunk_size: int = 80) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = [
            {"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant", "timestamp": time.time()}
        ]
        for start in range(0, len(content), chunk_size):
            events.append(
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": message_id,
                    "delta": content[start : start + chunk_size],
                    "timestamp": time.time(),
                }
            )
        events.append({"type": "TEXT_MESSAGE_END", "messageId": message_id, "timestamp": time.time()})
        return events

    @staticmethod
    def _sse(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
