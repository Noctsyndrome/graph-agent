# Stage 04-4: Agent 循环健壮性增强

## 背景

Phase 1（stage-04-3）完成了多场景泛化的数据层改造，DomainRegistry 已从硬编码切换为 schema 驱动。在推进 Phase 2（异构图谱）之前，对 agent 主循环做了一次全面的机制审查，发现了一系列影响可靠性和可扩展性的结构性问题。

本文档记录审查结论，并按优先级规划改进方案。这些改进独立于 Phase 2 的泛化工作，但其中部分（如 tool_specs 场景感知、intent 硬编码）与 Phase 2 有交叉，可协同推进。

---

## 问题清单

### P0-1: 无 dataset 过滤注入

**位置**: `tools.py:85-93` execute_cypher / `query.py:128-147` CypherSafetyValidator

**现状**: Neo4j 中多场景数据通过 `n.dataset` 属性隔离，但 agent 完全依赖 LLM 自行在 Cypher 中加入 `{dataset: 'elevator_poc'}` 等过滤条件。`validate_cypher` 和 `execute_cypher` 均不检查也不注入 dataset 过滤。

**风险**: 如果 LLM 忘记添加 dataset 过滤，查询会跨场景返回数据，产生无声错误——结果看似合理但混入了其他场景的数据，用户无法察觉。

**改进方案**:

在 `execute_cypher` 或 `validate_cypher` 中增加 dataset 感知逻辑，有两种策略：

| 策略 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A: 自动注入 | 在执行前通过 AST 或正则，为每个 MATCH 子句中的节点变量追加 `{dataset: $ds}` 约束 | 对 LLM 透明，不浪费 prompt token | Cypher 改写复杂，边界情况多 |
| B: 校验拦截 | 在 `validate_cypher` 中检查 Cypher 是否包含 dataset 过滤，缺失时返回 `{valid: false, error: "缺少 dataset 过滤"}` | 实现简单，错误信息可引导 LLM 自行修正 | 依赖重试机制（P0-3），多消耗一步 |
| C: Prompt 强化 | 在 system prompt 和 tool_specs 中明确要求"所有 MATCH 必须带 dataset 过滤" | 零代码改动 | 不可靠，LLM 仍可能遗忘 |

**建议**: 采用 B（校验拦截）+ C（Prompt 强化）组合。B 提供硬性兜底，C 降低触发 B 的频率。A 过于复杂且风险高。

**改动范围**: `query.py` CypherSafetyValidator — 新增 dataset 过滤检查方法

---

### P0-2: Agent 循环 5 步上限过紧

**位置**: `agent.py:113` `for step_index in range(1, 6)`

**现状**: 一次"正常"问答的最短工具链是 5 步：

```
get_schema_context → list_domain_values → validate_cypher → execute_cypher → format_results
```

这意味着零容错空间：
- LLM 生成了错误 Cypher → 没有步数修正
- 多步推理问题（需要两轮查询）→ 必然不够
- schema/domain 探索消耗步数后 → 留给执行的不足

**改进方案**:

| 策略 | 做法 | 权衡 |
|---|---|---|
| A: 增大上限 | 改为 8-10 步 | 简单直接，但对 LLM token 消耗和延迟有线性影响 |
| B: 动态上限 | 根据问题复杂度（关键词或 LLM 判断）设定 5-10 步 | 灵活但增加逻辑复杂度 |
| C: 按阶段计步 | schema/domain 探索不计入主循环步数（作为 pre-step），主循环 5 步用于 validate→execute→format 及重试 | 结构清晰，但需要重构循环 |

**建议**: 先采用 A 将上限调到 8 步，同时考虑 C 的设计——将 schema 和 domain 加载作为 pre-step 在循环外执行（尤其是在 tool_specs 场景感知改进后，LLM 可能不再需要手动调这两个工具）。

**改动范围**: `agent.py` — 调整循环上限，或重构为 pre-step + main-loop

---

### P0-3: 无显式失败重试机制

**位置**: `agent.py:130-145` _run_tool 调用与 observation 记录

**现状**: 工具执行结果不区分成功/失败。`execute_cypher` 抛出 Neo4j 错误时，异常被 `_run_tool` 正常捕获并返回为 `{error: "..."}` 格式的结果（或者直接中断循环如果异常未被 try-except 包裹）。错误信息作为普通 observation 传给下一轮 LLM，LLM 需要"自行领悟"应该修正 Cypher。

