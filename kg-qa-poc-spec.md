# 知识图谱智能问答 — 演示验证项目（PoC）

> 场景定义 · 需求清单 · 技术路线
>
> *KG-Augmented QA — Demo & Validation Project*
>
> 2026年3月

---

# 1. 项目概述

## 1.1 背景

项目已完成知识图谱平台的基础建设，实现了图谱定义、数据绑定、持续集成管线、图谱浏览与拓展等功能。现需引入 AI 能力，让业务人员能够通过自然语言直接查询图谱中的实体、关系和属性信息。

## 1.2 目标

- 验证“自然语言 → 图谱查询 → 智能回答”的技术可行性
- 覆盖客户、项目、设备三个核心域的典型查询场景
- 输出可复用的技术架构和组件设计，为后续产品化提供基础
- 评估不同查询类型的准确率和响应质量

## 1.3 范围界定

| **范围内（In Scope）** | **范围外（Out of Scope）** |
| --- | --- |
| 模拟图谱数据（客户/项目/设备） | 真实业务数据接入 |
| 四类核心查询场景的实现 | 多轮对话 / 会话记忆 |
| 命令行 / Web UI 演示界面 | 生产级性能优化 |
| 准确率评估报告 | 权限控制 / 多租户 |

# 2. 模拟数据模型

## 2.1 本体定义（Ontology）

演示图谱包含以下五类核心实体和其关系：

| **实体类型** | **属性** | **说明** |
| --- | --- | --- |
| **客户 Customer** | id, name, industry, region, level | 如：万科、华润、招商蛇口等 |
| **项目 Project** | id, name, type, city, start_date, status, area_sqm | 商业综合体、住宅、产业园区等 |
| **设备类型 Category** | id, name, system, parent_id | 树形体系：空调系统→冷水机组 |
| **设备型号 Model** | id, name, brand, cooling_kw, cop, refrigerant, noise_db, weight_kg, price_wan | 具体型号及性能参数 |
| **设备安装 Installation** | id, model_id, project_id, quantity, install_date, status | 某项目中实际安装的设备记录 |

## 2.2 关系定义

| **关系** | **起点** | **终点** | **基数** |
| --- | --- | --- | --- |
| OWNS_PROJECT | Customer | Project | 1:N |
| HAS_INSTALLATION | Project | Installation | 1:N |
| USES_MODEL | Installation | Model | N:1 |
| BELONGS_TO | Model | Category | N:1 |
| PARENT_OF | Category | Category | 1:N |
| CAN_REPLACE | Model | Model | N:N |

## 2.3 模拟数据规模

- 客户：10家（覆盖地产、商业、产业等行业）
- 项目：30个（分布在不同城市，类型各异）
- 设备类型：20个节点（三级树状体系）
- 设备型号：50个（覆盖开利、约克、大金、格力等品牌）
- 设备安装记录：200条

# 3. 查询场景定义

演示场景分为四类，按复杂度递增排列。每类场景包含典型问题示例、查询路径和预期回答格式。

## 3.1 场景一：单域精确查询

在单一实体域内进行属性查询和过滤，路径最短，复杂度最低。

| **#** | **示例问题** | **查询路径** | **回答格式** |
| --- | --- | --- | --- |
| S1-1 | 冷水机组有哪些型号？ | Category → Model | 列表 |
| S1-2 | 能效比在6以上的冷水机组有哪些？ | Category → Model [COP>6] | 过滤列表 |
| S1-3 | 开利 30XA-300 的详细参数是什么？ | Model → attributes | 属性卡片 |
| S1-4 | 开利和约克的冷水机组有什么区别？ | Model → attributes (x2) | 对比表格 |

## 3.2 场景二：跨域关联查询

查询路径跨越客户、项目、设备多个域，需要3–4跳图遍历。

| **#** | **示例问题** | **查询路径** | **回答格式** |
| --- | --- | --- | --- |
| S2-1 | 万科的项目分别用了哪些品牌的冷水机组？ | Customer → Project → Install → Model | 分项目列表 |
| S2-2 | 哪些项目安装了开利的设备？ | Model [brand] → Install → Project | 项目列表 |
| S2-3 | 深圳区域的项目都用了什么设备？ | Project [city] → Install → Model | 按项目分组 |

## 3.3 场景三：聚合统计查询

需要对查询结果做分组、计数、排序等聚合操作。

| **#** | **示例问题** | **查询逻辑** | **回答格式** |
| --- | --- | --- | --- |
| S3-1 | 哪个客户使用开利设备最多？ | Model → Install → Project → Customer + GROUP BY + COUNT | 排名列表 |
| S3-2 | 各品牌设备在所有项目中的占比是多少？ | Install → Model → brand + GROUP BY + ratio | 饼图/比例表 |
| S3-3 | 哪个城市的项目设备总制冷量最大？ | Project [city] → Install → Model.cooling_kw + SUM + RANK | 城市排名 |

