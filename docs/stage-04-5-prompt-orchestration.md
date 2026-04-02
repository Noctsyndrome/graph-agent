# Stage 04-5: Agent Prompt 编排体系（当前实现）

## 背景

Stage 04-4 解决了 agent 循环的若干健壮性问题，但随后为了提升执行纪律、减少无效工具调用、控制 token 消耗，我们又对 prompt 编排体系做了几轮调整。  

这些调整已经体现在当前代码中，尤其是 [agent.py](/C:/Code/kg-qa-poc/src/kgqa/agent.py) 和 [tools.py](/C:/Code/kg-qa-poc/src/kgqa/tools.py)。本文档记录“当前工作区真实实现”的 prompt 体系，作为后续继续调优的基线。

本文档以当前工作区代码为准，包含尚未提交但已存在于本地的 `agent.py` 提示词优化。

---

## 总体目标

当前 prompt 编排体系的目标不是“让 LLM 自由探索”，而是让它在一套受约束的工作流内行动：

1. 先理解图谱结构和枚举值
2. 再构造并校验 Cypher
3. 最后格式化结果并结束

同时保留以下能力：

- 多轮会话承接
- 场景感知（HVAC / Elevator / Property）
- 双预算控制
- 工具错误自修复

---

## 当前架构概览

当前一次问答由三层 prompt / context 共同决定：

### 1. System Prompt

由 [agent.py](/C:/Code/kg-qa-poc/src/kgqa/agent.py) 中 `_build_system_prompt()` 生成，提供：

- 当前数据集名
- 当前 schema 的实体清单
- 当前 schema 的关系清单
- 三阶段工作流规则
- 禁止行为

这是“全局约束层”，决定 agent 能做什么、不能做什么。

### 2. User Prompt

由 [agent.py](/C:/Code/kg-qa-poc/src/kgqa/agent.py) 中 `_build_user_prompt()` 生成，提供当前回合的运行态上下文：

- 当前问题
- 当前阶段判断
- 最近 10 条会话 transcript
- 当前问题中识别出的已知枚举值
- 最近错误
- 最近 observation 摘要
- 当前预算
- 当前工具列表
- 查询阶段的 Cypher 编写提醒
- JSON 输出格式要求

这是“运行态上下文层”，决定 agent 这一轮应该如何行动。

### 3. Tool Specs

由 [tools.py](/C:/Code/kg-qa-poc/src/kgqa/tools.py) 中 `tool_specs()` 生成。  
它不是独立的一次 prompt，但会被注入到 user prompt 中，因此本质上属于 prompt 体系的一部分。

它提供：

- 当前场景的实体名
- 当前场景的关系名
- `Entity.field` 形式的枚举字段示例
- 每个工具的描述与参数 schema

---

## 当前系统提示词的核心约束

当前 system prompt 里最重要的几条规则是：

### 1. 角色约束

- 你是企业知识图谱问答 Agent
- 必须通过工具逐步求解
- 禁止凭空回答
- 只输出 JSON，不要解释

这里的“只输出 JSON”只约束**规划模型**，不约束最终面对用户的自然语言回答。最终回答仍然由 `format_results + compose_answer` 生成。

### 2. 三阶段工作流

当前明确采用三阶段编排：

#### 阶段 1：理解（Understand）

目标：先掌握图谱结构与真实枚举值。

规则：

- schema context 在新会话开始时会自动注入
- 模糊值优先用 `match_value`
- 浏览真实枚举值用 `list_domain_values(kind='Entity.field')`
- 纯统计 / 排序 / Top N 且不涉及模糊值时，可以跳过显式枚举浏览

#### 阶段 2：查询（Query）

目标：生成正确 Cypher，并按“先校验、后执行”的链路推进。

规则：

- 必须先 `validate_cypher`
- 通过后才能 `execute_cypher`
- 失败时优先 `diagnose_error`
- 每次修正后都要重新 `validate_cypher`

#### 阶段 3：呈现（Present）

目标：格式化结果并结束。

规则：

- 有 rows 后调用 `format_results`
- `format_results` 成功后才能 `finish`
- 没有稳定结果时不能直接 `finish`

### 3. Dataset 强约束

