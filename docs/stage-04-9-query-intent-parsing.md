# Stage 04-9: 查询意图解析（Query Intent Parsing）

## 背景

`stage-04-8-2` 引入了 `inspect_recent_executions`，解决了前序查询约束丢失导致的范围扩散问题。

但在验证中暴露出更深一层的问题：

> 用户提问"请告诉我其中跟海底捞签订的合同具体信息"，agent 调用了 `inspect_recent_executions`，也拿到了前序 Cypher，但仍然返回空结果。用户被迫手动解释图语义（"合同关联的空间是海底捞在天街杭州店所在的空间"），agent 才得以成功查询。

这说明当前 agent 的问题不只是"约束继承"，更在于：

- 自然语言词没有被正确映射到 schema 实体类型（"合同" → `租赁合同`）
- 多跳图路径没有被提前确认（`项目空间 → 租赁合同 → 租户`）
- "其中"的指向没有被明确解析
- 歧义未主动澄清，而是静默执行、返回空结果

这些都是**语义理解**层面的问题，不是 Cypher 语法层面的问题。Agent 在语义层模糊时，反复在语法层试错，造成多次 validate/execute 重试。

---

## 目标

1. 在构造 Cypher 之前，让 agent 先显式解析查询意图。
2. 把隐式推理（藏在 thought 里）变成显式可观测的步骤（工具调用记录）。
3. 当歧义可以从上下文推断时，带透明假设继续执行，不打断流程。
4. 当歧义无法消解时，主动提出候选解读请用户确认，而不是静默执行后返回空结果。
5. 不对模型输出格式提出过高的结构化要求，保持对较小模型的兼容性。

---

## 非目标

- 不在本阶段实现通用开放域指代消解。
- 不构建独立的 NLU 模块；意图解析由 agent 自身基于 schema 完成。
- 不要求第一版覆盖所有歧义类型，优先覆盖当前已暴露的 case。

---

## 核心思路

在现有三阶段工作流（理解 → 查询 → 呈现）中，在阶段 1 和阶段 2 之间插入**解析阶段**：

```
阶段 1：理解（schema / 枚举 / 前序执行记录）
阶段 1.5：解析（图语义 + 路径 + 歧义判定）  ← 新增
阶段 2：查询（validate + execute）
阶段 3：呈现（format）
```

解析阶段通过新工具 `plan_query` 实现。该工具的输出是**自然语言描述**，不是结构化 JSON。

这个选择是刻意的：要求模型精确填写 `traversal_path`、`confidence` 等结构化字段，本身就是一个高难度任务——如果模型能稳定输出正确的图路径，很大程度上已经不需要这个工具了。`plan_query` 的真正价值在于**把推理过程外显**，自然语言同样能达到这个目的，且对较小模型更稳定。

---

## 新增工具：`plan_query`

### 职责

- 将当前自然语言问题翻译为充实的图语义描述
- 结合 schema 明确目标实体类型和图遍历路径
- 若 observations 中已有 `inspect_recent_executions` 的结果，则一并纳入以继承前序约束；否则仅基于 schema 和当前问题推断
- 检测歧义，决定是带假设继续还是请用户确认

### 输入

```json
{
  "question": "string"
}
```

### 输出

两个字段：`description`（自然语言，内部推理）和 `needs_clarification`（布尔，歧义信号）。

**意图明确时（`needs_clarification: false`）：**

```json
{
  "status": "ok",
  "description": "用户问的是天街杭州店内，租户海底捞与项目方签订的租赁合同。需要从项目空间（name=天街杭州店）出发，经由租赁合同，关联到租户（name=海底捞）。天街杭州店这一约束来自上一轮查询结果，上一轮 Cypher 已确认该项目存在。将以此路径构造查询，如理解有误请说明。",
  "needs_clarification": false
}
```

**存在可推断歧义时（`needs_clarification: false`）：**

```json
{
  "status": "ok",
  "description": "'其中'的指向不明确：可能是第1轮的三个购物中心，也可能是第2轮的两个天街项目。根据上下文判断最可能是第2轮的天街杭州店商户列表。将以天街杭州店为约束继续查询，如理解有误请说明。",
  "needs_clarification": false
}
```

