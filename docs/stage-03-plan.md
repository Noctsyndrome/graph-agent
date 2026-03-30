# KG-QA PoC 阶段三实施计划：挤出规则水分，验证 LLM 泛化能力

更新时间：2026-03-30

## 0. 背景与动机

阶段一、二已搭建完整的问答链路，20 条 baseline 用例通过率 100%。但深入分析发现：

- **query.py 中 56% 的代码是规则模板**，15 个硬编码 Cypher 模板精确覆盖了几乎所有测试问题
- **评估报告中三个多步场景全部走了 `rule_based_multistep`**，LLM 规划未真正上位
- **评估标准是关键词包含**（`must_include`），不是语义正确性校验
- **代码中硬编码了全部客户名、品牌名、城市名**，换域必须改代码

也就是说，当前 100% 的通过率主要由规则保证，LLM 的真实泛化能力尚未被检验。在通往泛化应用和动态场景切换的路上，这是必须首先解决的问题。

## 1. 阶段目标

1. 引入"纯 LLM"评估模式，量化规则 vs LLM 的真实差距
2. 新增规则覆盖范围之外的测试集，测试 LLM 对未见问题的泛化能力
3. 将硬编码的领域数据改为动态获取，为跨域切换扫清障碍
4. 产出可量化的决策依据：LLM 主路径是否可靠到足以逐步替代规则

## 2. 实施任务

### Phase 3.1：纯 LLM 评估模式（核心，最高优先级）

> 目的：关闭所有规则 fallback，让 LLM 裸跑，暴露真实基线。

#### 3.1.1 增加 `FORCE_LLM` 配置开关

在 `config.py` 的 `Settings` 中新增：

```python
force_llm: bool = Field(default=False, alias="FORCE_LLM")
```

语义：当 `force_llm=True` 时，全链路禁用规则 fallback。LLM 失败即失败，不兜底。

#### 3.1.2 改造 service.py 的 fallback 链

需要改造四个阶段：

| 阶段 | 当前行为 | `force_llm=True` 时行为 |
|------|----------|------------------------|
| `_resolve_intent` | LLM 失败 → 规则 fallback | LLM 失败 → 直接抛异常 |
| `_resolve_plan` | LLM 失败 → `plan_with_rules()` | LLM 失败 → 直接抛异常 |
| `_run_single_question` | LLM Cypher 失败/空结果 → `generate_with_rules()` | LLM 失败 → 直接抛异常 |
| `_resolve_answer` | LLM 失败 → `compose_with_template()` | LLM 失败 → 直接抛异常 |

改造方式：在每个 fallback 分支入口加 `if self.settings.force_llm: raise`。

关键约束：
- **不删除任何规则代码**，只是在 `force_llm` 模式下跳过
- **不影响默认行为**，`FORCE_LLM=false`（默认值）时行为与现在完全一致
- trace 中记录 `force_llm=true`，方便报告区分

#### 3.1.3 改造 run_eval.py 支持双模式评估

新增命令行参数或环境变量，支持在一次评估中同时跑两组：

```
python eval/run_eval.py                    # 默认模式（规则+LLM）
python eval/run_eval.py --force-llm        # 纯 LLM 模式
python eval/run_eval.py --compare          # 两组都跑，对比报告
```

`--compare` 模式的输出报告新增对比维度：

| 用例 | 规则+LLM 通过 | 纯 LLM 通过 | 差异原因 |
|------|:---:|:---:|------|
| S1-1 | ✓ | ✓ | — |
| S2-1 | ✓ | ✗ | Cypher 语法错误 |
| S4-2 | ✓ | ✗ | 规划步骤缺失 |

#### 3.1.4 评估报告扩展

在 HTML 报告中新增以下统计卡片：

