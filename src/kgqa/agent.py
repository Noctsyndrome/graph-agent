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
from kgqa.query import DomainRegistry, inspect_dataset_readiness
from kgqa.scenario import ScenarioDefinition, build_scenario_settings, get_scenario_definition
from kgqa.schema import SchemaRegistry
from kgqa.session import upsert_session
from kgqa.tools import KGQAToolbox

_AGENT_CACHE: dict[tuple[str, str, str, str, str, str, str, str], "KGQAAgent"] = {}
_AGENT_CACHE_LOCK = Lock()


def _agent_cache_key(settings: Settings, scenario: ScenarioDefinition) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        settings.neo4j_uri,
        settings.neo4j_username,
        settings.neo4j_password,
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        scenario.dataset_name,
        str(scenario.schema_file),
    )


def get_kgqa_agent(settings: Settings, scenario: ScenarioDefinition | None = None) -> "KGQAAgent":
    resolved_scenario = scenario or get_scenario_definition()
    scenario_settings = build_scenario_settings(settings, resolved_scenario)
    key = _agent_cache_key(scenario_settings, resolved_scenario)
    agent = _AGENT_CACHE.get(key)
    if agent is not None:
        return agent
    with _AGENT_CACHE_LOCK:
        agent = _AGENT_CACHE.get(key)
        if agent is None:
            agent = KGQAAgent(scenario_settings, resolved_scenario)
            _AGENT_CACHE[key] = agent
        return agent


def close_all_kgqa_agents() -> None:
    _AGENT_CACHE.clear()