**歧义无法消解时（`needs_clarification: true`）：**

```json
{
  "status": "ok",
  "description": "'海底捞的合同'存在两种不同解读：A）以海底捞为承租方（租户）签订的租赁合同；B）管理公司与海底捞签订的其他类型合同。两种路径差异较大，无法从上下文判断。",
  "needs_clarification": true
}
```

`needs_clarification` 是后端强制机制的触发信号。`description` 是内部推理，可包含图路径等技术细节；对用户呈现的澄清问题由 agent 在 `final_answer` 阶段另行改写，不直接复用 `description`。

---

## 歧义处理规则

### 歧义分类

| 类型 | 描述 | 示例 |
|---|---|---|
| **语义歧义** | 自然语言词可映射到多个 schema 实体类型 | "合同"可能对应多种合同实体 |
| **范围歧义** | 指代词（"其中"/"这些"/"这几个"）指向多个可能的前序集合 | 第1轮和第2轮都有可引用结果 |
| **结构歧义** | 两种解读导致完全不同的图路径，且置信度相近 | "海底捞的合同"：海底捞作为承租方 vs 作为合作方 |

### 处理策略

**带假设继续执行**（`needs_clarification: false`）：

- **语义歧义**：schema 通常足以消歧，agent 选最匹配的实体类型，在 `description` 中说明假设
- **范围歧义**：优先选最近一轮（`inspect_recent_executions` 返回的第一条），在 `description` 中说明选择依据

带假设执行时，agent 在 `final_answer` 开头附带一句简短的理解说明，让用户可以自然纠正。

**停下来请用户确认**（`needs_clarification: true`）：

- **结构歧义**：两条图路径均合理，置信度相近，无法从上下文和 schema 中排除任一
- **猜错代价高**：错误结果可能被用户误认为正确（例如查错了合同主体）

后端检测到 `needs_clarification: true` 后，`_clarify_pending` 被设置，`_validate_decision` 阻断后续任何查询工具调用。agent 被迫 `finish`，此时 `final_answer` 应基于 `description` 改写为用户可读的澄清问题（含候选选项），不得包含图路径、实体类型等技术细节。

### 核心判断标准

**猜错只是多一轮对话 → 带假设执行；猜错会产生误导性"正确结果" → 停下来确认。**

---

## Prompt 规则

### 系统 prompt：新增阶段 1.5

在阶段 1 和阶段 2 之间插入：

```
## 阶段 1.5：解析（Parse）
目标：在构造 Cypher 之前，先用自然语言明确查询意图。
- 每个问题在第一次调用 validate_cypher 之前，必须先调用 plan_query。
- plan_query 应基于当前 schema 和当前问题生成意图描述；
  若此前已调用 inspect_recent_executions，其结果已在上下文中可见，可一并纳入；
  inspect_recent_executions 不是 plan_query 的必要前置，两者相互独立。
- plan_query 用充实的自然语言描述：目标实体类型、图遍历路径、过滤约束、以及任何歧义的处理方式。
- 若 plan_query 返回 needs_clarification = true，直接 finish；
  final_answer 基于 description 改写为用户可读的澄清问题（含候选选项），
  不得包含图路径、实体类型等技术细节。
- 若 plan_query 返回 needs_clarification = false，基于 description 构造 Cypher；
  若 description 中存在假设，在 final_answer 开头附带一句简短的理解说明。
```

### 禁止行为补充

```
- 禁止在未调用 plan_query 的情况下直接调用 validate_cypher。
- 禁止在 plan_query 返回 needs_clarification = true 时继续执行查询。
- 禁止静默返回空结果而不说明理解依据。
```

---

## `plan_query` 的后端实现

`plan_query` 不执行任何图查询，是一个**纯直通函数**：

```python
def plan_query(self, question: str, description: str, needs_clarification: bool = False) -> dict[str, Any]:
    return {
        "status": "ok",
        "question": question,
        "description": description,
        "needs_clarification": bool(needs_clarification),
    }
```