- **纯 LLM 通过率**（不含规则 fallback）
- **规则依赖率**：有多少用例是靠规则 fallback 才通过的
- **各阶段 LLM 成功率**：意图 / 规划 / Cypher / 回答各自的 LLM 独立成功率
- **Cypher 可执行率**：LLM 生成的 Cypher 能通过 Neo4j EXPLAIN 的比例
- **平均延迟对比**：规则模式 vs LLM 模式

预期产出：一个能直观回答"LLM 到底行不行"的量化报告。

---

### Phase 3.2：规则盲区测试集

> 目的：新增一批无法被现有规则模板命中的问题，验证 LLM 的开放问题处理能力。

#### 3.2.1 新增 `generalization` 测试组

在 `tests/test_scenarios.yaml` 中新增第三组 `generalization`，问题设计原则：

- **不包含任何已有规则模板的触发关键词**
- **覆盖现有 Schema 但走不同查询路径**
- **包含需要 LLM 理解语义才能正确生成 Cypher 的问题**

计划新增 15 条测试用例，分为以下几类：

**A 类：同域但换角度（5 条）**

已有规则只覆盖 `cop > 6` 的过滤，换成其他属性过滤：

```yaml
- id: G1
  question: 噪音低于 70 分贝的冷水机组有哪些？
  must_include: [型号, 噪音]
  note: 规则模板无 noise_db 过滤

- id: G2
  question: 价格在 80 万以上的设备型号有哪些？
  must_include: [型号, 价格]
  note: 规则模板无 price_wan 过滤

- id: G3
  question: 格力有几个型号的冷水机组？
  must_include: [格力]
  note: 需 COUNT，但规则只有品牌列表查询

- id: G4
  question: 使用 R-410A 制冷剂的设备有哪些？
  must_include: [型号, R-410A]
  note: 规则只硬编码了 R-22 场景

- id: G5
  question: 重量最轻的冷水机组是哪个型号？
  must_include: [型号, 重量]
  note: 需 ORDER BY weight_kg ASC LIMIT 1
```

**B 类：跨域新路径（5 条）**

已有规则覆盖 Customer→Project→Model 路径，换成其他组合：

```yaml
- id: G6
  question: 哪些项目同时安装了开利和约克的设备？
  must_include: [项目]
  note: 需两个 MATCH + 交集，规则无此模板

- id: G7
  question: 华润的项目分布在哪些城市？
  must_include: [华润, 城市]
  note: Customer→Project 只取城市，不涉及设备

- id: G8
  question: 安装数量超过 5 台的项目有哪些？
  must_include: [项目]
  note: 需 SUM(i.quantity) > 5，规则无聚合过滤

- id: G9
  question: 哪些客户的项目用了大金的设备？
  must_include: [客户]
  note: 反向路径 Model→Install→Project→Customer

- id: G10
  question: 苏州有哪些客户有项目？
  must_include: [客户]
  note: Project[city]→Customer 反向，规则只覆盖 Customer→Project
```

**C 类：复杂聚合与推理（5 条）**

```yaml
- id: G11
  question: 每个品牌的平均能效比是多少？
  must_include: [品牌, 平均]
  note: GROUP BY brand + AVG(cop)，规则只有品牌占比

- id: G12
  question: 哪个客户的项目数量最多？
  must_include: [客户]
  note: Customer→Project + COUNT，规则只有设备数量聚合

- id: G13
  question: 建设中的项目有多少个？
  must_include: [项目]
  note: Project[status=建设中] + COUNT

- id: G14
  question: 万科和华润相比，谁的项目总面积更大？
  must_include: [万科, 华润]
  note: 需 SUM(area_sqm) + 对比，规则只有城市能效对比

- id: G15
  question: 最近开工的 5 个项目是哪些？
  must_include: [项目]
  note: ORDER BY start_date DESC LIMIT 5，规则无此模板
```

#### 3.2.2 评估报告按组别出通过率

报告中分三组独立统计：