**问题**:
1. LLM 可能不注意到上一步失败，继续走 finish 或调其他工具
2. 错误信息被 `_summarize_observation` 截断到 600 字符，可能丢失关键诊断信息
3. 没有结构化的错误上下文（如"属性 cooling_kw 不存在于 Model 实体"），LLM 只看到原始 Neo4j 报错

**改进方案**:

在 `_run_tool` 返回后、记录 observation 前，增加失败检测与上下文增强：

```
_run_tool() 返回 tool_result
    │
    ├── 成功（execute_cypher 返回 rows / validate 返回 valid:true）
    │   → 正常记录 observation
    │
    └── 失败（execute_cypher 抛异常 / validate 返回 valid:false）
        → 构造增强错误 observation:
          {
            "tool_name": "execute_cypher",
            "status": "FAILED",
            "error": "原始错误信息（不截断）",
            "hint": "Model 实体的可用属性为: name, brand, load_kg, speed_ms, ...",
            "suggested_action": "修正 Cypher 中的属性名后重新 validate_cypher"
          }
        → 不计入主步数（或计半步）
```

**建议**:
- 在 observation 中增加 `status` 字段（`"ok"` / `"error"`）
- 失败时不截断错误信息
- 从 schema 中提取相关实体的属性列表作为 hint
- 在 `_decide_next_action` 的 prompt 中显式标注哪些 observation 是失败的

**改动范围**: `agent.py` _run_tool + observation 构建, `tools.py` execute_cypher 错误包装

---

### P1-1: validate_cypher 不校验 schema 语义

**位置**: `query.py:128-147` CypherSafetyValidator

**现状**: 校验仅覆盖安全维度（只读、单语句、禁止写关键词），不验证 Cypher 中引用的实体名、属性名、关系名是否在 schema 中实际存在。

**后果**: LLM 可以生成 `MATCH (m:Model) WHERE m.cooling_kw > 100`（HVAC 属性）在电梯场景中通过校验，execute 返回空结果，agent 报告"未找到相关信息"——但真实原因是属性名错误。

**改进方案**:

从 Cypher 文本中提取节点标签和属性引用，与 schema 做交叉校验：

```python
def validate_schema_compliance(self, cypher: str, schema: dict) -> dict:
    # 提取 Cypher 中出现的节点标签 (n:Model), (c:Category) 等
    labels_used = re.findall(r'\((?:\w+)?:(\w+)', cypher)
    # 提取属性引用 n.cooling_kw, m.brand 等
    props_used = re.findall(r'(\w+)\.(\w+)', cypher)

    schema_entities = {e["name"] for e in schema["entities"]}
    for label in labels_used:
        if label not in schema_entities:
            return {"valid": False, "error": f"实体 {label} 不存在于 schema 中"}

    entity_props = {e["name"]: set(e["properties"].keys()) for e in schema["entities"]}
    for var_alias, prop in props_used:
        # 需要变量-标签映射来做精确校验
        ...
```

**复杂度**: 中等。精确校验需要从 Cypher 中建立 变量→标签 的映射关系，简单正则不够可靠。可以先做标签级校验（确认实体名存在），属性级校验作为后续增强。

**改动范围**: `query.py` CypherSafetyValidator 新增方法, `tools.py` validate_cypher 调用新校验

---

### P1-2: observation 截断丢失关键数据

**位置**: `agent.py:375-379` _summarize_observation

**现状**: 超过 600 字符的工具结果被硬截断为前 600 字符 + `"..."`。一个返回 10 行以上的查询结果很容易超限。

**后果**:
- LLM 在后续决策中看不到完整结果，可能对已有数据重复查询
- `format_results` 的 rows 参数不受此影响（直接从 tool_result 获取），但 LLM 的"知情程度"受损
- 错误信息也可能被截断，丢失 Neo4j 报错中的关键字段

**改进方案**:

| 策略 | 做法 |
|---|---|
| A: 分级截断 | 成功结果截断到 1200 字符，错误结果不截断 |
| B: 结构化摘要 | 成功时只保留 `{row_count, column_names, first_3_rows}`，错误时保留完整 error |
| C: 引用机制 | observation 中只存摘要，但保留对完整结果的引用 key，LLM 需要时可通过工具回查 |

**建议**: 采用 B。对 `execute_cypher` 的结果做结构化摘要比粗暴截断更有效，既控制 token 又保留关键信息。

