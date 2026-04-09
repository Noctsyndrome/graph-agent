# Stage 04-8-1: 追问引用解析（Agent-Native / 低 Prompt 开销）

## 背景

当前系统已经具备：

- 多轮会话持久化
- `messages / state / toolHistory / latestResult` 的会话恢复
- 基于近 10 条有效 Q/A 的 transcript 注入

但这仍不足以支撑真正稳定的追问承接。

在以下实际会话中：

1. 第一轮：`奥的斯电梯用量最多的是哪个客户？`
2. 第二轮：`上面结果中的这5个电梯分别是什么型号`

第二轮出现了典型的“范围扩散”：

- 系统只承接了第一轮自然语言回答中的 `绿城`
- 没有承接第一轮查询真正定义出来的约束范围：
  - `品牌 = 奥的斯`
  - 被计数对象是 `Installation`
  - `TOP 1 customer`
  - `count = 5` 对应的是一个底层实体集合，而不是普通文本数字

最终第二轮退化成“绿城名下有哪些型号”，返回了 23 条型号记录，而不是“上面结果中的那 5 个奥的斯安装实例分别是什么型号”。

---

## 问题本质

问题不在于“是否传了 transcript”，而在于系统目前传递的是：

- 自然语言对话文本

而没有传递：

- 可复用的查询语义
- 可引用的结果作用域
- 当前轮结果背后真正命中的实体集合

这说明现有多轮能力仍然停留在“对话文本延续”，没有进入“查询语义延续”。

对于 KG-QA Agent，后者才是追问稳定性的关键。

---

## 目标

1. 让 agent 能在追问中引用前序查询的“语义范围”和“结果作用域”，而不是只依赖自然语言回答。
2. 不将每轮原始 rows 或长表格持续注入 prompt，避免 prompt 随会话增长而膨胀。
3. 保持 agent-native：不依赖“上面结果 / 这些 / 其中 / 这5个”之类关键词 hardcode 分流。
4. 允许 agent 在需要时按需读取前序上下文，而不是被动接收全量历史。
5. 与当前 SQLite 会话持久化兼容，支持会话恢复后继续追问。

---

## 非目标

- 不在本阶段引入关键词触发的硬编码分支。
- 不在本阶段把每轮完整结果表重新塞回 prompt。
- 不在本阶段做跨会话长期记忆或向量召回。
- 不在本阶段试图实现通用开放域指代消解。
- 不要求第一版支持任意复杂的跨多轮、多集合布尔组合引用。

---

## 设计原则

1. 引用前序结果应作为一等能力，而不是零散 prompt 技巧。
2. 让 agent 自主决定何时需要读取前序上下文。
3. prompt 中只放稳定规则，不放不断增长的结果内容。
4. 后端持久化“可引用语义”，不持久化“供模型直接复述的大段文本”。
5. 前序结果需要同时区分：
   - 展示层结果
   - 可引用的底层作用域

---

## 核心思路

本阶段引入“结构化可引用记忆”，但不把它直接注入 prompt。

每轮成功查询后，后端生成一个紧凑的 `turn artifact`，表示这一轮问答的可引用语义：

- 这轮问题在查询什么
- 这轮结果是如何得到的
- 这轮最终选中了哪个底层实体集合
- 这轮结果后续还能如何继续查询

agent 只在需要时，通过通用工具读取该 artifact 或其对应的 scope。

换句话说：

- prompt 负责告诉 agent“你有能力读取前序语义”
- artifact / scope 负责在后端保存真正可复用的信息
- agent 通过工具按需获取，不走硬编码关键词分流

---

## 为什么不能继续依赖 transcript

当前 transcript 只保留：

- user 文本
- assistant 最终文本回答

它明确跳过了：

- tool call
- tool result
- execute 后真实返回的结构化 rows

因此 transcript 最多只能表达：

- “谁被回答出来了”
- “回答文本里显式说了什么”

却无法稳定表达：

- 查询路径
- 过滤条件
- 聚合对象
- top-k 选择逻辑
- “这 5 个”到底是哪 5 个底层实体

只要后续追问依赖这些结构化约束，单纯 transcript 就不够。

---

## 总体方案

### 一、引入 `turn artifact`

每轮 `execute_cypher` 成功并形成稳定结果后，生成一个紧凑 artifact。

建议包含以下信息：

```json
{
  "turn_id": "turn_xxx",
  "question": "奥的斯电梯用量最多的是哪个客户？",
  "result_kind": "aggregation_topk",
  "query_intent": {
    "root_entity": "Installation",
    "projection": ["customer", "installation_count"],
    "filters": [
      {"entity": "Model", "field": "brand", "op": "=", "value": "奥的斯"}
    ],
    "group_by": ["Customer.name"],
    "order_by": [{"expr": "count(Installation)", "direction": "desc"}],
    "limit": 1
  },
  "selected_bindings": {
    "Customer.name": "绿城"
  },
  "referable_scope": {
    "entity": "Installation",
    "scope_handle": "scope_xxx",
    "cardinality": 5
  },
  "display_result_ref": "result_xxx"
}
```

这里最重要的是：