## 3.4 场景四：条件交叉 + 多步推理

同时涉及多个域的属性过滤，或需要多步推理才能回答的复杂问题。

| **#** | **示例问题** | **查询逻辑** |
| --- | --- | --- |
| S4-1 | 2023年后的项目中，有没有还在用R-22制冷剂设备的？ | Project [start>2023] → Install → Model [refrigerant=R-22] |
| S4-2 | 万科的商业项目中，能效比最低的设备是哪台？有没有可替代方案？ | Customer → Project [type=商业] → Install → Model [MIN COP] → CAN_REPLACE |
| S4-3 | 对比深圳和上海的项目，哪边的设备平均能效比更高？ | Project [city=深圳|上海] → Install → Model.cop → AVG + COMPARE |

# 4. 技术架构

## 4.1 整体分层

系统采用五层架构，从上到下依次为：

- **接入层（API / UI）：**提供 Web UI 和 CLI 两种交互方式，接收用户自然语言输入。
- **意图路由层（Intent Router）：**判断查询类型（单域/跨域/聚合/多步），分发到对应的查询策略。
- **查询规划层（Query Planner）：**将复杂问题拆解为多个子查询，确定执行顺序和依赖关系。
- **图谱查询层（KG Query）：**执行实际的图谱查询，支持 NL2Cypher 和结构化 API 两种模式。
- **回答生成层（Answer Generator）：**将查询结果序列化后注入 LLM，生成自然语言回答。

## 4.2 查询类型与策略映射

| **查询类型** | **意图路由结果** | **查询策略** | **对应场景** |
| --- | --- | --- | --- |
| 单域精确 | SINGLE_DOMAIN | NL2Cypher 直接生成 | S1-1 ~ S1-4 |
| 跨域关联 | CROSS_DOMAIN | NL2Cypher + 多跳路径模板 | S2-1 ~ S2-3 |
| 聚合统计 | AGGREGATION | NL2Cypher + 聚合函数 | S3-1 ~ S3-3 |
| 多步推理 | MULTI_STEP | Agent 规划 + 多次查询 | S4-1 ~ S4-3 |

## 4.3 核心组件设计

### 4.3.1 Schema 注入模块

将图谱本体定义转化为 LLM 可理解的文本格式，注入 system prompt。包含：

- 实体类型及其属性字段（含类型、枚举值）
- 关系类型及方向
- 典型查询路径模板
- 分层注入策略：根据意图路由结果只注入相关域的 Schema

### 4.3.2 NL2Cypher 生成器

LLM 将自然语言问题转化为 Cypher 查询语句。核心设计要点：

- few-shot 示例库：每种查询类型准备 3–5 个示例对（自然语言 → Cypher）
- 查询校验：生成后先做语法检查，防止无效 Cypher 执行
- 安全护栏：只允许 READ 操作，拒绝 CREATE/DELETE/SET

### 4.3.3 查询规划器（Agent 模式）

对于场景四的多步推理问题，采用 ReAct 模式：

- LLM 先生成查询计划（Plan），拆解为多个子查询
- 逐步执行，每步结果作为下一步的输入上下文
- 最终综合所有子查询结果生成回答
- 最大步数限制：5步（防止无限循环）

### 4.3.4 结果序列化与回答生成

根据查询类型选择序列化格式：

- 属性查询 → Key-Value 格式
- 对比查询 → Markdown 表格
- 列表查询 → 编号列表
- 聚合统计 → 排名表 + 数值

序列化结果 + 用户问题 + Schema 一起注入 LLM，生成最终自然语言回答。

# 5. 技术选型

| **层次** | **技术选型** | **说明** |
| --- | --- | --- |
| 图数据库 | Neo4j Community | Cypher 生态最成熟，LLM 生成准确率最高 |
| LLM | Claude API / DeepSeek / Qwen | PoC 阶段用 Claude；私有化部署备选国产模型 |
| 后端框架 | Python + FastAPI | 异步架构，易于集成 LLM SDK 和 Neo4j Driver |
| Neo4j 驱动 | neo4j Python driver | 官方驱动，支持异步 |
| 前端（可选） | Streamlit / Gradio | 快速搭建演示界面，支持对话式交互 |
| 评估框架 | pytest + 自定义评分 | 自动化测试场景覆盖率和回答质量 |

# 6. 项目结构

推荐的项目目录结构如下：

