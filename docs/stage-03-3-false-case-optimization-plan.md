# False 用例优化方案

更新时间：2026-03-31

## 1. 摘要

当前 `report.html` 里的 false 项不是同一类问题，分成 3 组：

- 真实查询失败，且根因清晰
  - `S4-2` / `C12` / `C16`：把“万科商业项目”错误绑定成精确项目名或错误枚举值，未分解成 `Customer=万科 + Project.type=商业`
  - `C5`：把口语类目“冷水机”错误落成 `Category.name='冷水机'`，而图谱真实枚举是 `冷水机组`
  - `C7`：把“深圳项目”错误绑定成 `Project.name='深圳项目'`，而不是 `Project.city='深圳'`
  - `G15`：把“最近开工”错误绑定成 `Project.status='开工'`，而图谱真实状态枚举是 `建设中/规划/运营中`，问题本质应走 `start_date` 排序
- 查询结果其实成立，但评估规则误判
  - `C16`：当前答案/预览语义正确，但评估只按字面找 `型号`，而结果里可能是 `设备名称`
  - `C12`：历史报告里属于“回答中品牌信息不稳定/字段名不一致”导致的误判；当前运行又表现出查询不稳定，说明它同时有链路问题和评估问题
- 报告已过时，和当前代码状态不一致
  - `C5`、`C19` 当前已可复现成功，说明 `report.html` 至少部分反映的是旧状态，而不是当前基线

这轮优化应同时覆盖两条线：

- 查询链路：提升 LLM 对图谱真实枚举值、组合过滤条件、时间语义的 grounding 能力
- 评估链路：把“语义正确但字段名不同”的情况从 false 中剥离出来

关键约束：

- 不走“在 system prompt 里补硬规则”这条路
- 优先采用 `schema/domain grounding + 结构化验证 + 泛化 few-shot + 评估语义化` 的方案

## 2. 关键改动

### 2.1 新增 Grounding 层，先把问题对齐到图谱真实枚举与字段语义

新增一个独立的 grounding 子系统，位置建议挂在 `service -> router/query` 之前，输出结构化 grounding 结果，而不是直接写 prompt 规则。

Grounding 输入：

- 原始问题
- `DomainRegistry` 当前加载的真实枚举：
  - `customers`
  - `brands`
  - `cities`
  - `project_types`
  - `categories`
  - `refrigerants`
  - 新增 `project_statuses`
  - 新增 `project_names`
- `schema.yaml` 中的字段别名和新增语义配置

Grounding 输出建议新增类型：

- `GroundingCandidate`
  - `field`
  - `canonical_value`
  - `matched_text`
  - `score`
  - `source`（`domain` / `schema_alias` / `derived`）
- `GroundingResult`
  - `field_candidates: dict[str, list[GroundingCandidate]]`
  - `semantic_intents: list[str]`
  - `composite_entities: list[str]`

Grounding 规则不写死具体业务词，而是采用通用匹配策略：

- 枚举值 fuzzy match：精确匹配、子串匹配、去后缀匹配、字符相似度
- 通用后缀剥离：如 `项目/类项目/设备/品牌/型号`
- 字段语义映射来自 schema 配置，而不是 prompt 文案
- 允许一个问题命中多个字段候选，而不是过早决定唯一解释

这样可泛化解决：

- `冷水机 -> 冷水机组`
- `商业项目 -> 商业`
- `深圳项目 -> city=深圳`
- `万科商业项目 -> customer=万科 + type=商业`

### 2.2 扩展 schema 配置，显式表达“字段语义”而不是把语义塞进提示词

在 `schema.yaml` 中新增可配置语义层，建议新增：

- `field_semantics`
  - `Project.type`
    - 同义词提示：`商业项目/住宅项目/产业园项目`
  - `Project.city`
    - 同义词提示：`区域/地区/城市`
  - `Project.start_date`
    - 查询意图提示：`最近开工/最早开工/2023年后/2024年后`
  - `Project.status`
    - 枚举来源说明，不把 `开工` 当成真实状态值
  - `Category.name`
    - 类目口语别名来源
- `value_aliases`
  - 从真实 domain 值派生，不手写具体业务 case
  - 允许配置通用 alias 生成策略，而不是直接写 `冷水机 -> 冷水机组` 这种 one-off 映射

实现上：

- `SchemaRegistry` 负责加载这些语义配置
- `GroundingResolver` 使用这些配置构造候选
- Intent/Planner/Cypher prompt 只消费 grounding 结果，不再自行猜字段值

### 2.3 改造 LLM 输入：从“裸问题”改成“问题 + grounding 候选 + schema 约束”

不在 system prompt 中增加硬编码业务规则，而是把 grounding 结果作为结构化上下文传给 LLM。

Intent 阶段输入新增：

- 当前问题
- grounding 命中的字段候选
- 当前字段允许的 canonical values 摘要

NL2Cypher 阶段输入新增：

- 已确定的 intent
- grounding 结果中每个字段的 top candidates
- schema 中该字段的真实枚举值或来源说明

要求 LLM 输出时遵守：

- 若使用枚举字段值，优先从 grounding 候选里选 canonical value
- 若 grounding 已明确命中 `city/type/category`，不要再把该短语绑定成 `Project.name`
- 若问题是时间排序类，优先考虑 `date/range/order` 字段而不是状态精确匹配

这样解决的不是单个 case，而是一个泛化模式：

- 口语实体 -> canonical enum
- 组合实体 -> 多字段拆解
- 事件语义 -> 正确字段/算子