**改动范围**: `agent.py` _summarize_observation

---

### P1-3: tool_specs 不感知当前场景

**位置**: `tools.py:29-57` tool_specs (staticmethod)

**现状**: 工具描述是固定文本，不包含当前场景的实体名、属性名、关系名。LLM 看到的是泛化描述如"读取图谱中各实体 filterable_fields 的真实枚举值"，不知道当前场景有 Customer/Project/Model/Category/Installation 五个实体。

**后果**: LLM 几乎每次都需要先调 `get_schema_context` 来了解当前图谱结构，消耗一个宝贵步数。

**改进方案**:

将 `tool_specs()` 从 staticmethod 改为实例方法，注入场景上下文：

```python
def tool_specs(self) -> list[dict]:
    entity_names = [e["name"] for e in self.schema.schema["entities"]]
    relationship_names = [r["name"] for r in self.schema.schema["relationships"]]
    return [
        {
            "name": "get_schema_context",
            "description": f"读取当前图谱的详细 schema。当前实体: {', '.join(entity_names)}; "
                           f"关系: {', '.join(relationship_names)}。",
            ...
        },
        {
            "name": "list_domain_values",
            "description": f"读取图谱中各实体字段的真实枚举值。当前实体: {', '.join(entity_names)}。"
                           f"kind 参数格式: Entity.field（如 Model.brand）。",
            ...
        },
        ...
    ]
```

**进一步**: 如果实体列表和基础关系已经内嵌在 tool_specs 中，LLM 对简单问题可能跳过 `get_schema_context`，直接生成 Cypher——这在步数紧张时非常有价值。

**改动范围**: `tools.py` tool_specs 改为实例方法, `agent.py` 调用处适配

**与 Phase 2 的交叉**: Phase 2 要求 tool_specs 描述泛化，这里的改动方向一致。

---

### P1-4: ResultSerializer 格式选择机械且与 intent 逻辑矛盾

**位置**: `serializer.py:9-26`, `tools.py:96-106`, `tools.py:124-130`

**现状**: 结果格式化由三层独立判断叠加构成，每一层都有缺陷：

**第一层 — intent 分类** (`tools.py:133-141`):

关键词匹配决定 IntentType（见 P2-1），输入给第二层。

**第二层 — format 选择** (`serializer.py:9-26`):

```python
# 判断1: 单行 + 字段>2 + 问题不含比较词 → key_value
if len(rows) == 1 and len(rows[0]) > 2 and not any(kw in question for kw in ["区别","对比","占比","最多","最大","平均"]):

# 判断2: 问题含比较词 OR intent∈{AGGREGATION, MULTI_STEP} → markdown_table
if any(kw in question for kw in ["区别","对比","占比","最多","最大","平均"]) or intent in {AGGREGATION, MULTI_STEP}:

# 判断3: 行中有 list 类型值 → numbered_list
if any(isinstance(value, list) for row in rows for value in row.values()):

# 兜底: table
```

**第三层 — renderer 推断** (`tools.py:124-130`):

```python
if serialized.format in {"key_value"} and serialized.preview: return "metric_cards"
if serialized.preview: return "table"
return "raw_json"
```

**具体缺陷**:

**(a) 两套关键词重复检查且不一致**

`_infer_intent` 用 `["平均","占比","最多","最大","最少","总","排名","比较","对比"]` 判断 AGGREGATION；`serialize` 又独立用 `["区别","对比","占比","最多","最大","平均"]` 决定 format。两套有交集但不一致——"排名"触发 AGGREGATION intent 但不在 serialize 关键词中，"区别"在 serialize 中但不在 intent 中。同一个问题可能被 intent 判为 SINGLE_DOMAIN 但被 serialize 关键词匹配到 markdown_table，或者反过来。

**(b) 单行判断优先级高于聚合判断，导致错误格式**

问"哪个城市的项目电梯安装总量最大？"，Cypher 用 `ORDER BY ... LIMIT 1` 返回一行多列。`len(rows)==1 and len(rows[0])>2` 为真，直接走 key_value（指标卡片），不再检查聚合关键词——但这是排名结果，应该用 markdown_table。if-elif 的优先级决定了格式，而不是数据语义。

**(c) list 类型检测依赖 Cypher 实现方式**

`numbered_list` 的触发条件是行中存在 Python list 值（Cypher 中用了 `collect()`）。同样的数据，`RETURN collect(m.name) AS models` 会触发 numbered_list，而 `RETURN m.name AS model` 多行返回则走 table。格式选择不应依赖 Cypher 写法。