```
kg-qa-poc/
├── README.md                    # 项目说明
├── pyproject.toml               # 依赖管理
├── docker-compose.yml           # Neo4j + 应用编排
├── data/
│   ├── schema.yaml              # 图谱本体定义
│   ├── seed_data.cypher         # 模拟数据初始化脚本
│   └── few_shots.yaml           # NL2Cypher 示例库
├── src/
│   ├── schema/                  # Schema 加载与注入
│   ├── router/                  # 意图路由
│   ├── planner/                 # 查询规划器
│   ├── query/                   # NL2Cypher + 执行
│   ├── serializer/              # 结果序列化
│   ├── generator/               # LLM 回答生成
│   └── api/                     # FastAPI 接口
├── ui/                          # Streamlit 演示界面
├── tests/
│   ├── test_scenarios.yaml      # 测试用例定义
│   └── test_e2e.py              # 端到端测试
└── eval/                        # 评估报告生成
```

# 7. 实施计划

建议分四个迭代步骤执行，每步产出可演示的成果：

## 7.1 Phase 1：基础搭建（第1天）

- Neo4j 环境搭建（Docker）
- 模拟数据建模与导入（10客户 + 30项目 + 50型号 + 200安装记录）
- Schema 定义文件编写（schema.yaml）
- 验证标准：可在 Neo4j Browser 中查询所有实体和关系

## 7.2 Phase 2：单域查询负通（第2天）

- 实现 Schema 注入模块
- 实现 NL2Cypher 生成器 + Cypher 校验
- 实现结果序列化 + 回答生成
- few-shot 示例库编写（场景一）
- 验证标准：S1-1 ~ S1-4 全部走通

## 7.3 Phase 3：跨域 + 聚合 + 多步（第3天）

- 实现意图路由层
- 实现查询规划器（Agent 模式）
- 扩展 few-shot 示例库（场景二、三、四）
- 验证标准：全部 12 个示例问题均可回答

## 7.4 Phase 4：UI + 评估（第4天）

- Streamlit 演示界面搭建
- 端到端测试套件
- 准确率评估报告生成
- 验证标准：可现场演示的完整系统

# 8. 评估方案

## 8.1 评估维度

| **维度** | **定义** | **目标** |
| --- | --- | --- |
| Cypher 生成正确率 | 生成的 Cypher 可执行且返回预期结果 | ≥ 80%（场景1-3） |
| 意图路由准确率 | 正确判断查询类型 | ≥ 90% |
| 回答完整性 | 回答包含所有必要信息，无编造 | 人工评分 ≥ 4/5 |
| 响应延迟 | 从提问到返回回答的端到端时间 | 单域 <3s，多步 <10s |

## 8.2 测试用例设计

- 每个场景至少 5 个测试问题，含金标准答案
- 包含正常问题 + 边界问题（如查询不存在的实体）
- 包含模糊表述（如“开利的机器”代替“开利品牌的冷水机组型号”）
- 自动化执行 + 评分脚本，生成 HTML 评估报告

# 9. 核心 Prompt 设计要点

## 9.1 Schema 注入 Prompt 模板

以下为 Schema 注入的参考格式，实际使用时根据意图路由结果动态裁剪：

```
## 图谱 Schema

节点类型：
- Customer: {id, name, industry, region, level}
- Project: {id, name, type, city, start_date, status, area_sqm}
- Category: {id, name, system, parent_id}
- Model: {id, name, brand, cooling_kw, cop, refrigerant, noise_db, weight_kg, price_wan}
- Installation: {id, model_id, project_id, quantity, install_date, status}

关系类型：
- (Customer)-[:OWNS_PROJECT]->(Project)
- (Project)-[:HAS_INSTALLATION]->(Installation)
- (Installation)-[:USES_MODEL]->(Model)
- (Model)-[:BELONGS_TO]->(Category)
- (Category)-[:PARENT_OF]->(Category)
- (Model)-[:CAN_REPLACE]->(Model)
```

## 9.2 NL2Cypher few-shot 示例结构

每个示例包含三个字段：

```yaml
- question: "冷水机组有哪些型号？"
  intent: SINGLE_DOMAIN
  cypher: |
    MATCH (c:Category {name: '冷水机组'})<-[:BELONGS_TO]-(m:Model)
    RETURN m.name, m.brand, m.cop, m.cooling_kw

- question: "万科的项目用了哪些品牌的冷水机组？"
  intent: CROSS_DOMAIN
  cypher: |
    MATCH (cust:Customer {name: '万科'})-[:OWNS_PROJECT]->(p:Project)
          -[:HAS_INSTALLATION]->(i:Installation)-[:USES_MODEL]->(m:Model)
    RETURN p.name, m.brand, m.name, i.quantity
```

## 9.3 回答生成 Prompt 要点

- 明确指定回答语言（中文）
- 要求“仅基于提供的图谱数据回答，不要编造”
- 对比场景要求输出表格
- 聚合场景要求包含具体数值
- 查询无结果时要求明确说明“图谱中未找到相关信息”