### 2.4 加入查询后结构化诊断，不再只靠“空结果就重试”

保留两次尝试，但第二次不是盲重试，而是基于诊断结果重生。

新增 `CypherDiagnosis`：

- `empty_rows`
- `invalid_enum_literal`
- `overspecified_name_binding`
- `missing_grouping_dimension`
- `temporal_phrase_bound_to_status`
- `unknown_category_literal`

诊断逻辑：

- 对枚举字段字面值做 domain 校验
  - 例如 `Project.type='商业项目'`、`Category.name='冷水机'`、`Project.status='开工'`
- 对 `Project.name='深圳项目'`、`Project.name='万科商业项目'` 这类值做“是否真实存在”验证
- 对“按项目看”类问题检查返回列是否真的包含 `项目`
- 对“最近开工”类问题检查是否用了 `start_date ORDER BY`

第二次生成时不加硬编码业务规则，只传递诊断事实：

- 上一次使用了不存在的枚举值
- 上一次把问题短语绑定为不存在的 `Project.name`
- 上一次缺少问题要求的 grouping 维度

### 2.5 few-shot 选择改成“失败模式驱动”，补充泛化样例而不是加 case 规则

保留 few-shot，但补的是模式，不是 case 对 case。

需要新增的 few-shot archetype：

- 口语类目映射
  - `冷水机品牌` -> `Category.name='冷水机组'`
- 区域/城市映射
  - `深圳项目` -> `Project.city='深圳'`
- 组合过滤拆解
  - `万科商业项目` -> `Customer.name='万科'` + `Project.type='商业'`
- 时间排序
  - `最近开工的项目` -> `ORDER BY p.start_date DESC`
- 按项目聚合展示
  - `各项目都在用哪些品牌` -> `RETURN 项目, collect(...)`

few-shot 选择逻辑改为同时看：

- grounding 命中的字段集合
- 问题操作类型：`group/list/min/max/compare/time-rank`
- 领域 canonical values 命中情况

不再只按问题字面关键词 overlap 排序。

### 2.6 评估逻辑升级，区分“查询失败”和“评估误判”

当前 `run_eval.py` 的 `all(keyword in answer + str(result_preview))` 太脆，必须升级。

评估逻辑改为两层：

- 执行层
  - `query_success`
  - `cypher_valid`
  - `row_count`
- 语义层
  - 期望字段是否在 `result_preview` 的 canonical key 或 alias key 中出现
  - 期望实体值是否出现在答案或 preview values 中
  - 对 `型号/品牌/项目/城市` 这类 canonical 词，允许通过 `column_aliases` 进行别名匹配

新增报告字段：

- `failure_class`
- `query_failure_reason`
- `evaluation_failure_reason`
- `cypher_attempt_1`
- `cypher_attempt_2`
- `report_generated_at`
- `report_commit_sha`
- `report_model`

这样可以把：

- `C16` 这种“语义正确但字段名不是‘型号’”从 false 中剥离
- `C5/C7/G15` 这种真实查询失败保留为链路问题
- `C5/C19` 这种报告过时问题显式暴露出来

### 2.7 可观测性补强，给 false 项留下足够证据

当前 UI 能看进度，但报告和 trace 对失败原因仍不够细。

建议扩展 trace：

- `trace.grounding`
  - top candidates
  - chosen candidates
- `trace.cypher.attempts[]`
  - `text`
  - `row_count`
  - `diagnosis`
- `trace.evaluation`
  - `matched_expected_tokens`
  - `missing_expected_tokens`
  - `alias_matches`

这样 false case 的根因可以直接在 UI 和报告里看到，不需要每次再打 monkeypatch 抓一次。

## 3. 测试计划

- 用当前报告中的 false 项作为第一批回归：
  - `S4-2`
  - `C5`
  - `C7`
  - `C12`
  - `C16`
  - `C19`
  - `G15`
- 对每条用例分别验证：
  - grounding 是否命中正确 canonical candidates
  - 生成的 Cypher 是否使用真实枚举值
  - 查询是否非空
  - 答案是否忠于结果
  - 评估是否不再因字段别名误判
- 新增单元测试：
  - `冷水机 -> 冷水机组`
  - `商业项目 -> 商业`
  - `深圳项目 -> city=深圳`
  - `万科商业项目 -> customer=万科 + type=商业`
  - `最近开工 -> start_date DESC`
  - `型号` 期望可由 `设备名称` 满足
- 新增轻量 e2e：
  - 只跑 false 集合与 3 条稳定 pass 集合作 sanity check
  - 报告中必须区分 `query_failure` 与 `evaluation_failure`
- 验收标准：
  - 当前 false 集中，真实链路失败项应显著下降
  - `C16` 这类评估误判应转为 pass
  - 不通过向 system prompt 添加 case-specific 硬规则来达成通过率

## 4. 假设与默认值

- 本轮允许扩展 `schema.yaml`、trace 模型和评估报告结构。
- 本轮不改动数据集内容，默认以当前 Neo4j 数据为准。
- 本轮优先解决“泛化 grounding + 评估误判”，不引入 embedding 服务或外部检索基础设施。
- `report.html` 将在实现完成后重新生成，因为其中至少 `C5`、`C19` 已与当前代码状态不一致。
- “不采用硬规则”解释为：不在 system prompt 中加入 case-specific 规则，不新增按问题字符串分支的业务模板；允许使用 schema/domain 驱动的结构化 grounding、语义配置和通用校验。