- `query_intent`
- `selected_bindings`
- `referable_scope`

其中 `referable_scope` 不是文本摘要，而是一个后端可追踪的句柄，代表“这一轮真正命中的那组底层实体”。

### 二、区分“展示结果”和“可引用作用域”

每轮结果必须拆成两个层次：

#### 1. 展示层

即当前 UI 需要展示给用户的结果，例如：

- `绿城`
- `installation_count = 5`

#### 2. 引用层

即后续追问真正应承接的实体集合，例如：

- 属于 `绿城`
- 满足 `奥的斯`
- 被第一轮聚合统计命中的 5 个 `Installation`

第二轮用户说“这5个电梯”，真正对应的是引用层，而不是展示层文本。

---

## Agent-Native 工具设计

### 1. `resolve_reference`

新增通用工具：

```json
{
  "name": "resolve_reference",
  "args": {
    "question": "string"
  }
}
```

职责：

- 基于当前问题和当前线程历史，判断用户是否在引用前序 turn artifact
- 返回最可能的引用对象及其置信信息
- 不要求绑定到某个关键词规则

建议返回：

```json
{
  "status": "ok",
  "resolved": true,
  "reference_type": "prior_scope",
  "target_turn_id": "turn_xxx",
  "scope_handle": "scope_xxx",
  "entity": "Installation",
  "cardinality": 5,
  "summary": "上一轮选择出的绿城名下奥的斯安装实例集合，共 5 个",
  "confidence": 0.91
}
```

如果无法解析：

```json
{
  "status": "ok",
  "resolved": false,
  "candidates": [...]
}
```

### 2. `inspect_scope`

新增通用工具：

```json
{
  "name": "inspect_scope",
  "args": {
    "scope_handle": "string"
  }
}
```

职责：

- 返回这个 scope 的紧凑语义摘要
- 告诉 agent 当前 scope 代表哪类实体、来自哪轮查询、有哪些核心约束

返回内容应保持紧凑，不返回整批 rows。

### 3. `validate_cypher / execute_cypher` 支持 `scope_handle`

建议让主查询工具链支持在已有 scope 上继续查询，而不是每次都从头重建全部约束。

例如：

```json
{
  "cypher": "... RETURN m.name AS model_name",
  "scope_handle": "scope_xxx"
}
```

语义上等价于：

- 先锁定已有底层实体集合
- 再在该集合上做后续投影、关联或聚合

这样 agent 不必重复恢复上一轮全部路径与过滤条件，只需要知道“当前查询以哪个 scope 为输入”。

---

## `resolve_reference` 的实现要求

这里必须避免退化成关键词 hardcode。

因此它不应采用：

- 如果问题中包含“上面结果”就取上一轮
- 如果出现“这些”就取最近结果
- 如果出现数字就猜测是 top-k

这种写法只能作为脆弱启发式，不能作为系统主方案。

更合理的实现是：

1. 输入当前 question
2. 从当前线程最近若干轮 artifact 中取出候选引用对象
3. 由模型或统一的语义匹配器在候选对象中判断：
   - 当前问题是否依赖某个已有结果集合
   - 当前问题中的约束是否要求延续该集合
   - 当前问题究竟要引用“展示结果”还是“底层作用域”
4. 输出一个结构化引用决策

也就是说，系统 hardcode 的不是关键词，而只是：

- “当 agent 需要前序上下文时，可以调用一个通用引用解析工具”

引用是否成立，由工具内部的通用语义解析决定。

---

## Prompt 侧改动

本阶段不应把 artifact 全量注入 prompt。

Prompt 只增加稳定、常量级的规则：

1. 如果当前问题可能依赖前序查询的范围、集合、排序结果或聚合结果，优先调用 `resolve_reference`。
2. 若 `resolve_reference` 返回了 `scope_handle`，后续查询优先在该 scope 上继续推进。
3. 不要仅依据自然语言 transcript 猜测“这些 / 其中 / 上一轮结果”的具体约束。

这样 prompt 的增长成本基本固定，不随历史结果规模增长。

---

## 持久化方案

### 方案 A：继续挂在 `state` 中

在当前会话 `state` 中新增：

- `artifacts`
- `scopes`

优点：

- 改动面小
- 直接复用现有 `sessions.db`
- 会话恢复路径已经存在

缺点：

- 如果 artifact 越积越多，单条 session 的 `state_json` 会膨胀

### 方案 B：新增独立表

在 SQLite 中新增：

- `session_turn_artifacts`
- `session_scopes`

优点：

- 与 `session.state` 解耦
- 更适合后续扩展和按需读取

缺点：

- 落地复杂度更高

### 当前建议

PoC 当前优先采用方案 A，但必须保证 artifact 是紧凑结构，不能存整批 rows。

如果后续发现 `state_json` 明显膨胀，再演进到独立表。

---

## 作用域表示

`scope_handle` 背后不能只是一个名字，它必须能够真正复原底层集合。

建议 scope 至少保存：

- `scope_handle`
- `entity`
- `source_turn_id`
- `dataset_name`
- `derivation`
- `cardinality`

其中 `derivation` 可采用两种形式之一：

