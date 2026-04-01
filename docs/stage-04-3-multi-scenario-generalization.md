# Stage 04-3: 多场景泛化验证

## 为什么现在做

当前系统只在一个 HVAC 冷水机组数据集上运行过。无论通过率多少，都无法证明 agent 的泛化能力——LLM 可能只是碰巧对这个领域有先验知识。构造新数据场景并运行评估，是验证架构是否真正 schema 驱动的唯一方式。

## 两阶段策略

| 阶段 | 场景 | 图谱结构 | 验证目标 |
|---|---|---|---|
| Phase 1 | 建筑行业 · 电梯设备 | 同构（Customer/Project/Installation/Model/Category） | agent 能否在同结构不同数据下生成正确 Cypher |
| Phase 2 | 物业资产经营 | 异构（全新实体类型和关系） | 系统是否真正 schema 驱动，代码能否适配任意图谱 |

Phase 1 先行，快速出结论；Phase 2 基于 Phase 1 结果决定代码泛化的优先级。

---

## Phase 1：建筑行业 · 电梯设备

### 设计思路

保持与 HVAC 场景同构的实体-关系骨架，但将设备类别从冷水机组换成电梯。这样 DomainRegistry 的结构代码不需要改动（仍然是 Customer/Project/Model/Category），但 **schema 属性、种子数据、测试问题全部重新设计**，LLM 不可能靠冷水机组的先验知识作弊。

### schema 差异

schema.yaml 需要更新 Model 属性。其他实体（Customer、Project、Installation、Category）的属性结构可保持一致。

| 字段 | HVAC 冷水机组 | 电梯 |
|---|---|---|
| Model.name | 30XA-300 | OTIS GeN2-MR |
| Model.brand | 开利/约克/大金/格力/美的/海尔 | 奥的斯/三菱/通力/蒂森克虏伯/日立/迅达 |
| Model.cooling_kw → **load_kg** | 制冷量 kW | 额定载重 kg |
| Model.cop → **speed_ms** | 能效比 | 额定速度 m/s |
| Model.refrigerant → **drive_type** | 制冷剂 | 驱动方式（永磁同步/异步变频/液压） |
| Model.noise_db → **noise_db** | 噪音 dB | 运行噪音 dB（保留） |
| Model.weight_kg → **floors** | 重量 kg | 服务层站数 |
| Model.price_wan → **price_wan** | 价格万元 | 价格万元（保留） |

Category 层级从 HVAC 换为电梯体系：

```
电梯系统 (level 1)
├── 乘客电梯 (level 2)
│   ├── 有机房乘客梯 (level 3)
│   └── 无机房乘客梯 (level 3)
├── 货梯 (level 2)
│   ├── 载货电梯 (level 3)
│   └── 汽车电梯 (level 3)
├── 自动扶梯 (level 2)
│   ├── 室内扶梯 (level 3)
│   └── 室外扶梯 (level 3)
└── 特种电梯 (level 2)
    ├── 观光电梯 (level 3)
    ├── 医用电梯 (level 3)
    └── 消防电梯 (level 3)
```

Customer 和 Project 保持建筑行业背景，但使用不同的客户名和项目：

- Customer：绿城、中海、金茂、世茂、融创、新城、雅居乐、远洋、首开、中梁
- Project.type：住宅/商业/医院/酒店/写字楼
- Project.city：广州/北京/武汉/重庆/天津/厦门

### 数据规模

| 实体 | 数量 | 说明 |
|---|---|---|
| Customer | 10 | 不同于 HVAC 的地产客户 |
| Project | 25-30 | 不同城市、不同类型 |
| Category | 12-15 | 电梯分类体系（3 层） |
| Model | 40-50 | 6 品牌 × 多型号 |
| Installation | 50-60 | 部署记录 |
| CAN_REPLACE 关系 | 10-15 | 型号间替代关系 |

### schema.yaml 改动

新建 `data/schema_elevator.yaml`：