当前 system prompt 明确强化了 dataset 规则：

- 所有 MATCH 子句中的每个节点都必须带当前 `dataset`
- 推荐内联写法：`MATCH (n:Entity {dataset: 'xxx'})`
- 缺失 dataset 会被 `validate_cypher` 拒绝

这条约束在当前工作区又被进一步强化了一版：不仅说明“必须带 dataset”，还给了明确的正确/错误写法示例。

---

## 当前 user prompt 的结构

当前 `_build_user_prompt()` 的结构是稳定的，按以下顺序组织：

### 1. 当前问题

始终显式注入用户最新问题。

### 2. 当前阶段

由 `_infer_current_phase()` 计算，属于 prompt 内显式自检：

- 阶段 1：理解
- 阶段 1→2：已有 schema，准备进入查询
- 阶段 2：查询
- 阶段 3：呈现

这个阶段信息非常关键，因为它把“当前应该做什么”从隐含判断变成了显式上下文。

### 3. 最近 10 条 transcript

这是本轮修回来的能力。  
由 `_messages_for_prompt(messages)` 生成，保留最近 10 条消息，格式为：

```text
[user] ...
[assistant] ...
```

作用：

- 恢复多轮追问能力
- 让“这些类型 / 这些项目 / 它们 / 其中谁”这类问题能承接上一轮对话
- 避免每轮都退化成单轮问答

这是当前 prompt 体系里最重要的多轮上下文来源。

### 4. 当前问题中已识别的枚举值

由 `_candidate_domain_matches(question)` 基于当前问题文本和 `DomainRegistry.as_dict()` 扫描得到。

例如问题里直接出现了：

- 品牌
- 类型
- 城市
- 状态

就会提前在 prompt 中写成：

```text
- Entity.field = "实际值"
```

作用：

- 减少 agent 对全量 `list_domain_values` 的依赖
- 让简单问题可以更快进入 validate/execute

注意：这一层目前只基于**当前问题文本**做匹配，不会从上一轮答案反向提取候选值。

### 5. 最近错误

只注入最近 4 条 `status == error` 的 observation。

作用：

- 让模型显式优先处理失败信息
- 减少“刚失败过又重复同一个错误”的概率

### 6. 最近 observation 摘要

只保留最近 6 条 observation，并做字段裁剪。当前允许透传的关键字段主要是：

- `status`
- `error`
- `hint`
- `row_count`
- `columns`
- `rows_preview`
- `note`
- `exact_match`
- `fuzzy_matches`
- `value`

作用：

- 保留必要运行态信息
- 避免把整份工具原始输出塞回 prompt
- 控制 token

### 7. 预算状态

当前会把预算直接写进 prompt：

- 辅助工具剩余次数
- 主工具剩余次数
- 是否已有格式化结果

这意味着 LLM 当前能显式“看到预算”，而不是完全由系统暗中裁剪。

### 8. 工具列表

把当前场景 `tool_specs()` 的 JSON 完整注入 prompt。

这使得：

- 场景实体 / 关系 / `Entity.field` 示例是动态的
- 工具名与参数格式对 LLM 明确可见
- 不同场景能共享同一套 agent 框架

### 9. 查询阶段 Cypher 编写提醒

这是当前工作区最新加的一层提醒，仅在“进入查询阶段”时才注入。

内容重点是：

- 每个 MATCH 子句中的每个节点都必须带 dataset
- 推荐内联写法
- 缺失 dataset 会被 validate 拒绝

这属于“阶段感知提醒”，不是全局恒定附加内容，目的是在真正写 Cypher 的那一刻把约束再强调一遍。

### 10. 输出格式

最终要求 LLM 只输出一个 JSON 对象，字段固定为：

- `thought`
- `action`
- `tool_name`
- `tool_args`
- `final_answer`
- `auto_finish_after_format`

这保证规划层输出可被后端稳定解析。

---

## 当前预算模型

当前不是单一“8 步”模型，而是双预算：

- `AUX_TOOLS`
  - `get_schema_context`
  - `list_domain_values`
  - `match_value`
  - `diagnose_error`
- `MAIN_TOOLS`
  - `validate_cypher`
  - `execute_cypher`
  - `format_results`