| 测试组 | 用例数 | 规则+LLM 通过率 | 纯 LLM 通过率 |
|--------|--------|:---:|:---:|
| baseline | 13 | — | — |
| challenge | 20 | — | — |
| **generalization** | **15** | — | — |

`generalization` 组的纯 LLM 通过率是本阶段最核心的指标。

---

### Phase 3.3：领域硬编码解耦

> 目的：让代码不再绑定特定客户名、品牌名、城市名，为动态场景切换铺路。

#### 3.3.1 实体枚举值动态化

将 `query.py` 中的硬编码列表改为启动时从 Neo4j 查询：

```python
# 现在（hardcoded）
CUSTOMERS = ["万科", "华润", "招商蛇口", ...]
BRANDS = ["开利", "约克", "大金", ...]
CITIES = ["深圳", "上海", ...]

# 改造后（dynamic）
class DomainRegistry:
    def __init__(self, settings: Settings):
        executor = Neo4jExecutor(settings)
        self.customers = executor.query_flat("MATCH (c:Customer) RETURN c.name")
        self.brands = executor.query_flat("MATCH (m:Model) RETURN DISTINCT m.brand")
        self.cities = executor.query_flat("MATCH (p:Project) RETURN DISTINCT p.city")
        self.project_types = executor.query_flat("MATCH (p:Project) RETURN DISTINCT p.type")
        self.categories = executor.query_flat("MATCH (c:Category) WHERE c.parent_id IS NOT NULL RETURN c.name")
        executor.close()
```

启动时查询一次，缓存在 `KGQAService` 实例上。换数据集后自动生效，无需改代码。

影响范围：
- `query.py` 的 `generate_with_rules()` 中所有实体名匹配逻辑
- `schema.py` 的 `infer_focus_entities()` 中客户名关键词
- `router.py` 的 `_classify_with_rules()` 中部分关键词

#### 3.3.2 多步上下文别名配置化

将 `service.py` 中硬编码的 `aliases` 字典提取到 `schema.yaml`：

```yaml
# schema.yaml 新增
column_aliases:
  型号:
    zh: [设备型号, 设备名称, 机型, 可替代型号, 可替代设备]
    en: [name, model, model_name]
  品牌:
    zh: [品牌名称]
    en: [brand]
  能效比:
    zh: []
    en: [COP, cop]
  制冷量:
    zh: []
    en: [cooling_kw]
  项目:
    zh: [项目名称]
    en: [project, project_name]
```

`service.py` 的 `_context_keys_for()` 改为从 Schema 加载别名，不再硬编码。

#### 3.3.3 few-shot 评分权重自动化

将 `schema.py` 中硬编码的关键词权重（"冷水机组"+2, "R-22"+3, "替代"+3）改为从 Schema 自动派生：

- 实体名和关系名自动获得基础权重
- 属性名（特别是枚举型属性的值）自动获得匹配权重
- 不再手工维护权重表

---

### Phase 3.4：评估框架增强

> 目的：让评估结果能直接指导决策，而不只是"通过/不通过"。

#### 3.4.1 Cypher 正确性独立评估

新增金标准 Cypher 字段，对 LLM 生成的 Cypher 做结构化比对：

```yaml
- id: S1-1
  question: 冷水机组有哪些型号？
  must_include: [型号, 品牌]
  gold_cypher: |
    MATCH (c:Category {name: '冷水机组'})<-[:BELONGS_TO]-(m:Model)
    RETURN m.name AS 型号, m.brand AS 品牌, m.cop AS 能效比, m.cooling_kw AS 制冷量
```

评估维度：
- Cypher 是否可执行（语法正确）
- Cypher 返回结果是否与金标准结果集一致（集合相等或子集）
- 如果结果不一致，差异是什么（多了/少了哪些行）

#### 3.4.2 延迟分布统计

在报告中新增延迟分析：

- P50 / P90 / P99 延迟
- 按意图类型分组的延迟分布
- LLM 调用次数与延迟的相关性
- 超时（>30s）的用例列表