**(d) 第三层 renderer 抹平了 format 区分**

5 种 format（empty / key_value / markdown_table / numbered_list / table）映射到 3 种 renderer：

```
key_value      → metric_cards
markdown_table → table
numbered_list  → table     ← 与 markdown_table 相同
table          → table     ← 与 markdown_table 相同
empty          → raw_json
```

`numbered_list` 和 `markdown_table` 最终用同一个 renderer，那 serializer 区分它们的意义仅在 markdown 文本格式上——但前端展示组件是同一个。

**(e) 三层判断互相看不到完整上下文**

| 层 | 输入 | 不知道什么 |
|---|---|---|
| _infer_intent | 问题文本 | 查询了什么实体、返回了什么列 |
| serialize | rows + 问题 + intent | Cypher 结构（聚合函数、JOIN 关系） |
| _infer_renderer | format 字符串 | 一切原始信息 |

没有任何一层看到列名语义（`count`/`avg`/`name` 的区别）、Cypher 查询结构、实体关系上下文。

**改进方案**:

| 策略 | 做法 | 优缺点 |
|---|---|---|
| A: 数据结构驱动 | 移除 intent 依赖和问题关键词匹配，纯粹根据 rows 结构决定格式：行数、列数、列名模式（含 count/avg/sum → 聚合表格）、值类型 | 可预测，无硬编码关键词；但对边界情况仍可能误判 |
| B: 列名语义分析 | 在 A 基础上，分析列名是否含聚合语义（count、avg、sum、max、min、total），是否含实体名（对应 key_value），是否含 list 类型 | 比 A 更精确，且列名通常由 LLM 在 Cypher 的 AS 子句中指定，语义明确 |
| C: LLM 辅助 | 将 rows 的前几行 + 问题 + 列名传给 LLM，让 LLM 选择展示格式 | 最准确但多一次 LLM 调用，增加延迟 |

**建议**: 采用 B。具体改造：
1. 移除 `serialize` 中的问题关键词匹配
2. 移除对 `intent` 参数的依赖（或将 intent 降级为 hint，不作为硬判断条件）
3. 格式决策改为：行数 + 列数 + 列名模式（聚合函数名 / 实体属性名）+ 值类型
4. 统一 renderer 映射，消除 numbered_list 和 markdown_table 的无效区分

**改动范围**: `serializer.py` 重写 serialize 逻辑, `tools.py` format_results 和 _infer_renderer 简化

---

### P2-1: _infer_intent 硬编码中文关键词

**位置**: `tools.py:133-141`

**现状**:

```python
if any(keyword in text for keyword in ["平均", "占比", "最多", "最大", "最少", "总", "排名", "比较", "对比"]):
    return IntentType.AGGREGATION
if any(keyword in text for keyword in ["客户", "项目", "品牌", "城市", "区域"]):
    return IntentType.CROSS_DOMAIN
```

**问题**:
1. 关键词是 HVAC/Elevator 的实体名，物业经营场景完全不适用
2. intent 推断影响 `ResultSerializer` 的格式选择和 `AnswerGenerator` 的 prompt，错误分类导致格式不当
3. 与 agent 的 LLM 推理逻辑脱节——agent 自己不用 intent，这是一个旁路判断

**与 P1-4 的关系**: P1-4 分析了 serializer 的完整三层判断链条，_infer_intent 是其中第一层输入。如果 P1-4 的改进落地（serializer 不再依赖 intent），_infer_intent 的唯一下游消费者就只剩 `AnswerGenerator`。届时可以考虑：

| 策略 | 做法 |
|---|---|
| A: 从 schema 生成关键词 | 用 entity description 替代硬编码的实体关键词，AGGREGATION 关键词保留（通用） |
| B: 由 LLM 判断 | 在 compose_answer 时让 LLM 自行判断问答意图 |
| C: 彻底移除 intent | 不再传 intent 给 AnswerGenerator，让 LLM 只根据问题和结构化数据生成回答 |

**建议**: 如果 P1-4 先落地，C 是最干净的选择——AnswerGenerator 的 prompt 中 intent 字段本就不影响 LLM 的回答质量（LLM 看到问题和数据就能判断如何回答）。如果 P1-4 未落地，先用 A 过渡。

**改动范围**: `tools.py` _infer_intent, `generator.py`, 可能影响 `models.py` IntentType