```yaml
dataset: elevator_poc
description: 电梯设备知识图谱问答演示数据集
entities:
  - name: Customer
    description: 客户信息
    properties:
      id: string
      name: string
      industry: string
      region: string
      level: string
      dataset: string
    filterable_fields: [name, industry, region, level]
  - name: Project
    description: 项目信息
    properties:
      id: string
      name: string
      type: string
      city: string
      start_date: date
      status: string
      area_sqm: number
      dataset: string
    filterable_fields: [name, type, city, start_date, status]
  - name: Category
    description: 电梯类型
    properties:
      id: string
      name: string
      system: string
      parent_id: string
      level: number
      dataset: string
    filterable_fields: [name, system, parent_id, level]
  - name: Model
    description: 电梯型号
    properties:
      id: string
      name: string
      brand: string
      load_kg: number
      speed_ms: number
      drive_type: string
      noise_db: number
      floors: number
      price_wan: number
      dataset: string
    filterable_fields: [name, brand, load_kg, speed_ms, drive_type, price_wan]
  - name: Installation
    description: 电梯安装记录
    properties:
      id: string
      model_id: string
      project_id: string
      quantity: number
      install_date: date
      status: string
      dataset: string
    filterable_fields: [model_id, project_id, quantity, install_date, status]
relationships:
  - name: OWNS_PROJECT
    from: Customer
    to: Project
    cardinality: 1:N
    direction: out
  - name: HAS_INSTALLATION
    from: Project
    to: Installation
    cardinality: 1:N
    direction: out
  - name: USES_MODEL
    from: Installation
    to: Model
    cardinality: N:1
    direction: out
  - name: BELONGS_TO
    from: Model
    to: Category
    cardinality: N:1
    direction: out
  - name: PARENT_OF
    from: Category
    to: Category
    cardinality: 1:N
    direction: out
  - name: CAN_REPLACE
    from: Model
    to: Model
    cardinality: N:N
    direction: out
paths:
  SINGLE_DOMAIN:
    - Category -> Model
    - Model -> attributes
  CROSS_DOMAIN:
    - Customer -> Project -> Installation -> Model
    - Project -> Installation -> Model
  AGGREGATION:
    - Model -> Installation -> Project -> Customer
    - Project -> Installation -> Model
  MULTI_STEP:
    - Project[filter] -> Installation -> Model[filter]
    - Customer -> Project[type] -> Installation -> Model -> CAN_REPLACE
column_aliases:
  项目:
    zh: [项目名称]
    en: [project, project_name]
  型号:
    zh: [电梯型号, 设备名称, 机型, 可替代型号, 替代型号]
    en: [name, model, model_name]
  品牌:
    zh: [品牌名称]
    en: [brand]
  速度:
    zh: [额定速度]
    en: [speed_ms]
  载重:
    zh: [额定载重]
    en: [load_kg]
```

### 测试用例设计

新建 `tests/test_scenarios_elevator.yaml`，~48 条，对标现有用例结构：

**Baseline（~13 条）**

```yaml
baseline:
  - id: E-S1-1
    question: 乘客电梯有哪些型号？
    must_include: [型号, 品牌]
  - id: E-S1-2
    question: 额定速度在 3m/s 以上的电梯有哪些？
    must_include: [型号, 速度]
  - id: E-S1-3
    question: 奥的斯 GeN2-MR 的详细参数是什么？
    must_include: [GeN2-MR, 奥的斯]
  - id: E-S1-4
    question: 奥的斯和三菱的乘客电梯有什么区别？
    must_include: [奥的斯, 三菱]
  - id: E-S2-1
    question: 绿城的项目分别用了哪些品牌的电梯？
    must_include: [绿城, 项目]
  - id: E-S2-2
    question: 哪些项目安装了通力的电梯？
    must_include: [项目]
  - id: E-S2-3
    question: 武汉的项目都用了什么电梯？
    must_include: [武汉, 项目]
  - id: E-S3-1
    question: 哪个客户使用奥的斯电梯最多？
    must_include: [客户]
  - id: E-S3-2
    question: 各品牌电梯在所有项目中的占比是多少？
    must_include: [品牌, 占比]
  - id: E-S3-3
    question: 哪个城市的项目电梯安装总量最大？
    must_include: [城市]
  - id: E-S4-1
    question: 2024年后的项目中有没有还在用液压驱动电梯的？
    must_include: [项目]
  - id: E-S4-2
    question: 绿城的医院项目中载重最大的电梯是哪台？有没有可替代方案？
    must_include: [型号, 品牌]
  - id: E-S4-3
    question: 对比武汉和北京的项目，哪边的电梯平均速度更高？
    must_include: [武汉, 北京]
```

**Challenge（~20 条）** — 同义改写、口语表达