默认预算：

- 辅助预算：4
- 主预算：8
- 总回合上限：`4 + 8 + 4 = 16`

意义：

- 允许一定量的理解/诊断工具
- 避免辅助工具过快耗尽主查询链路
- 仍然保留总回合上限，防止无限循环

---

## 当前 pre-step 机制

当前实现里，`get_schema_context` 并不是完全交给 LLM 自主决定。

规则是：

- 如果最近消息里没有 `get_schema_context`
- 则在主循环前自动执行一次 schema pre-step
- 这一步不计入预算

这意味着当前体系实际上采用了“**硬性 pre-step + 后续 prompt 引导**”的混合模式，而不是纯 prompt 引导。

它的优点：

- 新会话第一问更稳
- 避免在没有 schema 的情况下直接 validate / execute

它的代价：

- 对 agent 自主性有一定约束
- 如果上下文判断不准，可能出现不必要的 schema 调用

---

## 当前决策校验层

Prompt 不是唯一约束，后端还有一层 `_validate_decision()`。

它会拦截以下问题：

- 非法 action
- 非法 tool name
- `tool_args` 不是对象
- 缺少必填参数
- 重复的辅助工具调用

被拦截后不会当成工具调用展示，而是：

- 记录为 `llm_decision` 类型的错误 observation
- 发出 `DECISION_ISSUE` 事件
- 仅更新运行状态文本

这使得当前体系不只是“靠 prompt 约束”，而是“prompt 约束 + 决策校验”的双层机制。

---

## 当前工具编排原则

从 prompt 和决策校验两层合起来看，当前系统实际鼓励的是这条路径：

### 简单问题

```text
schema pre-step
→ validate_cypher
→ execute_cypher
→ format_results
→ finish
```

### 含模糊值的问题

```text
schema pre-step
→ match_value 或 list_domain_values(Entity.field)
→ validate_cypher
→ execute_cypher
→ format_results
→ finish
```

### 失败修正问题

```text
schema pre-step
→ validate_cypher / execute_cypher
→ diagnose_error
→ validate_cypher
→ execute_cypher
→ format_results
→ finish
```

---

## 与 04-4 阶段相比的关键变化

### 1. 从“循环健壮性”走向“显式阶段编排”

04-4 更偏机制补洞。  
当前体系已经把“理解 → 查询 → 呈现”正式写进 prompt，形成了明确的阶段式编排。

### 2. transcript 已被重新放回 prompt

这是当前体系恢复多轮对话能力的关键变化。  
没有这层，agent 容易退化成单轮执行器。

### 3. dataset 规则被双重强化

现在不仅靠校验器拦截，还在：

- system prompt
- 查询阶段用户 prompt

两层反复提醒。

### 4. prompt 里显式注入预算

这让 LLM 能“看到”剩余预算，从而减少不必要的辅助工具重复调用。

### 5. 工具不是完全自由调用

当前体系已经明确不再是“LLM 想调什么就调什么”，而是：

- prompt 做流程约束
- `_validate_decision()` 做结构化拦截

---

## 当前体系的已知取舍

### 优点

- 执行纪律明显增强
- `validate_cypher -> execute_cypher -> format_results` 主链路更稳定
- 多场景下的工具描述保持动态化
- dataset 隔离更稳
- 多轮 transcript 已恢复

### 代价

- prompt 结构更重
- pre-step 使得系统不再是纯 LLM 自主决策
- `_candidate_domain_matches()` 仍然只看当前问题文本，多轮追问的指代能力主要依赖 transcript
- dataset 提醒被写得很强，若后续 validator 逻辑变化，需要同步更新 prompt

---

## 当前代码基线

本文档对应的核心实现位置：

- [agent.py](/C:/Code/kg-qa-poc/src/kgqa/agent.py)
- [tools.py](/C:/Code/kg-qa-poc/src/kgqa/tools.py)

其中 `agent.py` 当前工作区包含一版尚未提交的 prompt 强化，主要是：

- 更明确的 dataset 内联写法说明
- 查询阶段专门追加的 Cypher 编写提醒

若后续继续调 prompt，建议先以本文档为基线，再记录变更点，避免文档再次落后于实现。