**与 Phase 2 的交叉**: 这是 stage-04-3 中列出的 Phase 2 必改项之一。

---

### P2-2: LLM 决策无自校验

**位置**: `agent.py:209-266` _decide_next_action

**现状**: 单次 LLM 调用直接确定行动。不验证返回的 `tool_name` 是否在可用工具列表中，不验证 `tool_args` 是否符合工具的参数 schema。`agent.py:123-128` 对无效 tool_name 的兜底是直接放弃：

```python
if not tool_name:
    final_answer = "当前无法从图谱中推导出稳定结论。"
    break
```

**改进方案**:

- 验证 `tool_name` 是否在 `["get_schema_context", "list_domain_values", "validate_cypher", "execute_cypher", "format_results"]` 中
- 验证 `tool_args` 的必填参数是否存在
- 无效时不放弃，而是将校验错误反馈给 LLM 重新决策（消耗一步）

**改动范围**: `agent.py` _decide_next_action 后增加校验逻辑

---

### P2-3: schema focus 推断可能过度过滤

**位置**: `schema.py:117-126` _infer_focus

**现状**: 根据关键词匹配决定返回哪些实体的 schema。如果只命中部分实体，其余实体的 schema 被完全省略。

**例**: 问"安装数量超过 5 台的项目有哪些？"可能只匹配到 Installation（"安装"、"数量"），但 LLM 还需要 Project 的 schema 才能写出 `(p:Project)-[:HAS_INSTALLATION]->(i:Installation)` 的 JOIN 查询。

**改进方案**:

匹配到实体后，自动扩展其直接关联实体（通过 schema relationships 的 from/to）：

```python
def _infer_focus(self, question, entities):
    direct = # 关键词直接匹配的实体集合
    expanded = set(direct)
    for rel in self._schema["relationships"]:
        if rel["from"] in direct:
            expanded.add(rel["to"])
        if rel["to"] in direct:
            expanded.add(rel["from"])
    return expanded
```

**改动范围**: `schema.py` _infer_focus

---

## 问题间的依赖关系

```
P0-1 dataset 注入 ──────┐
                         │ 三者构成完整的错误防护链:
P0-2 步数上限 ───────────┤ 注入防无声错误 → 步数给空间 → 重试让恢复可靠
                         │
P0-3 重试机制 ──────────┘
         │
         │ 重试机制的质量依赖于:
         ├── P1-1 schema 语义校验（提供更精准的错误诊断）
         ├── P1-2 observation 不截断（错误信息完整传递）
         └── P1-3 tool_specs 场景感知（减少探索步数，为重试腾空间）

P1-4 serializer 格式选择 ──► P2-1 intent 泛化/移除
  │                            │
  │ P1-4 落地后 serializer     │ intent 唯一下游变为 AnswerGenerator,
  │ 不再依赖 intent            │ 可考虑彻底移除
  │                            │
  └────────────────────────────┘

P2-1 intent 泛化 ────────┐
                          │ Phase 2 泛化改造的前置/协同项
P1-3 tool_specs 场景感知 ─┘
```

---

## 实施建议

### 第一批: P0 三件套（建议在 Phase 2 之前完成）

| 项 | 改动文件 | 预估行数 |
|---|---|---|
| dataset 校验拦截 | `query.py` | ~20 行 |
| dataset prompt 强化 | `tools.py` tool_specs 描述 | ~5 行 |
| 循环上限调整 | `agent.py` | ~3 行 |
| 失败检测 + 增强 observation | `agent.py`, `tools.py` | ~40 行 |

### 第二批: P1 改进（可与 Phase 2 并行）

| 项 | 改动文件 | 预估行数 |
|---|---|---|
| schema 语义校验（标签级） | `query.py`, `tools.py` | ~30 行 |
| observation 结构化摘要 | `agent.py` | ~20 行 |
| tool_specs 场景感知 | `tools.py`, `agent.py` | ~30 行 |
| serializer 格式选择重构 | `serializer.py`, `tools.py` | ~60 行 |

### 第三批: P2 改进（随 Phase 2 一起落地）

| 项 | 改动文件 | 预估行数 |
|---|---|---|
| intent 泛化或移除 | `tools.py`, `generator.py`, `models.py` | ~30 行 |
| LLM 决策校验 | `agent.py` | ~20 行 |
| focus 推断扩展关联实体 | `schema.py` | ~15 行 |