```yaml
challenge:
  - id: E-C1
    question: 帮我列一下乘客电梯的设备型号
    must_include: [型号, 品牌]
  - id: E-C2
    question: 速度超过 3 的电梯有哪些？
    must_include: [型号]
  - id: E-C3
    question: GeN2-MR 参数给我看下
    must_include: [GeN2-MR]
  - id: E-C5
    question: 绿城名下各项目都在用哪些电梯品牌？
    must_include: [绿城, 项目]
  - id: E-C7
    question: 武汉项目装了哪些电梯型号？
    must_include: [项目]
  - id: E-C8
    question: 奥的斯电梯用量最多的是哪个客户？
    must_include: [客户]
  - id: E-C11
    question: 2024 年以后还有项目在用液压驱动吗？
    must_include: [项目]
  - id: E-C17
    question: 有没有品牌 XYZ 的电梯？
    allow_empty: true
  - id: E-C18
    question: 火星区域的项目用了什么电梯？
    allow_empty: true
  # ... 补全至 ~20 条
```

**Generalization（~15 条）**

```yaml
generalization:
  - id: E-G1
    question: 噪音低于 50 分贝的电梯有哪些？
    must_include: [型号, 噪音]
  - id: E-G2
    question: 价格在 100 万以上的电梯型号有哪些？
    must_include: [型号, 价格]
  - id: E-G3
    question: 日立有几个型号的乘客电梯？
    must_include: [日立]
  - id: E-G4
    question: 使用永磁同步驱动的电梯有哪些？
    must_include: [型号]
  - id: E-G5
    question: 服务层站数最多的电梯是哪个型号？
    must_include: [型号]
  - id: E-G6
    question: 哪些项目同时安装了奥的斯和三菱的电梯？
    must_include: [项目]
  - id: E-G7
    question: 中海的项目分布在哪些城市？
    must_include: [中海, 城市]
  - id: E-G8
    question: 安装数量超过 5 台的项目有哪些？
    must_include: [项目]
  - id: E-G11
    question: 每个品牌的平均额定速度是多少？
    must_include: [品牌, 平均]
  - id: E-G12
    question: 哪个客户的项目数量最多？
    must_include: [客户]
  - id: E-G13
    question: 建设中的项目有多少个？
    must_include: [项目]
  # ... 补全至 ~15 条
```

### 代码改动

| 文件 | 改动 | 原因 |
|---|---|---|
| `src/kgqa/config.py` | `schema_file` 支持通过环境变量 `SCHEMA_FILE` 切换 | 目前 `schema_file` 是硬编码路径，需要能指向不同 schema |
| `src/kgqa/query.py` DomainRegistry | Model 属性查询中去掉 `m.refrigerant`，改为从 schema 动态获取 | 电梯场景没有 refrigerant 字段，有 drive_type |

> 关键判断：DomainRegistry 的 7 个查询（customers/brands/cities/project_types/project_statuses/categories/refrigerants）中，前 6 个对电梯场景完全适用（只是值不同），只有 `refrigerants` 需要替换为 `drive_types`。最简方案：将 `refrigerants` 改名为更通用的 `model_enum_values`，从 schema 的 filterable_fields 中动态决定查哪个字段。

### 实施步骤

```
Step 1: 创建 data/schema_elevator.yaml
Step 2: 创建 data/seed_data_elevator.cypher（10 客户 + 30 项目 + 15 类别 + 45 型号 + 55 安装 + 12 替代）
Step 3: 创建 tests/test_scenarios_elevator.yaml（~48 条用例）
Step 4: config.py 新增 SCHEMA_FILE 环境变量支持
Step 5: DomainRegistry 小改：refrigerants → 泛化为 schema 驱动的枚举字段
Step 6: 加载电梯数据，运行评估，对比两场景通过率
```

### 验证标准

- 电梯场景 eval 48 条用例全部跑通（不要求全部 pass，要求不报错）
- 与 HVAC 场景做分组通过率对比
- 失败用例按原因分类（schema 理解错误 / Cypher 语法错误 / 枚举值未匹配 / 其他）

---

## Phase 2：物业资产经营

### 设计思路

完全不同的图谱拓扑结构，验证系统是否真正 schema 驱动。

### 实体设计

```
经营平台公司 (OperatingCompany)
    │ MANAGES_PROJECT
    ▼
经营项目 (OperatingProject)
    │ HAS_SPACE
    ▼
项目空间 (Space)
    │ OCCUPIED_BY
    ▼
项目租户 (Tenant)
    │ HAS_LEASE
    ▼
租赁合同 (Lease)
    │ HAS_PAYMENT
    ▼
租金付款记录 (Payment)
```

### 实体属性