---

## 3. 实施顺序与依赖关系

```
Phase 3.1（纯 LLM 模式）
  ├── 3.1.1 config 加 FORCE_LLM ───┐
  ├── 3.1.2 service fallback 改造 ──┼── 3.1.3 eval 双模式 ── 3.1.4 报告扩展
  └────────────────────────────────┘         │
                                             ▼
                                     【运行首次纯 LLM 评估】
                                             │
                            ┌────────────────┼────────────────┐
                            ▼                ▼                ▼
                     Phase 3.2         Phase 3.3         Phase 3.4
                  （泛化测试集）     （领域解耦）       （评估增强）
                            │                │                │
                            └────────────────┼────────────────┘
                                             ▼
                                     【运行完整评估】
                                             │
                                             ▼
                                       决策点：
                               LLM 泛化通过率 ≥ 70%？
                              /                     \
                           YES                       NO
                            │                         │
                    继续解耦 + 生产化            优先优化 prompt
                    Phase 3.3 深化               + few-shot + 换模型
```

## 4. 预期工作量

| Phase | 预计改动文件 | 新增/修改行数 | 预计耗时 |
|-------|-------------|:---:|------|
| 3.1.1 config 开关 | config.py | ~5 行 | 10 min |
| 3.1.2 service 改造 | service.py | ~30 行 | 30 min |
| 3.1.3 eval 双模式 | run_eval.py | ~60 行 | 45 min |
| 3.1.4 报告扩展 | run_eval.py | ~80 行 | 45 min |
| 3.2.1 泛化测试集 | test_scenarios.yaml | ~90 行 | 30 min |
| 3.2.2 报告分组 | run_eval.py | ~20 行 | 15 min |
| 3.3.1 实体动态化 | query.py, schema.py, service.py | ~80 行 | 1.5 h |
| 3.3.2 别名配置化 | schema.yaml, service.py, schema.py | ~50 行 | 1 h |
| 3.3.3 权重自动化 | schema.py | ~40 行 | 45 min |
| 3.4.1 Cypher 评估 | run_eval.py, test_scenarios.yaml | ~100 行 | 1 h |
| 3.4.2 延迟分析 | run_eval.py | ~50 行 | 30 min |

**总计约 8 小时**。Phase 3.1 是关键路径（约 2 小时），必须先完成并跑一轮评估后，再并行推进 3.2/3.3/3.4。

## 5. 决策门槛

首次纯 LLM 评估跑出后，根据结果决定方向：

| 纯 LLM 通过率 | 判断 | 行动 |
|:---:|------|------|
| ≥ 80% | LLM 能力充分 | 加速解耦（Phase 3.3），逐步删减规则模板 |
| 60%–80% | LLM 能力基本够用 | 优化 prompt + few-shot，Phase 3.3 同步推进 |
| < 60% | LLM 能力不足 | 暂缓解耦，优先调优 prompt / 换模型 / 增加 few-shot |

泛化测试集（`generalization` 组）的独立通过率是额外参考：

| 泛化通过率 | 判断 |
|:---:|------|
| ≥ 70% | Schema 注入 + few-shot 策略有效，可推广到新域 |
| < 50% | 需要更强的 Schema 检索或提示工程优化 |

## 6. 风险与缓解

| 风险 | 可能性 | 缓解措施 |
|------|:---:|------|
| 纯 LLM 模式下 Cypher 生成大面积失败 | 高 | 不删规则代码，`FORCE_LLM` 只是评估开关 |
| 阿里 Dashscope 限流导致评估超时 | 中 | eval 中加异常重试 + 用例级超时保护 |
| 泛化测试集设计的问题恰好被 few-shot 覆盖 | 低 | 问题设计时逐条比对 few_shots.yaml 确保不重叠 |
| 动态实体查询增加启动延迟 | 低 | 查询结果缓存，仅启动时执行一次 |