class KGQAAgent:
    AUX_TOOLS = {"get_schema_context", "list_domain_values", "match_value", "diagnose_error"}
    MAIN_TOOLS = {"validate_cypher", "execute_cypher", "format_results"}
    AUX_BUDGET = 4
    MAIN_BUDGET = 8
    MAX_TOTAL_TURNS = AUX_BUDGET + MAIN_BUDGET + 4

    def __init__(self, settings: Settings, scenario: ScenarioDefinition):
        self.settings = settings
        self.scenario_id = scenario.scenario_id
        self.scenario_label = scenario.label
        self.dataset_name = scenario.dataset_name
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
        state.setdefault("_budget", {"aux_remaining": self.AUX_BUDGET, "main_remaining": self.MAIN_BUDGET})
        session_scenario_id = request.scenarioId or self.scenario_id
        upsert_session(
            thread_id,
            scenario_id=session_scenario_id,
            scenario_label=self.scenario_label,
            dataset_name=self.dataset_name,
            messages=messages,
            state=self._public_state(state),
            status="running",
        )

        question = self._extract_latest_user_message(messages)
        if not question:
            yield self._sse({"type": "RUN_ERROR", "message": "No user message found.", "code": "missing_user_message"})
            upsert_session(
                thread_id,
                scenario_id=session_scenario_id,
                scenario_label=self.scenario_label,
                dataset_name=self.dataset_name,
                messages=messages,
                state=self._public_state(state),
                status="failed",
            )
            return

        readiness = inspect_dataset_readiness(self.settings, self.schema.schema)
        if not readiness.get("ready", False):
            yield self._sse(
                {
                    "type": "RUN_ERROR",
                    "message": "当前场景尚未加载可用数据，请先执行对应场景的 seed/load。",
                    "code": "scenario_not_loaded",
                    "detail": readiness,
                    "timestamp": time.time(),
                }
            )
            upsert_session(
                thread_id,
                scenario_id=session_scenario_id,
                scenario_label=self.scenario_label,
                dataset_name=self.dataset_name,
                messages=messages,
                state=self._public_state(state),
                status="failed",
            )
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
            step_index = 0
            while step_index < self.MAX_TOTAL_TURNS and (
                self._has_remaining_budget(state) or formatted_result is not None or state.get("_latest_rows") is not None
            ):
                step_index += 1
                step_name = f"agent_step_{step_index}"
                yield self._sse({"type": "STEP_STARTED", "stepName": step_name, "timestamp": time.time()})
                decision = self._decide_next_action(question, messages, observations, formatted_result, state)
                decision_issue = self._validate_decision(decision)
                if decision_issue is not None:
                    observations.append(decision_issue)
                    state["toolHistory"] = list(state.get("toolHistory", [])) + [decision_issue | {"timestamp": time.time()}]
                    upsert_session(
                        thread_id,
                        scenario_id=session_scenario_id,
                        scenario_label=self.scenario_label,
                        dataset_name=self.dataset_name,
                        messages=messages,
                        state=self._public_state(state),
                        status="running",
                    )
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    continue

                if decision.get("action") == "finish":
                    if formatted_result is None and state.get("_latest_rows") is None:
                        observations.append(
                            self._decision_error(
                                code="finish_without_result",
                                message="当前还没有稳定结果，不能直接 finish。",
                                decision=decision,
                                hint="请继续使用 validate_cypher、execute_cypher、format_results，或先调用辅助工具补齐信息。",
                            )
                        )
                        yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                        continue
                    final_answer = decision.get("final_answer")
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    break

                tool_name = str(decision.get("tool_name", "")).strip()
                tool_args = decision.get("tool_args") or {}
                if not tool_name:
                    final_answer = "当前无法从图谱中推导出稳定结论。"
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    break
                if not self._has_budget_for_tool(state, tool_name):
                    observations.append(
                        self._decision_error(
                            code="budget_exhausted",
                            message=f"{tool_name} 对应的预算已耗尽。",
                            decision=decision,
                            hint=self._budget_exhausted_hint(tool_name),
                        )
                    )
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    continue

                tool_result, tool_status, messages, state = self._run_tool(
                    thread_id=thread_id,
                    messages=messages,
                    state=state,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                self._consume_budget(state, tool_name)
                for event in self.drain_buffered_events(state):
                    yield self._sse(event)
                observations.append(
                    {
                        "tool_name": tool_name,
                        "status": tool_status,
                        "tool_args": tool_args,
                        "tool_result": self._summarize_observation(tool_name, tool_result, tool_status),
                    }
                )
                if tool_name == "format_results" and tool_status == "ok":
                    formatted_result = tool_result
                upsert_session(
                    thread_id,
                    scenario_id=session_scenario_id,
                    scenario_label=self.scenario_label,
                    dataset_name=self.dataset_name,
                    messages=messages,
                    state=self._public_state(state),
                    status="running",
                )
                yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})

                if formatted_result and decision.get("auto_finish_after_format", True):
                    break

            if formatted_result is None:
                rows_payload = state.get("_latest_rows")
                if rows_payload is not None:
                    formatted_result = self.toolbox.format_results(question=question, rows=rows_payload)
                    state["latestResult"] = formatted_result

            if formatted_result is not None:
                state["latestResult"] = formatted_result
                final_answer = self.toolbox.compose_answer(question, formatted_result)
            elif not final_answer:
                final_answer = "当前未能在预算内形成稳定结论，请根据已有线索继续缩小范围或修正查询。"

            assistant_message_id = str(uuid.uuid4())
            messages.append({"id": assistant_message_id, "role": "assistant", "content": final_answer})
            for event in self._text_message_events(assistant_message_id, final_answer):
                yield self._sse(event)

            upsert_session(
                thread_id,
                scenario_id=session_scenario_id,
                scenario_label=self.scenario_label,
                dataset_name=self.dataset_name,
                messages=messages,
                state=self._public_state(state),
                status="completed",
            )
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
            upsert_session(
                thread_id,
                scenario_id=session_scenario_id,
                scenario_label=self.scenario_label,
                dataset_name=self.dataset_name,
                messages=messages,
                state=self._public_state(state),
                status="failed",
            )
            yield self._sse({"type": "RUN_ERROR", "message": str(exc), "code": "agent_failed", "timestamp": time.time()})

    def _decide_next_action(
        self,
        question: str,
        messages: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        formatted_result: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        tool_specs = self.toolbox.tool_specs()
        transcript = self._messages_for_prompt(messages)
        has_recent_schema_context = self._has_recent_schema_context(messages)
        observations_text = json.dumps(observations[-6:], ensure_ascii=False, indent=2)
        failure_text = json.dumps(
            [item for item in observations[-6:] if item.get("status") == "error"],
            ensure_ascii=False,
            indent=2,
        )
        budget_text = json.dumps(self._budget_snapshot(state), ensure_ascii=False)
        prompt = (
            f"当前问题：{question}\n\n"
            f"会话消息：\n{transcript}\n\n"
            f"工具观察：\n{observations_text}\n\n"
            f"最近失败观察（如果为空说明暂无失败）：\n{failure_text}\n\n"
            f"当前预算：{budget_text}\n\n"
            f"当前会话最近是否已有 schema 上下文：{'是' if has_recent_schema_context else '否'}\n"
            f"是否已有格式化结果：{formatted_result is not None}\n"
            f"可用工具：\n{json.dumps(tool_specs, ensure_ascii=False, indent=2)}\n\n"
            "请输出 JSON："
            '{"thought": str, "action": "call_tool|finish", "tool_name": str | null, '
            '"tool_args": object, "final_answer": str | null, "auto_finish_after_format": bool}。'
        )
        system_prompt = (
            "你是企业知识图谱问答 Agent。"
            "你的职责是通过工具逐步完成问题求解，而不是凭空回答。"
            f"当前场景数据集是 {self.dataset_name}。所有 MATCH 节点都必须显式限制 dataset = '{self.dataset_name}'。"
            "预算规则：get_schema_context、list_domain_values、match_value、diagnose_error 使用辅助预算；"
            "validate_cypher、execute_cypher、format_results 使用主预算。"
            "schema 使用策略：新问题开始、问题明显跨实体/多跳、或你对关系路径不确定时，优先调用 get_schema_context；"
            "如果当前会话最近已经有足够相关的 schema context，后续追问默认不要重复调用；"
            "只有在问题焦点明显变化，或 validate_cypher/execute_cypher 失败且怀疑 schema 理解不足时，才再次调用 get_schema_context。"
            "原则：如果对图谱结构不确定，先读取 schema、domain values 或 match_value；"
            "如果要执行 Cypher，先 validate_cypher，再 execute_cypher；"
            "如果 validate_cypher 或 execute_cypher 失败，必要时调用 diagnose_error；"
            "如果最近一次 observation.status=error，优先修复错误，不要直接 finish。"
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
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]], dict[str, Any]]:
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

        try:
            tool_result = self.toolbox.invoke(tool_name, tool_args)
        except Exception as exc:
            tool_result = {
                "status": "error",
                "error": {
                    "code": "tool_invocation_failed",
                    "message": str(exc),
                },
            }
        tool_status = self._resolve_tool_status(tool_name, tool_result)
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
                "status": tool_status,
                "tool_args": tool_args,
                "tool_result": self._summarize_observation(tool_name, tool_result, tool_status),
                "timestamp": time.time(),
            }
        ]
        if tool_name == "execute_cypher" and tool_status == "ok":
            state["_latest_rows"] = list(tool_result.get("rows", []))
        if tool_name == "format_results" and tool_status == "ok":
            state["latestResult"] = tool_result
        clean_state = self._public_state(state)

        event_bundle = [
            {"type": "TOOL_CALL_START", "toolCallId": tool_call_id, "toolCallName": tool_name, "parentMessageId": tool_parent_message_id, "timestamp": time.time()},
            {"type": "TOOL_CALL_ARGS", "toolCallId": tool_call_id, "delta": json.dumps(tool_args, ensure_ascii=False), "timestamp": time.time()},
            {"type": "TOOL_CALL_END", "toolCallId": tool_call_id, "timestamp": time.time()},
            {"type": "TOOL_CALL_RESULT", "messageId": tool_result_message_id, "toolCallId": tool_call_id, "content": result_text, "role": "tool", "timestamp": time.time()},
            {"type": "STATE_SNAPSHOT", "snapshot": clean_state, "timestamp": time.time()},
        ]
        if tool_name == "format_results" and tool_status == "ok":
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

        return tool_result, tool_status, messages, state

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
    def _has_recent_schema_context(messages: list[dict[str, Any]], max_messages: int = 24) -> bool:
        for message in reversed(messages[-max_messages:]):
            tool_calls = message.get("toolCalls") or []
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                if not isinstance(function, dict):
                    continue
                if str(function.get("name", "")).strip() == "get_schema_context":
                    return True
        return False

    @staticmethod
    def _public_state(state: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in state.items() if key not in {"_event_buffer", "_latest_rows", "_budget"}}

    def _summarize_observation(self, tool_name: str, value: dict[str, Any], status: str) -> dict[str, Any]:
        if status == "error":
            return {
                "status": "error",
                "error": value.get("error"),
                "hint": self._observation_hint(tool_name, value),
            }
        if tool_name == "execute_cypher":
            rows = list(value.get("rows", []))
            columns = list(rows[0].keys()) if rows else []
            return {
                "status": "ok",
                "row_count": int(value.get("row_count", len(rows))),
                "columns": columns,
                "first_rows": rows[:3],
            }
        text = json.dumps(value, ensure_ascii=False)
        if len(text) <= 1200:
            return {"status": "ok", "value": value}
        return {"status": "ok", "summary": text[:1200] + "..."}

    def _observation_hint(self, tool_name: str, value: dict[str, Any]) -> str | None:
        error_payload = value.get("error")
        if isinstance(error_payload, dict) and error_payload.get("hint"):
            return str(error_payload.get("hint"))
        if tool_name == "execute_cypher":
            return "请根据错误信息修正 Cypher 后重新执行 validate_cypher；必要时可调用 diagnose_error。"
        if tool_name == "validate_cypher":
            return "请参考 schema 中的实体、关系和属性名修正查询；必要时可调用 diagnose_error。"
        if tool_name == "list_domain_values":
            return "如果用户说的是简称、模糊值或别名，可改用 match_value。"
        return None

    def _validate_decision(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        action = str(decision.get("action", "finish"))
        tool_specs = {item["name"]: item for item in self.toolbox.tool_specs()}
        if action not in {"call_tool", "finish"}:
            return self._decision_error(
                code="invalid_action",
                message=f"LLM 返回了未知 action: {action}",
                decision=decision,
            )
        if action == "finish":
            return None
        tool_name = str(decision.get("tool_name") or "").strip()
        if tool_name not in tool_specs:
            return self._decision_error(
                code="invalid_tool_name",
                message=f"LLM 选择了未知工具: {tool_name or '<empty>'}",
                decision=decision,
                hint=f"请从这些工具中选择: {', '.join(tool_specs)}。",
            )
        tool_args = decision.get("tool_args") or {}
        if not isinstance(tool_args, dict):
            return self._decision_error(
                code="invalid_tool_args",
                message="tool_args 必须是 JSON object。",
                decision=decision,
            )
        required_args = [
            key
            for key, value in tool_specs[tool_name].get("args_schema", {}).items()
            if "null" not in str(value).lower()
        ]
        missing_args = [name for name in required_args if tool_args.get(name) is None]
        if missing_args:
            return self._decision_error(
                code="missing_tool_args",
                message=f"{tool_name} 缺少必要参数: {', '.join(missing_args)}",
                decision=decision,
                hint=f"{tool_name} 的参数 schema: {tool_specs[tool_name].get('args_schema', {})}",
            )
        return None

    @staticmethod
    def _decision_error(
        code: str,
        message: str,
        decision: dict[str, Any],
        hint: str | None = None,
    ) -> dict[str, Any]:
        observation = {
            "tool_name": "llm_decision",
            "status": "error",
            "tool_args": {},
            "tool_result": {
                "status": "error",
                "error": {
                    "code": code,
                    "message": message,
                    "decision": decision,
                },
            },
        }
        if hint:
            observation["tool_result"]["hint"] = hint
        return observation

    @staticmethod
    def _resolve_tool_status(tool_name: str, tool_result: dict[str, Any]) -> str:
        if tool_name == "validate_cypher":
            return "ok" if tool_result.get("valid") else "error"
        status = str(tool_result.get("status", "")).strip().lower()
        if status in {"ok", "error"}:
            return status
        if "error" in tool_result:
            return "error"
        return "ok"

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

    def _has_remaining_budget(self, state: dict[str, Any]) -> bool:
        budget = state.get("_budget", {})
        return int(budget.get("aux_remaining", 0)) > 0 or int(budget.get("main_remaining", 0)) > 0

    def _has_budget_for_tool(self, state: dict[str, Any], tool_name: str) -> bool:
        budget = state.get("_budget", {})
        if tool_name in self.AUX_TOOLS:
            return int(budget.get("aux_remaining", 0)) > 0
        if tool_name in self.MAIN_TOOLS:
            return int(budget.get("main_remaining", 0)) > 0
        return True

    def _consume_budget(self, state: dict[str, Any], tool_name: str) -> None:
        budget = state.setdefault("_budget", {"aux_remaining": self.AUX_BUDGET, "main_remaining": self.MAIN_BUDGET})
        if tool_name in self.AUX_TOOLS and int(budget.get("aux_remaining", 0)) > 0:
            budget["aux_remaining"] = int(budget.get("aux_remaining", 0)) - 1
        if tool_name in self.MAIN_TOOLS and int(budget.get("main_remaining", 0)) > 0:
            budget["main_remaining"] = int(budget.get("main_remaining", 0)) - 1

    def _budget_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        budget = state.get("_budget", {})
        return {
            "aux_remaining": int(budget.get("aux_remaining", 0)),
            "main_remaining": int(budget.get("main_remaining", 0)),
            "aux_tools": sorted(self.AUX_TOOLS),
            "main_tools": sorted(self.MAIN_TOOLS),
        }

    def _budget_exhausted_hint(self, tool_name: str) -> str:
        if tool_name in self.AUX_TOOLS:
            return "辅助预算已耗尽，请优先利用已有 schema、枚举值和错误信息完成主查询链路。"
        if tool_name in self.MAIN_TOOLS:
            return "主查询预算已耗尽，只能基于现有稳定结果 finish。"
        return "请改用其他仍有预算的工具。"