```yaml
entities:
  - name: OperatingCompany
    description: 物业经营平台公司
    properties:
      id: string
      name: string          # 万物云、碧桂园服务、龙湖智创生活、保利物业...
      parent_group: string   # 母公司集团
      region: string         # 华南/华东/华北
      scale: string          # 大型/中型
      dataset: string
    filterable_fields: [name, parent_group, region, scale]

  - name: OperatingProject
    description: 经营项目
    properties:
      id: string
      name: string           # 万象城深圳店、天街杭州店...
      type: string           # 购物中心/写字楼/产业园/社区商业
      city: string
      total_area_sqm: number
      opening_date: date
      status: string         # 筹备中/运营中/改造中/已关闭
      dataset: string
    filterable_fields: [name, type, city, status, opening_date]

  - name: Space
    description: 项目空间（可租赁单元）
    properties:
      id: string
      name: string           # A101/B2-03/F3-整层
      floor: string          # B1/1F/2F/3F
      area_sqm: number
      space_type: string     # 零售/餐饮/办公/仓储/车位
      monthly_rent_yuan: number  # 月租单价 元/㎡
      dataset: string
    filterable_fields: [name, floor, space_type, area_sqm, monthly_rent_yuan]

  - name: Tenant
    description: 租户
    properties:
      id: string
      name: string           # 星巴克、优衣库、海底捞、瑞幸咖啡...
      industry: string       # 餐饮/零售/服务/办公
      brand_level: string    # 国际一线/国内一线/区域品牌/个体
      dataset: string
    filterable_fields: [name, industry, brand_level]

  - name: Lease
    description: 租赁合同
    properties:
      id: string
      start_date: date
      end_date: date
      monthly_rent: number    # 月租总额
      deposit: number         # 押金
      rent_free_months: number # 免租期月数
      status: string          # 生效中/已到期/已终止
      dataset: string
    filterable_fields: [start_date, end_date, status, monthly_rent]

  - name: Payment
    description: 租金付款记录
    properties:
      id: string
      period: string          # 2025-01/2025-02
      amount: number
      due_date: date
      paid_date: date         # null 表示未付
      status: string          # 已付/逾期/未付
      dataset: string
    filterable_fields: [period, status, amount, due_date]
```

### 关系

```yaml
relationships:
  - name: MANAGES_PROJECT
    from: OperatingCompany
    to: OperatingProject
    cardinality: 1:N
  - name: HAS_SPACE
    from: OperatingProject
    to: Space
    cardinality: 1:N
  - name: OCCUPIED_BY
    from: Space
    to: Tenant
    cardinality: N:1        # 一个空间当前由一个租户占用
  - name: HAS_LEASE
    from: Tenant
    to: Lease
    cardinality: 1:N        # 同一租户可能续签多次
  - name: LEASE_FOR_SPACE
    from: Lease
    to: Space
    cardinality: N:1        # 合同对应具体空间
  - name: HAS_PAYMENT
    from: Lease
    to: Payment
    cardinality: 1:N
```

### 典型问题

```
单域查询：
- "万象城深圳店有哪些空间？"
- "星巴克在哪些项目有门店？"
- "月租单价超过 500 元/㎡的空间有哪些？"

跨域查询：
- "万物云管理的项目中，哪些租户是国际一线品牌？"
- "龙湖天街杭州店的餐饮类租户有哪些？"

聚合查询：
- "各经营公司管理的项目总面积分别是多少？"
- "哪个项目的空置率最高？"（需要计算：无租户空间面积 / 总面积）
- "各行业租户的平均月租是多少？"

多步推理：
- "万物云运营中的购物中心里，月租最低的零售空间是谁在租？合同什么时候到期？"
- "逾期付款最多的租户是哪家？它在哪些项目有门店？"
```

### 必须泛化的代码

Phase 2 的图谱有 6 种实体（不是 5 种），6 种关系（不同于 Phase 1 的关系名），完全不同的属性集。以下代码必须泛化才能支持：