### 1. 语义 derivation

保存结构化语义条件，例如：

- 根实体 = `Installation`
- 过滤：`Customer.name = 绿城`
- 过滤：`Model.brand = 奥的斯`

优点：

- 可解释性强
- 更利于后续继续组合

### 2. 物化 derivation

保存实体主键集合或其稳定引用，例如：

- `installation_ids = [...]`

优点：

- 追问时最准确

缺点：

- 集合很大时可能膨胀

### 当前建议

优先采用混合方案：

- 小集合时保存显式 ids
- 大集合时保存结构化 derivation

对当前问题中的 `5 个 Installation`，显式保存 ids 是完全可接受的。

---

## 目标 case 的工作流

### 第一轮

用户：

`奥的斯电梯用量最多的是哪个客户？`

系统执行后生成：

- 展示结果：`绿城, 5`
- artifact：
  - 被计数实体：`Installation`
  - 过滤：`Model.brand = 奥的斯`
  - 分组：`Customer.name`
  - `limit = 1`
  - 选中绑定：`Customer.name = 绿城`
  - 引用作用域：`scope_xxx`
  - `cardinality = 5`

### 第二轮

用户：

`上面结果中的这5个电梯分别是什么型号`

agent 的正确行为应是：

1. 判断当前问题可能依赖前序结果集合
2. 调用 `resolve_reference(question)`
3. 得到 `scope_xxx`
4. 调用 `inspect_scope(scope_xxx)`，确认该 scope 是“绿城名下奥的斯的 5 个 Installation”
5. 在该 scope 上继续查询型号
6. 返回 5 个安装实例对应的型号，而不是重新查询绿城名下全部设备

这条链路里不需要把第一轮 rows 或完整 tool result 注入 prompt。

---

## 为什么这是 Agent-Native

这个方案仍然是 agent-native，原因是：

1. 是否需要前序上下文，由 agent 自己判断。
2. 何时调用 `resolve_reference`，由 agent 决策，不由外层关键词分支接管。
3. 前序引用对象通过工具返回，agent 再决定如何继续调用查询工具。
4. Prompt 中只声明能力和规则，不编码具体词表或固定跳转路径。

换句话说，系统只提供：

- “可引用前序结果”的工具能力

而不是：

- “检测到某些词就强制走某个流程”的硬路由。

---

## 与当前架构的结合点

### 1. 生成 artifact 的时机

建议放在：

- `execute_cypher` 成功之后
- `format_results` 完成之前或之后都可以

原因：

- 此时已知完整 rows
- 此时已知实际 row_count
- 此时最容易判断是否可形成稳定 scope

### 2. artifact 的来源

artifact 不应完全依赖自然语言总结生成。

建议综合使用：

- 当前 question
- validate 后的最终 cypher
- execute 的 rows / row_count
- schema 中的实体关系信息

### 3. 恢复链路

会话恢复时：

- 不需要把全部 artifact 注入 prompt
- 只需要保证 `resolve_reference` / `inspect_scope` 能从持久化 state 中读到它们

---

## 风险与边界

### 1. artifact 提取失败

有些查询未必能稳定抽取出引用层 scope，例如：

- 复杂多重聚合
- 自定义计算列
- 混合标量和对象结果

第一版可接受：

- 只对一部分“稳定可引用”结果生成 scope
- 对无法生成 scope 的 turn 返回 `resolved = false`

### 2. scope 过大

如果底层集合非常大，不应直接保存完整 ids。

因此需要：

- 小集合显式物化
- 大集合保存 derivation

### 3. 多候选歧义

某些问题可能同时可指向多轮结果或多个集合。

第一版工具应允许返回：

- `resolved = false`
- 多候选摘要

再由 agent 继续澄清，而不是强行猜中一个。

---

## 实施建议

### Phase 1

先覆盖最有价值的可引用类型：

- 单轮 top-k / top-1 聚合结果
- 上一轮选中的对象集合
- 上一轮小集合实例结果

这已经足以覆盖当前暴露出的核心问题。

### Phase 2

再扩展到：

- 多轮派生 scope
- 跨两轮以上的引用链
- 排名切片、分页切片、前 N / 后 N 子集

---

## 验收标准

1. 对于“上一轮是聚合 / 排序 / top-k，下一轮引用其底层集合”的场景，不再发生明显范围扩散。
2. Prompt 大小不随执行结果行数线性膨胀。
3. 旧会话恢复后，agent 仍可按需引用此前 artifact / scope。
4. 系统主链路中不存在基于特定中文关键词的硬编码分流逻辑。
5. 当引用无法稳定解析时，agent 返回澄清或保守失败，而不是 silently 扩大查询范围。

---

## 结论

本阶段不应继续尝试“把更多历史结果塞进 prompt”来修补追问能力，也不应使用关键词 hardcode 分流。

正确方向是：

- 为每轮稳定结果生成可引用的 `turn artifact`
- 将底层实体集合抽象成 `scope_handle`
- 让 agent 通过通用工具按需解析和读取前序作用域

这样既能控制 prompt 成本，又能让系统真正从“对话文本延续”升级为“查询语义延续”。
