# Stage 04-8-2: 追问引用解析（简化版方案）

## 背景

`stage-04-8-1` 提出了一套更完整的方向：

- 引入 `turn artifact`
- 引入 `scope_handle`
- 增加引用解析与 scope 检视能力

这条路线方向正确，但当前实现成本偏高，主要不确定点包括：

- `query_intent` 的稳定提取效果未知
- `result_kind` 缺少清晰枚举边界
- 如果过早引入完整 artifact / scope 抽象，容易在 PoC 阶段把问题做重

因此，本阶段保留总方向，但先落地一个更轻的版本：

- 不新增完整 `turn artifact`
- 不预设完整 `query_intent / result_kind`
- 先直接复用当前已持久化的 `toolHistory`

---

## 目标

1. 让 agent 在追问时可以按需读取前序真实执行记录，而不是只依赖 transcript。
2. 优先解决“上一轮查询约束丢失导致范围扩散”的问题。
3. 不把每轮执行结果全量注入 prompt。
4. 保持 agent-native，不做关键词 hardcode 分流。

---

## 核心思路

当前系统已经持久化了 `toolHistory`，其中对 `execute_cypher` / `validate_cypher` 已保存了关键执行信息：

- `tool_args.cypher`
- `tool_result.row_count`
- `tool_result.columns`
- `tool_result.rows_preview`
- 错误修正链路

简化版方案不再额外设计一套新的查询语义模型，而是直接把 `toolHistory` 作为“可检索执行记忆”暴露给 agent。

agent 在发现当前问题可能依赖前序执行时，应主动读取最近成功查询的真实 Cypher 与结果摘要，再决定下一步查询。

---

## 为什么这版比 transcript 更有效

transcript 只保留自然语言问答，不包含：

- validate 后最终通过的 Cypher
- execute 的真实返回列
- 聚合、排序、limit 的具体表达

而 `toolHistory` 中已经保留了这些执行信息。

因此即使不新增 `turn artifact`，agent 也已经可以学到：

- 上一轮到底查了什么
- 过滤条件是什么
- 聚合和排序逻辑是什么
- 返回结果大致长什么样

对于当前暴露出的 case，这已经比单纯 Q/A transcript 强得多。

---

## 需要补充的最小存储

虽然 `toolHistory` 已有较多信息，但当前形态有两个缺口：

**缺口 1：没有用户问题关联**

`toolHistory` 存的是工具调用记录，不含触发该轮查询的用户问题。

建议在每条 `execute_cypher` 记录写入时，直接附上当轮的 user question：

```json
{
  “tool”: “execute_cypher”,
  “tool_args”: { “cypher”: “...” },
  “tool_result”: { “row_count”: 5, “columns”: [...], “rows_preview”: [...] },
  “user_question”: “奥的斯电梯用量最多的是哪个客户？”
}
```

这比事后通过 turn index 关联 `messages` 更简单，且直接可读。

**缺口 2：完整 rows 不落盘**

持久化的是 `rows_preview`，完整 `rows` 只在运行态 `_latest_rows` 中存在。

建议仅在结果集较小时保存完整 rows，例如：

- `row_count <= 20`

超出阈值时仍只保存 `rows_preview`。

这样可以覆盖大量追问场景，同时避免 `state_json` 膨胀失控。

---

## Agent 工具调整

### 新增 `inspect_recent_executions`

用于返回当前线程最近几次成功的 `execute_cypher` 记录。

建议返回（每条记录）：

- `user_question`：触发该轮查询的用户问题
- `cypher`：最终通过 validate 的 Cypher
- `row_count`
- `columns`
- `rows_preview`
- `rows`（仅当 `row_count <= 20` 且已持久化时）

返回条数建议默认取最近 3 次，避免 context 无谓膨胀。

agent 用它来读取上一轮真实执行逻辑，而不是从自然语言答案中猜约束。

**不新增 `inspect_execution_result`**

Phase 1 暂不引入按引用 ID 读取单条结果的能力。`inspect_recent_executions` 已涵盖绝大多数追问场景。若后续出现需要跨多轮精确引用特定执行结果的需求，再补充该工具。

### 暂不新增 `scope_handle`

简化版先不引入更抽象的 scope 模型。agent 先基于最近执行的 Cypher 和结果摘要承接上一轮约束。

如果后续证明这仍不足以表达”聚合结果背后的底层实体集合”（例如 TOP-k 集合需要被精确固定、不允许重新推导），再进入 `04-8-1` 的完整方案。

---

## 工具结果注入机制（实施关键）

`inspect_recent_executions` 的结果必须经过两处代码改动才能对 agent 可见，缺一不可。

### 背景：现有 observation 流转路径

工具调用结果经由以下路径注入 agent 的 prompt：

1. 工具原始返回 → `_summarize_observation(tool_name, result, status)`
2. 摘要存入 `observations` 列表
3. `_build_user_prompt` Section 5 对摘要应用 compact 白名单过滤后渲染

compact 白名单（`agent.py` ~474 行）：

```python
(“status”, “error”, “hint”, “row_count”, “columns”, “rows_preview”,
 “note”, “exact_match”, “fuzzy_matches”, “value”)
```

不在白名单内的字段会被丢弃。若摘要为空 dict，则保留原始摘要（不过滤）。

### 现有各工具的实际可见内容