| 文件 | 当前硬编码 | 泛化方案 |
|---|---|---|
| `query.py` DomainRegistry | 7 个固定属性（customers/brands/...）+ 7 条固定 Cypher 查询 | 读取 schema.yaml 的 entities 列表，为每个 entity 的每个 filterable_field 动态生成 `MATCH (n:{Entity}) RETURN DISTINCT n.{field} AS v` |
| `schema.py:96-101` | `Customer→客户`, `Model→设备/型号/品牌` 等 5 组硬编码 | 删除硬编码，仅从 schema.yaml 的 `description` 字段提取关键词 |
| `tools.py:73-89` list_domain_values | 返回 `{customers: [], brands: [], ...}` 固定结构 | 返回 `{EntityName: {field: [values]}}` 结构 |
| `tools.py:146-154` _infer_intent | `"客户"`, `"项目"`, `"品牌"` 等关键词 | 从 schema 的 entity descriptions 和 relationship names 推断，或直接移除（agent 不需要预分类 intent） |
| `tools.py:30-57` tool_specs | 描述文本提到 "customers/brands/cities..." | 改为从 schema 动态生成描述 |

### 预估工作量

| 项 | 工作量 |
|---|---|
| DomainRegistry 泛化 | ~80 行重写 |
| SchemaRegistry focus keywords 泛化 | ~30 行 |
| tools.py 泛化 | ~40 行 |
| 新 schema + seed data + test scenarios | 3 个新文件 |
| eval 适配 + 多场景对比报告 | ~50 行 |

---

## 多场景运行方式

通过环境变量切换场景，无需改代码：

```bash
# === HVAC 冷水机组（默认） ===
python -m kgqa.cli seed-load
python -m eval.run_eval

# === 电梯（Phase 1） ===
DATASET_NAME=elevator_poc \
SCHEMA_FILE=data/schema_elevator.yaml \
SEED_FILE=data/seed_data_elevator.cypher \
EVALUATION_FILE=tests/test_scenarios_elevator.yaml \
python -m kgqa.cli seed-load && python -m eval.run_eval

# === 物业经营（Phase 2） ===
DATASET_NAME=property_ops \
SCHEMA_FILE=data/schema_property.yaml \
SEED_FILE=data/seed_data_property.cypher \
EVALUATION_FILE=tests/test_scenarios_property.yaml \
python -m kgqa.cli seed-load && python -m eval.run_eval
```

### config.py 改动

将 `schema_file` 从硬编码路径改为支持环境变量：

```python
schema_file: Path = Field(default=ROOT / "data" / "schema.yaml", alias="SCHEMA_FILE")
seed_file: Path = Field(default=ROOT / "data" / "seed_data.cypher", alias="SEED_FILE")
evaluation_file: Path = Field(default=ROOT / "tests" / "test_scenarios.yaml", alias="EVALUATION_FILE")
```

---

## 对比分析框架

三场景对比表：

| 指标 | HVAC 冷水机组 | 电梯（Phase 1） | 物业经营（Phase 2） |
|---|---|---|---|
| 总用例数 | 48 | ~48 | ~48 |
| Baseline 通过率 | | | |
| Challenge 通过率 | | | |
| Generalization 通过率 | | | |
| 总通过率 | | | |
| 平均步骤数 | | | |
| 平均延迟 | | | |
| 常见失败模式 | | | |

失败用例归类维度：

- **Schema 理解错误**：Cypher 引用了不存在的属性/关系
- **枚举值未匹配**：用了近似但不精确的枚举值
- **Cypher 语法错误**：合法但语义错误的查询
- **空结果误判**：查询成功但结果为空，未正确处理
- **Intent 推断偏差**：_infer_intent 关键词不适用于新领域

---

## 文件清单

### Phase 1 新建

| 文件 | 说明 |
|---|---|
| `data/schema_elevator.yaml` | 电梯场景 schema |
| `data/seed_data_elevator.cypher` | 电梯场景种子数据 |
| `tests/test_scenarios_elevator.yaml` | 电梯场景 ~48 条测试用例 |

### Phase 1 修改

| 文件 | 改动 |
|---|---|
| `src/kgqa/config.py` | schema_file / seed_file / evaluation_file 支持环境变量 |
| `src/kgqa/query.py` | DomainRegistry.refrigerants → 泛化为 schema 驱动的枚举字段 |

### Phase 2 新建

| 文件 | 说明 |
|---|---|
| `data/schema_property.yaml` | 物业经营场景 schema |
| `data/seed_data_property.cypher` | 物业经营场景种子数据 |
| `tests/test_scenarios_property.yaml` | 物业经营场景 ~48 条测试用例 |

### Phase 2 修改

| 文件 | 改动 |
|---|---|
| `src/kgqa/query.py` | DomainRegistry 完全泛化 |
| `src/kgqa/schema.py` | focus keywords 从 schema description 自动生成 |
| `src/kgqa/tools.py` | list_domain_values 返回结构泛化、_infer_intent 移除或泛化 |
