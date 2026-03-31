# 战略决策：False Case 修正 vs Agent 架构演进

## Context

项目已完成三个阶段，纯 LLM 管线通过率 85.4%（41/48），PoC 核心论点——"NL → Graph → Answer 由 LLM 驱动可行"——已验证。现在面临路线选择：是投入 Codex 提出的 Grounding 子系统方案修正剩余 7 个失败用例，还是着手构建真正的 Agent 架构？

---

## 1. Codex 方案评估

Codex 的 `stage-03-3-false-case-optimization-plan.md` 提出了 7 个子系统，约 670 行新增代码。按 ROI 拆解：

### 值得现在做（小投入、高杠杆、架构无关）

| 措施 | 改动量 | 解决的问题 |
|---|---|---|
| 向 CypherGenerator prompt 注入 domain 枚举值 | ~20 行 query.py | S4-2, C5, C7, C19, G15（全部 5 个 ERROR） |
| DomainRegistry 新增 `project_statuses` | ~5 行 query.py | G15 |
| eval 关键词匹配支持 column_aliases | ~40 行 run_eval.py | C16, 可能 C12 |
| 补充 3-4 条失败模式 few-shot | ~30 行 few_shots.yaml | 巩固 grounding 效果 |

**合计 ~95 行，约 2 小时。预期将通过率推到 89-96%。**

### 应该跳过（在 Agent 架构下会被自然取代）

| 措施 | 被取代原因 |
|---|---|
| GroundingResolver 子系统 (~200 行) | Agent 调用 `list_domain_values` 工具，自然完成 grounding |
| CypherDiagnosis 后验校验 (~100 行) | Agent 的 ReAct 循环本身就是"执行→观察→修正"的诊断循环 |
| field_semantics / value_aliases 配置体系 | Agent 通过工具描述理解字段语义，不需要独立配置层 |
| 扩展 trace 可观测性 | Agent 架构需要全新的 tool_call trace 模型 |

**结论：Codex 方案 ~70% 的代码量在 Agent 架构下需要重写，不值得现在投入。**

---

## 2. 推荐路线：快速收尾 → Agent 演进

### Stage 3.4 — 轻量收尾（~2 小时）

1. **重跑 eval**：当前 report.html 是旧代码的结果（C5/C19 可能已修复），先拿到准确基线
2. **eval 别名匹配**：`run_eval.py` 的 `must_include` 检查加载 `schema.yaml` 的 `column_aliases`，扩展关键词匹配
3. **Cypher prompt 注入枚举值**：在 `query.py` `generate_with_llm()` 的 prompt 中附加 domain 枚举值摘要（categories, project_types, cities, statuses, brands）
4. **DomainRegistry 补充 `project_statuses`**
5. **补 3-4 条 archetype few-shot**：城市绑定、组合实体、口语类目、时间排序
6. **再次跑 eval + 记录最终基线**

### Stage 4 — Agent 架构（核心演进）

**新增核心组件：**

| 文件 | 职责 |
|---|---|
| `src/kgqa/agent.py` | AgentOrchestrator — ReAct 循环（观察→决策→执行→反思） |
| `src/kgqa/tools.py` | Tool 定义与注册表，包装现有组件为 agent 可调用工具 |
| `src/kgqa/session.py` | 会话管理，多轮对话历史存储 |

**现有组件复用映射：**

| 现有组件 | Agent 中的角色 |
|---|---|
| `Neo4jExecutor.query()` | → `execute_cypher` 工具 |
| `DomainRegistry` 各属性 | → `list_domain_values` 工具（直接解决 grounding 问题） |
| `CypherSafetyValidator.validate()` | → `validate_cypher` 工具 |
| `SchemaRegistry.render_schema_context()` | → `get_schema_context` 工具 |
| `ResultSerializer.serialize()` | → `format_results` 工具 |
| `LLMClient` (连接池/JSON解析) | → 扩展 `generate_with_tools()` 方法 |
| `progress_callback` 模式 | → 演进为 agent reasoning 流式输出 |
| `_QUERY_JOB_STORE` 模式 | → session store 的模板 |

**需要重建的部分：**

| 现有代码 | 原因 |
|---|---|
| `KGQAService.process_question()` | 固定线性管线 → 被 agent 动态循环取代 |
| `QueryPlanner` | 显式多步规划 → agent 隐式规划（选择调用什么工具） |
| `ui/app.py` | 表单式单问 → `st.chat_input` / `st.chat_message` 对话式 |

**实施顺序：**

```
Stage 3.4 轻量收尾 (~2h)
    │
    ▼
Phase 4.1 Agent 核心 + 工具 + 会话 (~4h)
    ├── agent.py: ReAct 循环 (max 5 轮)
    ├── tools.py: 包装现有组件
    ├── session.py: 内存会话管理
    └── llm.py: 扩展 multi-turn + tool calling
    │
    ▼
Phase 4.2 Chat API (~3h)
    └── api.py: POST /chat, GET /chat/{session_id}/messages
    │
    ▼
Phase 4.3 Chat UI (~3h)
    └── ui/app.py: 对话界面 + tool call 展示 + 侧边栏用例选择保留
    │
    ▼
Phase 4.4 图谱可视化（可选/并行）(~3h)
    └── visualizer.py + vis.js 渲染
    │
    ▼
Phase 4.5 集成测试 + 评估更新 (~2h)
Phase 4.6 文档 (~1h)
```

**关键设计决策：**

- **Tool calling 格式**：优先使用 Qwen 的 OpenAI 兼容 function calling。若不可靠，回退到 JSON 模式（现有 `generate_json()` 已验证可行）
- **Agent 迭代上限**：5 轮（与现有 `plan.steps[:5]` 一致）
- **向后兼容**：保留 `/query` 端点不变，agent 模式通过新的 `/chat` 端点提供
- **会话存储**：PoC 阶段用内存 dict，与现有 `_QUERY_JOB_STORE` 模式一致

---

## 3. 关键文件清单

| 文件 | Stage 3.4 改动 | Stage 4 改动 |
|---|---|---|
| `src/kgqa/query.py` | prompt 注入枚举值, DomainRegistry +statuses | → tool 包装 |
| `eval/run_eval.py` | alias 匹配修复 | agent 模式评估 |
| `data/few_shots.yaml` | +3-4 条 archetype | 不变 |
| `src/kgqa/llm.py` | — | +generate_with_tools() |
| `src/kgqa/agent.py` | — | 新建 |
| `src/kgqa/tools.py` | — | 新建 |
| `src/kgqa/session.py` | — | 新建 |
| `src/kgqa/models.py` | — | +AgentMessage, Session 等 |
| `src/kgqa/api.py` | — | +/chat 端点 |
| `ui/app.py` | — | 重写为对话式 |

---

## 4. 验证方式

- **Stage 3.4 验证**：`python eval/run_eval.py`，预期通过率 ≥ 89%
- **Phase 4.1 验证**：单元测试 — agent 对 baseline 前 3 条用例能正确调用工具并返回答案
- **Phase 4.3 验证**：UI 手动测试 — 能进行 2-3 轮连续对话，上下文被正确保持
- **Phase 4.5 验证**：全量 eval 48 条，agent 模式通过率 ≥ 85%（不低于现有基线）