| 工具 | agent 实际看到 |
|---|---|
| `get_schema_context` | `{status}` — 内容经 system prompt 另行注入，observations 中只剩 status |
| `list_domain_values` | `{status, value}` — 完整枚举摘要 |
| `match_value` | `{status, exact_match, fuzzy_matches, hint}` |
| `validate_cypher`（成功） | `{status, value: {valid, cypher}}` — cypher 完整可见 |
| `validate_cypher`（失败） | `{status, error, hint}` |
| `execute_cypher`（成功） | `{status, row_count, columns, rows_preview, note}` — 专项处理，完整保留 |
| `execute_cypher`（失败） | `{status, error, hint}` |
| `diagnose_error` | 短结果：`{status, value}`；长结果：`{status}` — summary 被白名单丢弃 ⚠️ |
| `format_results` | `{status}` — 内容由 formatted_result 另存，状态栏显示”已有格式化结果: 是” |

### `inspect_recent_executions` 不做特殊处理时的问题

若不做修改，`inspect_recent_executions` 会走 fallthrough 路径：

- 3 条执行记录（Cypher + rows_preview）的 JSON 通常远超 1200 字符
- `_summarize_observation` 截断为 `{status: “ok”, summary: “...”}`
- compact 过滤丢弃 `summary` → agent 只看到 `{status: “ok”}`

工具调了等于没调。

### 需要的两处代码修改

**修改 1：`_summarize_observation` 增加专项处理**

参考 `execute_cypher` 的写法，在 `agent.py` 的 `_summarize_observation` 方法中新增：

```python
if tool_name == “inspect_recent_executions”:
    return {
        “status”: “ok”,
        “executions”: value.get(“executions”, []),
    }
```

跳过通用截断路径，直接将结构化 executions 列表存入摘要。

**修改 2：compact 白名单加入 `executions`**

在 `_build_user_prompt` ~474 行的白名单 tuple 中追加：

```python
if k in (...现有字段..., “executions”)
```

`executions` 字段才能通过过滤，完整出现在 Section 5。

### 附：`diagnose_error` 的已知问题

`diagnose_error` 在返回内容较长时，`summary` 字段同样会被 compact 过滤丢弃，agent 只能看到 `{status: “ok”}`，修复建议完全不可见。建议在本阶段一并修复，将 `diagnose_error` 也加入 `_summarize_observation` 的专项处理，或将其返回结构限制在 1200 字符以内。

---

## Prompt 规则

### 系统 prompt（`_build_system_prompt`）

在阶段 1（理解）末尾追加，置于”如果问题是纯统计/排序/Top N 可跳过此阶段”之前：

> 如果当前问题引用了前序查询的结果（如”这几个”、”上面的”、”其中”、”这些”等），在进入阶段 2 之前先调用 `inspect_recent_executions`，从返回的真实 Cypher 还原前序约束，再在此基础上构造新查询。不得仅凭对话历史文本中的自然语言回答推断前序约束。

### 用户 prompt（`_build_user_prompt`）

将 Section 2 的对话历史标题从：

```
## 对话历史（追问时必须延续前文的查询范围和约束条件）
```

改为：

```
## 对话历史（仅供语境参考；前序查询的具体约束须通过 inspect_recent_executions 获取，不可从此处文本猜测）
```

原标题容易引导 LLM 从自然语言 transcript 中猜约束，这正是范围扩散的根源。改后明确区分两件事：对话历史提供语境，Cypher 约束由工具提供。

---

## 当前 case 下的工作方式

第一轮：

- agent 执行 top1 聚合查询
- `toolHistory` 保存最终 Cypher 与结果摘要

第二轮：

- agent 判断当前问题可能依赖前序执行
- 调用 `inspect_recent_executions`
- 读取上一轮真实 Cypher，知道其约束包含：
  - `奥的斯`
  - `Customer`
  - `COUNT(i)`
  - `ORDER BY ... DESC LIMIT 1`
- 再基于这些真实执行记录构造下一轮查询，而不是只从自然语言答案里继承 `绿城`

这版不保证一步就恢复“那 5 个底层 installation 实例集合”，但能显著减少无约束扩散。

---

## 边界

这份简化版方案主要解决的是：

- 前序查询约束丢失
- 只靠自然语言 transcript 承接导致的范围扩散

**关于当前 case 的精确性说明**

对于"奥的斯用量最多的客户是谁 → 这5个电梯分别是什么型号"这个 case，Cypher 复用实际上是**精确的，不是近似的**。

原因：上一轮 Cypher 包含完整过滤条件（`m.brand = '奥的斯'` + `c.name = '绿城'`），第二轮在这些条件上继续投影，等价于重新执行"奥的斯 + 绿城"约束下的安装集合——结果仍然是那 5 个，不会扩散到绿城其他品牌的安装记录。

Cypher 复用不足的场景是：结果集本身是动态排名的边界（如 TOP-3 客户），后续需要固定"那一批人"而非重新推导——这是 `scope_handle` 解决的问题，不属于本阶段范围。

**尚未解决的问题：**

- 多轮派生 scope（如在 TOP-3 结果上继续筛选）
- 大结果集的稳定子集引用
- 跨多轮的引用链

这些仍属于 `stage-04-8-1` 的完整方案范围。

---

## 结论

简化版方案的核心是：

- 先不重新发明一套 `turn artifact`
- 先直接把现有 `toolHistory` 变成 agent 可按需读取的执行记忆
- 只补最小必要的用户问题关联和小结果集完整 rows 持久化

实施时必须同步完成的配套改动：

1. `_summarize_observation` 为 `inspect_recent_executions` 增加专项处理
2. compact 白名单加入 `executions` 字段
3. 用户 prompt 对话历史标题调整语义
4. 系统 prompt 阶段 1 补充调用规则
5. （建议顺带）修复 `diagnose_error` 长结果被截断的问题

这样改动最小，也最贴近当前代码基线。