`description` 和 `needs_clarification` 均由 agent 作为 `tool_args` 传入，后端原样透传。意图解析的推理发生在 agent 自身的 `_decide_next_action` 过程中，工具调用只是把推理结果外显为可持久化的记录。不引入任何额外的 LLM 调用，也没有外部 IO。

后端在 `_run_tool` 中读取 `needs_clarification`，若为 `true` 则设置 `state["_clarify_pending"]`，`_validate_decision` 随即阻断后续查询工具调用。`_is_clarify_description` 方法已随此机制一并移除。

### `tool_args` schema

```json
{
  "question": "string",
  "description": "string",
  "needs_clarification": "boolean"
}
```

---

## 与现有架构的结合点

### `observations` 注入

在 `_summarize_observation` 中为 `plan_query` 增加专项处理：

```python
if tool_name == "plan_query":
    return {
        "status": "ok",
        "description": str(value.get("description", "")),
        "needs_clarification": bool(value.get("needs_clarification", False)),
    }
```

在 compact 白名单中加入 `"description"` 和 `"needs_clarification"`，确保两个字段对后续步骤完整可见。

### `toolHistory` 持久化

`plan_query` 的调用记录写入 `toolHistory`，与 `execute_cypher` 记录一起构成完整的执行语义链，便于会话恢复后追问时复用。

---

## 目标 case 的工作流

**第三轮："请告诉我其中跟海底捞签订的合同具体信息"**

1. agent 判断问题可能依赖前序执行 → 调用 `inspect_recent_executions`（阶段 1 可选步骤）
2. 读取第2轮 Cypher：约束为天街杭州店、查询商户列表
3. 调用 `plan_query`（阶段 1.5，与步骤 1 独立；此处因步骤 1 已执行，description 可纳入前序约束）；`description` 输出：
   > "用户问的是天街杭州店内，租户海底捞签订的租赁合同。路径：项目空间（name=天街杭州店）→ 租赁合同 → 租户（name=海底捞）。天街杭州店约束来自上一轮查询。将以此路径构造查询，如理解有误请说明。"
4. `description` 不以 `CLARIFY：` 开头 → 继续执行
5. 基于 `description` 构造 Cypher → `validate_cypher` → `execute_cypher`
6. `final_answer` 开头附带："本次查询理解为：天街杭州店内，租户海底捞签订的租赁合同。如理解有误请说明。"

---

## 风险与边界

### 1. `plan_query` 描述质量

`plan_query` 依赖 agent 自身的推理能力。若模型对 schema 路径理解不足，`description` 中的路径可能仍然有误。

缓解：`description` 在 `observations` 中完整可见，后续 Cypher 构造阶段仍可自行校验；若构造失败，仍可通过 `diagnose_error` 修正。这与当前无 `plan_query` 时的兜底路径一致，不会变差。

### 2. `CLARIFY：` 标记被误用

模型可能在不该澄清时也输出 `CLARIFY：`（过于保守），或该澄清时没有输出（过于激进）。

第一版可接受一定误判，后续根据实际表现在 prompt 中补充判断阈值的说明示例。

### 3. 工具预算消耗

`plan_query` 每轮必须调用一次，消耗辅助工具预算。需确认现有预算上限是否需要相应调整。

---

## 验收标准

1. 对于"跨实体类型追问"场景（如从商户列表追问到合同），agent 能在 `plan_query` 中正确描述目标实体和图路径，不需要用户手动解释图语义。
2. 歧义可从上下文推断时，agent 带透明假设执行，`final_answer` 开头包含简短理解说明。
3. 结构歧义无法消解时，agent 设置 `needs_clarification = true`，`final_answer` 为用户可读的澄清问题，不含技术细节，不静默执行、不返回空结果。
4. 多次 validate/execute 重试次数显著减少（目标：当前问题场景下从 5+ 次降至 2 次以内）。
5. `plan_query` 记录写入 `toolHistory`，会话恢复后仍可追溯。
6. 系统主链路中不存在基于特定词汇的硬编码分流逻辑。
