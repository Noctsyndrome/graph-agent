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
    AUX_TOOLS = {"get_schema_context", "list_domain_values", "match_value", "diagnose_error", "inspect_recent_executions", "plan_query"}
    MAIN_TOOLS = {"validate_cypher", "execute_cypher", "format_results"}
    AUX_BUDGET = 5
    MAIN_BUDGET = 8
    MAX_TOTAL_TURNS = AUX_BUDGET + MAIN_BUDGET + 4
    FULL_ROWS_HISTORY_LIMIT = 20

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
        state["_current_turn_plan_query"] = False
        state["_current_turn_plan_description"] = None
        state["_clarify_pending"] = None
        state["_current_turn_validate_ok"] = False
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

        # ------------------------------------------------------------------
        # Pre-step: auto-inject schema context on new conversations so LLM
        # always has graph structure before making any decisions.
        # ------------------------------------------------------------------
        if not self._has_recent_schema_context(messages):
            pre_step_name = "agent_pre_step_schema"
            yield self._sse({"type": "STEP_STARTED", "stepName": pre_step_name, "timestamp": time.time()})
            schema_result, _, messages, state = self._run_tool(
                thread_id=thread_id,
                messages=messages,
                state=state,
                tool_name="get_schema_context",
                tool_args={"question": question},
            )
            # Pre-step does NOT consume budget – it's mandatory infrastructure.
            for event in self.drain_buffered_events(state):
                yield self._sse(event)
            observations.append(
                {
                    "tool_name": "get_schema_context",
                    "status": "ok",
                    "tool_args": {"question": question},
                    "tool_result": self._summarize_observation("get_schema_context", schema_result, "ok"),
                }
            )
            upsert_session(
                thread_id,
                scenario_id=session_scenario_id,
                scenario_label=self.scenario_label,
                dataset_name=self.dataset_name,
                messages=messages,
                state=self._public_state(state),
                status="running",
            )
            yield self._sse({"type": "STEP_FINISHED", "stepName": pre_step_name, "timestamp": time.time()})

        try:
            step_index = 0
            while step_index < self.MAX_TOTAL_TURNS and (
                self._has_remaining_budget(state) or formatted_result is not None or state.get("_latest_rows") is not None
            ):
                step_index += 1
                step_name = f"agent_step_{step_index}"
                yield self._sse({"type": "STEP_STARTED", "stepName": step_name, "timestamp": time.time()})
                decision = self._decide_next_action(question, messages, observations, formatted_result, state)
                decision_issue = self._validate_decision(decision, state)
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
                    yield self._sse(self._decision_issue_event(decision_issue))
                    yield self._sse({"type": "STEP_FINISHED", "stepName": step_name, "timestamp": time.time()})
                    continue

                if decision.get("action") == "finish":
                    if formatted_result is None and state.get("_latest_rows") is None and not state.get("_clarify_pending"):
                        issue = self._decision_error(
                            code="finish_without_result",
                            message="当前还没有稳定结果，不能直接 finish。",
                            decision=decision,
                            hint="请继续使用 validate_cypher、execute_cypher、format_results，或先调用辅助工具补齐信息。",
                        )
                        observations.append(issue)
                        state["toolHistory"] = list(state.get("toolHistory", [])) + [issue | {"timestamp": time.time()}]
                        yield self._sse(self._decision_issue_event(issue))
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
                    issue = self._decision_error(
                        code="budget_exhausted",
                        message=f"{tool_name} 对应的预算已耗尽。",
                        decision=decision,
                        hint=self._budget_exhausted_hint(tool_name),
                    )
                    observations.append(issue)
                    state["toolHistory"] = list(state.get("toolHistory", [])) + [issue | {"timestamp": time.time()}]
                    yield self._sse(self._decision_issue_event(issue))
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
        candidate_domain_matches = self._candidate_domain_matches(question)
        budget = self._budget_snapshot(state)
        current_phase = self._infer_current_phase(observations, formatted_result, state)
        recent_errors = [item for item in observations[-4:] if item.get("status") == "error"]

        system_prompt = self._build_system_prompt()
        prompt = self._build_user_prompt(
            question=question,
            messages=messages,
            observations=observations,
            formatted_result=formatted_result,
            tool_specs=tool_specs,
            candidate_domain_matches=candidate_domain_matches,
            budget=budget,
            current_phase=current_phase,
            recent_errors=recent_errors,
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
            if not state.get("_current_turn_plan_query"):
                return {
                    "thought": "需要先明确查询意图。",
                    "action": "call_tool",
                    "tool_name": "plan_query",
                    "tool_args": {
                        "question": question,
                        "description": f"需要先明确问题“{question}”在当前图谱中的实体、路径和约束。",
                    },
                    "auto_finish_after_format": False,
                }
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

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        schema_entities = [
            str(e.get("name", "")) for e in self.schema.schema.get("entities", [])
        ]
        schema_relationships = [
            f"({r.get('from')})-[:{r.get('name')}]->({r.get('to')})"
            for r in self.schema.schema.get("relationships", [])
        ]
        return (
            "# 角色\n"
            "你是企业知识图谱问答 Agent。你必须通过工具逐步求解，禁止凭空回答。\n"
            "只输出 JSON，不要解释。\n\n"
            "# 当前图谱概要\n"
            f"- 数据集: {self.dataset_name}\n"
            f"- 实体: {', '.join(schema_entities)}\n"
            f"- 关系: {'; '.join(schema_relationships)}\n\n"
            "# 工作流（必须按阶段推进）\n"
            "你的每一步决策必须遵循以下四阶段顺序，不可跳过前置阶段：\n\n"
            "## 阶段 1：理解（Understand）\n"
            "目标：确保你已掌握图谱结构和相关枚举值。\n"
            "- schema context 已在会话开始时自动注入，通常不需要再次调用 get_schema_context。\n"
            "- 如果问题涉及模糊值（品牌简称、类别别名等），调用 match_value 确认精确值。\n"
            "- 如果需要浏览某实体某字段的所有可选值，调用 list_domain_values(kind='Entity.field')。\n"
            "- 如果当前问题引用了前序查询的结果（如“这几个”“上面的”“其中”“这些”等），在进入阶段 2 前先调用 inspect_recent_executions，从真实 Cypher 还原前序约束；不得仅凭对话历史文本中的自然语言回答推断约束。\n"
            "- 如果问题是纯统计/排序/Top N 且不涉及模糊值，可以跳过此阶段。\n\n"
            "## 阶段 1.5：解析（Parse）\n"
            "目标：在构造 Cypher 之前，先用自然语言明确查询意图。\n"
            "- 每个问题在第一次调用 validate_cypher 之前，必须先调用 plan_query。\n"
            "- plan_query 应基于当前 schema 和当前问题生成意图描述；若此前已调用 inspect_recent_executions，其结果已在上下文中可见，可一并纳入；inspect_recent_executions 不是 plan_query 的必要前置，两者相互独立。\n"
            "- plan_query 用充实的自然语言描述：目标实体类型、图遍历路径、过滤约束、以及任何歧义的处理方式。\n"
            "- 若 plan_query 返回 needs_clarification = true，直接 finish；\n"
            "  final_answer 基于 description 改写为用户可读的澄清问题（含候选选项），不得包含图路径、实体类型等技术细节。\n"
            "- 若 plan_query 返回 needs_clarification = false，基于 description 中明确的路径和约束构造 Cypher；\n"
            "  若 description 中存在假设，在 final_answer 开头附带一句简短的理解说明。\n\n"
            "## 阶段 2：查询（Query）\n"
            "目标：生成正确的 Cypher 并执行。\n"
            "- 先 validate_cypher，通过后再 execute_cypher。绝不可跳过 validate。\n"
            f"- **关键规则**：所有 MATCH 子句中的每个节点都必须内联限定 `{{dataset: '{self.dataset_name}'}}`，例如:\n"
            f"  `MATCH (n:Entity {{dataset: '{self.dataset_name}'}})` — 不带 dataset 的 MATCH 会被 validate 拒绝。\n"
            "- 如果 validate 或 execute 失败，调用 diagnose_error 获取修复建议，然后修正 Cypher 重试。\n"
            "- 每轮重试都必须重新 validate_cypher。\n\n"
            "## 阶段 3：呈现（Present）\n"
            "目标：格式化结果并结束。\n"
            "- execute_cypher 成功返回 rows 后，调用 format_results（rows 参数将自动填充完整数据，无需手动传入）。\n"
            "- format_results 成功后，action 设为 finish。\n"
            "- 没有 rows 时不可 finish。\n\n"
            "# 禁止行为\n"
            "- 禁止在没有 schema context 的情况下调用 validate_cypher 或 execute_cypher。\n"
            "- 禁止在未调用 plan_query 的情况下直接调用 validate_cypher。\n"
            "- 禁止在 plan_query 返回 needs_clarification = true 时继续执行查询。\n"
            "- 禁止跳过 validate_cypher 直接 execute_cypher。\n"
            "- 禁止在没有成功的 execute_cypher 结果时 finish。\n"
            "- 禁止静默返回空结果而不说明理解依据。\n"
            "- 禁止连续两次用完全相同的参数调用同一个辅助工具。\n"
        )

    def _build_user_prompt(
        self,
        question: str,
        messages: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        formatted_result: dict[str, Any] | None,
        tool_specs: list[dict[str, Any]],
        candidate_domain_matches: list[dict[str, str]],
        budget: dict[str, Any],
        current_phase: str,
        recent_errors: list[dict[str, Any]],
    ) -> str:
        sections: list[str] = []
        transcript = self._messages_for_prompt(messages)

        # Section 1: question + phase
        sections.append(f"## 当前问题\n{question}")
        sections.append(f"## 当前阶段\n{current_phase}")

        # Section 2: recent transcript (Q&A pairs only, no tool noise)
        if transcript:
            sections.append(
                "## 对话历史（仅供语境参考；前序查询的具体约束须通过 inspect_recent_executions 获取，不可从此处文本猜测）\n"
                + transcript
            )

        # Section 3: candidate domain matches (compact)
        if candidate_domain_matches:
            match_lines = [
                f"- {m['entity']}.{m['field']} = \"{m['value']}\""
                for m in candidate_domain_matches
            ]
            sections.append(
                "## 问题中识别到的枚举值（已确认存在于图谱中）\n" + "\n".join(match_lines)
            )

        # Section 4: recent errors (only if any)
        if recent_errors:
            sections.append(
                "## 最近错误（必须优先处理）\n"
                + json.dumps(recent_errors, ensure_ascii=False, indent=2)
            )

        # Section 5: observation history (compact)
        if observations:
            obs_lines: list[str] = []
            for obs in observations[-6:]:
                status = obs.get("status", "ok")
                tool = obs.get("tool_name", "?")
                result_summary = obs.get("tool_result", {})
                if isinstance(result_summary, dict):
                    # Only include key fields to reduce token usage
                    compact = {
                        k: v
                        for k, v in result_summary.items()
                        if k
                        in (
                            "status",
                            "error",
                            "hint",
                            "row_count",
                            "columns",
                            "rows_preview",
                            "rows",
                            "note",
                            "exact_match",
                            "fuzzy_matches",
                            "value",
                            "executions",
                            "description",
                            "needs_clarification",
                        )
                    }
                    if compact:
                        result_summary = compact
                obs_lines.append(f"- [{status}] {tool}: {json.dumps(result_summary, ensure_ascii=False)}")
            sections.append("## 工具执行历史\n" + "\n".join(obs_lines))

        # Section 6: budget + state
        state_lines = [
            f"- 辅助工具剩余: {budget.get('aux_remaining', 0)} 次",
            f"- 主工具剩余: {budget.get('main_remaining', 0)} 次",
            f"- 已有格式化结果: {'是' if formatted_result is not None else '否'}",
        ]
        sections.append("## 当前状态\n" + "\n".join(state_lines))

        # Section 7: available tools
        sections.append(
            "## 可用工具\n" + json.dumps(tool_specs, ensure_ascii=False, indent=2)
        )

        # Section 8: Cypher writing reminder (inject when entering query phase)
        if "查询" in current_phase or "validate" in current_phase.lower():
            sections.append(
                "## Cypher 编写提醒（重要）\n"
                f"生成 Cypher 时，每个 MATCH 子句中的节点都必须添加 dataset 过滤：\n"
                f"  正确: MATCH (n:Entity {{dataset: '{self.dataset_name}'}}) ...\n"
                f"  错误: MATCH (n:Entity) WHERE n.dataset = '{self.dataset_name}' （放在 WHERE 中也可以，但内联写法更不容易遗漏）\n"
                f"  错误: MATCH (n:Entity) ... （缺少 dataset 限定，validate_cypher 会拒绝）\n"
                "如果有多个 MATCH 子句，每个节点都必须单独限定 dataset。"
            )

        # Section 9: output format
        sections.append(
            "## 输出格式\n"
            "请输出一个 JSON 对象，包含以下字段：\n"
            '{"thought": "你的推理过程", "action": "call_tool 或 finish", '
            '"tool_name": "工具名或null", "tool_args": {}, '
            '"final_answer": "最终回答或null", "auto_finish_after_format": true}'
        )

        return "\n\n".join(sections)

    @staticmethod
    def _infer_current_phase(
        observations: list[dict[str, Any]],
        formatted_result: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> str:
        """Determine which workflow phase the agent is in based on history."""
        if formatted_result is not None:
            return "阶段 3（呈现）— 已有格式化结果，应 finish。"
        if state.get("_clarify_pending"):
            return "阶段 1.5（解析）— 已生成需要澄清的候选解读，应 finish。"
        if state.get("_latest_rows") is not None:
            return "阶段 3（呈现）— 已有查询结果，应调用 format_results。"

        has_successful_execute = any(
            obs.get("tool_name") == "execute_cypher" and obs.get("status") == "ok"
            for obs in observations
        )
        if has_successful_execute:
            return "阶段 3（呈现）— 已成功执行查询，应调用 format_results。"

        has_successful_validate = any(
            obs.get("tool_name") == "validate_cypher" and obs.get("status") == "ok"
            for obs in observations
        )
        if has_successful_validate:
            return "阶段 2（查询）— 已通过校验，应调用 execute_cypher。"

        has_successful_plan_query = any(
            obs.get("tool_name") == "plan_query" and obs.get("status") == "ok"
            for obs in observations
        )
        if has_successful_plan_query:
            return "阶段 2（查询）— 已完成意图解析，应调用 validate_cypher。"

        has_schema = any(
            obs.get("tool_name") == "get_schema_context" and obs.get("status") == "ok"
            for obs in observations
        )
        has_any_query_attempt = any(
            obs.get("tool_name") in ("validate_cypher", "execute_cypher")
            for obs in observations
        )
        if has_schema and not has_any_query_attempt:
            return "阶段 1.5（解析）— 已有 schema，如需前序约束可先 inspect_recent_executions，然后调用 plan_query。"

        if has_any_query_attempt:
            last_error = None
            for obs in reversed(observations):
                if obs.get("status") == "error":
                    last_error = obs
                    break
            if last_error:
                return "阶段 2（查询）— 上一次查询失败，需要修正 Cypher 后重试（可调用 diagnose_error 获取帮助）。"
            return "阶段 2（查询）— 正在构建查询。"

        return "阶段 1（理解）— 需要先理解图谱结构，schema 已自动注入，检查是否需要确认枚举值后再构建 Cypher。"

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

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
        visible_tool_args = dict(tool_args)

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
                        "arguments": json.dumps(visible_tool_args, ensure_ascii=False),
                    },
                }
            ],
        }
        messages.append(assistant_tool_message)

        # Auto-supply full rows for format_results from latest execute_cypher
        # so the LLM's truncated observation doesn't cause data loss.
        invocation_tool_args = dict(visible_tool_args)
        if tool_name == "format_results" and state.get("_latest_rows") is not None:
            invocation_tool_args = {**invocation_tool_args, "rows": state["_latest_rows"]}
        if tool_name == "inspect_recent_executions":
            invocation_tool_args = {
                **invocation_tool_args,
                "tool_history": list(state.get("toolHistory", [])),
            }

        try:
            tool_result = self.toolbox.invoke(tool_name, invocation_tool_args)
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

        history_item = {
            "tool_name": tool_name,
            "status": tool_status,
            "tool_args": visible_tool_args,
            "tool_result": self._summarize_observation(tool_name, tool_result, tool_status),
            "timestamp": time.time(),
        }
        if tool_name == "execute_cypher" and tool_status == "ok":
            history_item["user_question"] = self._extract_latest_user_message(messages[:-1])
            state["_latest_rows"] = list(tool_result.get("rows", []))
            graph_delta = self._graph_delta_from_cypher(visible_tool_args.get("cypher"))
            history_item["graph_delta"] = graph_delta
            state["_latest_graph_delta"] = graph_delta
        if tool_name == "validate_cypher":
            state["_current_turn_validate_ok"] = tool_status == "ok"
        if tool_name == "plan_query" and tool_status == "ok":
            description = str(tool_result.get("description", "")).strip()
            state["_current_turn_plan_query"] = True
            state["_current_turn_plan_description"] = description
            if bool(tool_result.get("needs_clarification", False)):
                state["_clarify_pending"] = description
        state["toolHistory"] = list(state.get("toolHistory", [])) + [history_item]
        if tool_name == "format_results" and tool_status == "ok":
            state["latestResult"] = tool_result
        clean_state = self._public_state(state)

        event_bundle = [
            {"type": "TOOL_CALL_START", "toolCallId": tool_call_id, "toolCallName": tool_name, "parentMessageId": tool_parent_message_id, "timestamp": time.time()},
            {"type": "TOOL_CALL_ARGS", "toolCallId": tool_call_id, "delta": json.dumps(visible_tool_args, ensure_ascii=False), "timestamp": time.time()},
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
                    "graph_delta": state.get("_latest_graph_delta"),
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
        """Extract user questions and assistant final answers, skipping tool
        call / tool result noise so the context window covers more turns."""
        qa_lines: list[str] = []
        for message in messages:
            role = str(message.get("role", "unknown"))
            if role == "tool":
                continue
            if role == "assistant" and message.get("toolCalls"):
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
            content = str(content).strip()
            if not content:
                continue
            qa_lines.append(f"[{role}] {content}")
        # Last 10 Q&A entries ≈ 5 full conversation turns
        return "\n".join(qa_lines[-10:])

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
        return {
            key: value
            for key, value in state.items()
            if key
            not in {
                "_event_buffer",
                "_latest_rows",
                "_latest_graph_delta",
                "_budget",
                "_current_turn_plan_query",
                "_current_turn_plan_description",
                "_clarify_pending",
                "_current_turn_validate_ok",
            }
        }

    def _graph_delta_from_cypher(self, cypher: Any) -> dict[str, Any]:
        active_types = self.schema.extract_active_types(str(cypher or ""))
        return {
            "active_types": active_types,
            "nodes": [],
            "edges": [],
            "inference_level": "exact",
        }

    def _summarize_observation(self, tool_name: str, value: dict[str, Any], status: str) -> dict[str, Any]:
        if status == "error":
            return {
                "status": "error",
                "error": value.get("error"),
                "hint": self._observation_hint(tool_name, value),
            }
        if tool_name == "list_domain_values":
            return {
                "status": "ok",
                "value": self._summarize_domain_values(value),
            }
        if tool_name == "match_value":
            return {
                "status": "ok",
                "entity": value.get("entity"),
                "field": value.get("field"),
                "keyword": value.get("keyword"),
                "exact_match": value.get("exact_match"),
                "fuzzy_matches": list(value.get("fuzzy_matches", []))[:5],
                "hint": value.get("hint"),
            }
        if tool_name == "inspect_recent_executions":
            executions = value.get("executions", [])
            return {
                "status": "ok",
                "executions": list(executions) if isinstance(executions, list) else [],
            }
        if tool_name == "plan_query":
            return {
                "status": "ok",
                "description": str(value.get("description", "")),
                "needs_clarification": bool(value.get("needs_clarification", False)),
            }
        if tool_name == "diagnose_error":
            return {
                "status": "ok",
                "value": value,
            }
        if tool_name == "execute_cypher":
            rows = list(value.get("rows", []))
            row_count = int(value.get("row_count", len(rows)))
            columns = list(rows[0].keys()) if rows else []
            preview_limit = min(len(rows), self.FULL_ROWS_HISTORY_LIMIT)
            result: dict[str, Any] = {
                "status": "ok",
                "row_count": row_count,
                "columns": columns,
                "rows_preview": rows[:preview_limit],
            }
            if row_count <= self.FULL_ROWS_HISTORY_LIMIT:
                result["rows"] = rows
            if row_count > preview_limit:
                result["note"] = (
                    f"共 {row_count} 行，此处仅展示前 {preview_limit} 行预览。"
                    "完整数据已保存，format_results 将自动使用全部数据。"
                )
            else:
                result["note"] = f"共 {row_count} 行（完整数据）。format_results 将自动使用全部数据。"
            return result
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
            return "如果已经拿到完整枚举值，不要重复调用相同的 list_domain_values；下一步应改用 match_value、validate_cypher，或指定更窄的 Entity.field。"
        if tool_name == "inspect_recent_executions":
            return "最近执行记录刚刚已经查看过，请基于其中的 Cypher 和结果摘要继续构造查询。"
        if tool_name == "plan_query":
            return "当前问题的查询意图刚刚已经解析过，请基于 description 构造 Cypher 或直接澄清。"
        return None

    def _validate_decision(self, decision: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
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
        if state.get("_clarify_pending") and tool_name != "plan_query":
            return self._decision_error(
                code="clarify_requires_finish",
                message="plan_query 已给出需要澄清的候选解读，当前不能继续调用查询工具。",
                decision=decision,
                hint="请直接 finish，并将 CLARIFY 描述作为 final_answer 返回给用户。",
            )
        if tool_name == "validate_cypher" and not bool(state.get("_current_turn_plan_query")):
            return self._decision_error(
                code="missing_plan_query",
                message="调用 validate_cypher 之前必须先调用 plan_query。",
                decision=decision,
                hint="请先调用 plan_query，用自然语言明确目标实体、图路径、过滤约束和歧义处理。",
            )
        if tool_name == "execute_cypher" and not bool(state.get("_current_turn_validate_ok")):
            return self._decision_error(
                code="missing_validate_cypher",
                message="调用 execute_cypher 之前必须先成功执行 validate_cypher。",
                decision=decision,
                hint="请先调用 validate_cypher；如果 validate 失败，先修正 Cypher 再重试。",
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
        last_tool_call = self._last_tool_history_item(state)
        if (
            tool_name in self.AUX_TOOLS
            and last_tool_call
            and str(last_tool_call.get("tool_name", "")) == tool_name
            and (last_tool_call.get("tool_args") or {}) == tool_args
        ):
            return self._decision_error(
                code="redundant_aux_tool_call",
                message=f"{tool_name} 与上一轮辅助工具调用完全相同。",
                decision=decision,
                hint=self._redundant_aux_tool_hint(tool_name, tool_args),
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
    def _decision_issue_event(issue: dict[str, Any]) -> dict[str, Any]:
        error_payload = issue.get("tool_result", {}).get("error", {}) if isinstance(issue.get("tool_result"), dict) else {}
        return {
            "type": "DECISION_ISSUE",
            "code": error_payload.get("code"),
            "message": error_payload.get("message"),
            "hint": issue.get("tool_result", {}).get("hint") if isinstance(issue.get("tool_result"), dict) else None,
            "decision": error_payload.get("decision"),
            "timestamp": time.time(),
        }

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

    @staticmethod
    def _last_tool_history_item(state: dict[str, Any]) -> dict[str, Any] | None:
        history = state.get("toolHistory", [])
        if not isinstance(history, list) or not history:
            return None
        last_item = history[-1]
        return last_item if isinstance(last_item, dict) else None

    @staticmethod
    def _redundant_aux_tool_hint(tool_name: str, tool_args: dict[str, Any]) -> str:
        if tool_name == "list_domain_values" and not tool_args:
            return "已经拿到完整枚举值，请改为指定具体 Entity.field、调用 match_value，或直接进入 validate_cypher。"
        if tool_name == "get_schema_context":
            return "最近一轮已经读取过 schema context；除非问题焦点明显变化，否则请直接利用现有 schema 继续推理。"
        if tool_name == "match_value":
            return "相同的模糊值匹配刚刚已经执行过，请使用返回的 exact_match/fuzzy_matches 继续构造查询。"
        if tool_name == "diagnose_error":
            return "相同错误诊断刚刚已经执行过，请根据 suggestion 修正 Cypher。"
        return "请避免重复辅助调用，改为利用已有上下文进入主查询链路。"

    @staticmethod
    def _summarize_domain_values(value: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for entity_name, field_map in value.items():
            if not isinstance(field_map, dict):
                continue
            entity_summary: dict[str, Any] = {}
            for field_name, field_values in field_map.items():
                values = list(field_values) if isinstance(field_values, list) else []
                entity_summary[str(field_name)] = {
                    "count": len(values),
                    "sample": values[:5],
                }
            summary[str(entity_name)] = entity_summary
        return summary

    def _candidate_domain_matches(self, question: str, limit: int = 8) -> list[dict[str, str]]:
        text = question.replace(" ", "").strip()
        if not text:
            return []
        matches: list[dict[str, str]] = []
        for entity_name, field_map in self.domain.as_dict().items():
            for field_name, values in field_map.items():
                for value in values:
                    value_text = str(value).strip()
                    if not value_text:
                        continue
                    if value_text.replace(" ", "") not in text:
                        continue
                    matches.append(
                        {
                            "entity": entity_name,
                            "field": field_name,
                            "value": value_text,
                        }
                    )
                    if len(matches) >= limit:
                        return matches
        return matches
