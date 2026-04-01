# KGQA Agent 架构与执行机制说明

> 基于 Phase 1 多场景泛化改造后的代码状态（commit `66e7409`）

---

## 1. 系统总览

KGQA（Knowledge Graph Question Answering）是一个基于知识图谱的中文问答系统。用户以自然语言提问，系统通过 Agent 循环调用工具链，将问题翻译为 Cypher 查询，在 Neo4j 图数据库中执行，并将结果格式化后返回自然语言回答。

```
┌──────────────────────────────────────────────────────────────────┐
│                        Frontend (React)                         │
│                     POST /chat (SSE stream)                     │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                      FastAPI Server (api.py)                     │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ /health    │  │ /scenarios   │  │ POST /chat               │ │
│  │ /llm/status│  │ /schema      │  │  → resolve scenario      │ │
│  │ /seed/load │  │ /examples    │  │  → get_kgqa_agent()      │ │
│  │            │  │              │  │  → agent.stream_chat()   │ │
│  └────────────┘  └──────────────┘  └────────────┬─────────────┘ │
└─────────────────────────────────────────────────┼───────────────┘
                                                  │
                           ┌──────────────────────▼───────────────┐
                           │          KGQAAgent (agent.py)        │
                           │                                      │
                           │  ┌──────────────────────────────┐    │
                           │  │  Agent Loop (最多 5 步)       │    │
                           │  │  Step 1→5: _decide_next_action│   │
                           │  │  → 选择工具 → 执行 → 观察     │    │
                           │  └──────────────┬───────────────┘    │
                           │                 │                    │
                           │  ┌──────────────▼───────────────┐    │
                           │  │     KGQAToolbox (tools.py)    │   │
                           │  │                               │   │
                           │  │  get_schema_context           │   │
                           │  │  list_domain_values           │   │
                           │  │  validate_cypher              │   │
                           │  │  execute_cypher               │   │
                           │  │  format_results               │   │
                           │  └───┬──────────┬──────────┬─────┘   │
                           └──────┼──────────┼──────────┼─────────┘
                                  │          │          │
                    ┌─────────────▼─┐  ┌─────▼────┐  ┌─▼────────────┐
                    │ SchemaRegistry│  │ LLMClient│  │ Neo4jExecutor │
                    │  (schema.py)  │  │ (llm.py) │  │  (query.py)   │
                    └───────────────┘  └──────────┘  └───────────────┘
                           │                │                │
                    ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
                    │ schema.yaml │  │ OpenAI API  │  │   Neo4j DB  │
                    │ (YAML 文件)  │  │ (LLM 服务)  │  │  (图数据库)  │
                    └─────────────┘  └─────────────┘  └─────────────┘
```

---

## 2. 核心模块职责

| 模块 | 文件 | 职责 |
|---|---|---|
| **Settings** | `config.py` | 环境变量、数据库连接、LLM 配置、文件路径 |
| **Scenario** | `scenario.py` | 场景定义与注册（HVAC / Elevator），运行时切换 |
| **KGQAAgent** | `agent.py` | Agent 主循环：推理 → 工具调用 → 观察 → 回答 |
| **KGQAToolbox** | `tools.py` | 5 个工具的实现与统一调度接口 |
| **SchemaRegistry** | `schema.py` | YAML Schema 加载、渲染、focus 推断 |
| **DomainRegistry** | `query.py` | 从 Neo4j 动态加载各实体的 filterable field 枚举值 |
| **Neo4jExecutor** | `query.py` | Neo4j 驱动封装、Cypher 执行、结果归一化 |
| **CypherSafetyValidator** | `query.py` | Cypher 安全校验（只读、单语句、禁止写操作） |
| **LLMClient** | `llm.py` | OpenAI 兼容 API 调用封装（含 JSON 提取） |
| **ResultSerializer** | `serializer.py` | 查询结果 → markdown/表格/列表格式化 |
| **AnswerGenerator** | `generator.py` | 结构化结果 + LLM → 最终自然语言回答 |
| **Session** | `session.py` | 内存会话存储（消息历史 + agent 状态） |
| **Models** | `models.py` | Pydantic 数据模型（ChatRequest、IntentType 等） |

---

## 3. Agent 初始化流程

Agent 实例在首次请求时按 `(neo4j 配置, llm 配置, scenario)` 缓存，同配置复用同一实例。

```
get_kgqa_agent(settings, scenario)
│
├── 1. build_scenario_settings()
│      将 scenario 的 dataset_name / schema_file / seed_file 覆盖到 settings
│
├── 2. 计算 cache key (8-tuple)
│      (neo4j_uri, neo4j_username, neo4j_password,
│       llm_base_url, llm_api_key, llm_model,
│       dataset_name, schema_file)
│
├── 3. 命中缓存 → 直接返回
│
└── 4. 未命中 → new KGQAAgent(settings, scenario)
       │
       ├── LLMClient(settings)            ← 构建 LLM HTTP 客户端
       │
       ├── DomainRegistry(settings)       ← 解析 schema.yaml
       │   └── .load()                    ← 遍历 entities × filterable_fields
       │       遍历 schema 中每个 entity 的每个 filterable_field，
       │       执行 MATCH (n:{Entity}) RETURN DISTINCT n.{field}
       │       构建 {Entity: {field: [values]}} 字典
       │
       ├── SchemaRegistry(settings, domain)
       │   └── _build_focus_keywords()    ← 构建 entity → keywords 映射
       │       数据源：schema description + filterable_fields
       │                + 硬编码中文关键词 + domain 枚举值
       │
       └── KGQAToolbox(settings, schema, domain, llm_client)
           包装 5 个工具方法 + answer_generator
```

---

## 4. Agent 主循环（核心执行逻辑）

`stream_chat()` 是 Agent 的主入口，接收 `ChatRequest`，以 SSE 事件流返回结果。

### 4.1 流程图

```
stream_chat(request)
│
├── 提取 thread_id, run_id
├── 深拷贝 messages / state
├── upsert_session(status="running")
├── 提取用户最新问题 question
│
├── yield RUN_STARTED
│
├── ┌─── Agent Loop (step 1 → 5) ─────────────────────────────────┐
│   │                                                              │
│   │  yield STEP_STARTED                                          │
│   │      │                                                       │
│   │      ▼                                                       │
│   │  _decide_next_action(question, messages, observations)       │
│   │      │                                                       │
│   │      │  构造 prompt 包含：                                     │
│   │      │    - 当前问题                                          │
│   │      │    - 最近 10 条会话消息                                  │
│   │      │    - 最近 6 条工具观察                                   │
│   │      │    - 5 个可用工具定义                                    │
│   │      │                                                       │
│   │      │  LLM 返回 JSON:                                        │
│   │      │    {thought, action, tool_name, tool_args,             │
│   │      │     final_answer, auto_finish_after_format}            │
│   │      │                                                       │
│   │      ├── action == "finish" ─→ 记录 final_answer, break      │
│   │      │                                                       │
│   │      ├── tool_name 为空 ─→ fallback answer, break            │
│   │      │                                                       │
│   │      └── action == "call_tool"                               │
│   │              │                                               │
│   │              ▼                                               │
│   │          _run_tool(tool_name, tool_args)                     │
│   │              │                                               │
│   │              ├── 构造 assistant tool-call message             │
│   │              ├── toolbox.invoke(tool_name, args)             │
│   │              ├── 构造 tool result message                     │
│   │              ├── 更新 state.toolHistory                       │
│   │              ├── 缓冲 SSE events (TOOL_CALL_*, STATE_SNAPSHOT)│
│   │              └── 如果是 format_results → 额外缓冲 CUSTOM event│
│   │              │                                               │
│   │          drain_buffered_events → yield 所有缓冲事件           │
│   │              │                                               │
│   │          记录 observation {tool_name, tool_args, tool_result} │
│   │              │                                               │
│   │          如果是 format_results 且 auto_finish → break         │
│   │              │                                               │
│   │  yield STEP_FINISHED                                         │
│   │                                                              │
│   └──────────────────────────────────────────────────────────────┘
│
├── 后处理
│   ├── 如果没有 formatted_result → 从最后一次 execute_cypher 的
│   │   rows 自动调用 format_results
│   │
│   ├── 如果有 formatted_result → compose_answer(question, result)
│   │   └── AnswerGenerator.compose_with_llm() → 自然语言回答
│   │
│   └── 如果都没有 → "图谱中未找到相关信息。"
│
├── 追加 assistant message 到 messages
├── yield TEXT_MESSAGE_START / CONTENT / END
├── upsert_session(status="completed")
└── yield RUN_FINISHED
```

### 4.2 典型执行路径示例

以问题 **"乘客电梯有哪些型号？"** 为例：

```
Step 1: LLM 决策 → call_tool: get_schema_context("乘客电梯有哪些型号？")
        → 返回 Schema 上下文（实体、关系、典型路径）

Step 2: LLM 决策 → call_tool: list_domain_values("Category")
        → 返回 Category.name 枚举值 ["乘客电梯", "货梯", ...]

Step 3: LLM 决策 → call_tool: validate_cypher(
            "MATCH (c:Category {name:'乘客电梯'})<-[:BELONGS_TO]-(m:Model)
             RETURN m.name AS 型号, m.brand AS 品牌")
        → {valid: true}

Step 4: LLM 决策 → call_tool: execute_cypher(同上 Cypher)
        → {row_count: 8, rows: [{型号: "GeN2-MR", 品牌: "奥的斯"}, ...]}

Step 5: LLM 决策 → call_tool: format_results(question, rows)
        → {renderer: "table", markdown: "| 型号 | 品牌 | ...", ...}
        → auto_finish_after_format=true → break

后处理: compose_answer() → LLM 生成自然语言回答
```

---

## 5. 工具链详解

### 5.1 工具概览

```
┌─────────────────────────────────────────────────────────────────┐
│                       KGQAToolbox                               │
│                                                                 │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │ get_schema_     │  │ list_domain_     │  │ validate_     │  │
│  │ context         │  │ values           │  │ cypher        │  │
│  │                 │  │                  │  │               │  │
│  │ 读取图谱 schema │  │ 查看枚举值       │  │ 安全校验      │  │
│  │ + 关系路径      │  │ (Entity.field)   │  │ (只读检查)    │  │
│  └────────┬────────┘  └────────┬─────────┘  └───────┬───────┘  │
│           │                    │                     │          │
│  ┌────────▼────────┐  ┌───────▼──────────────────────▼───────┐ │
│  │ SchemaRegistry  │  │           DomainRegistry             │ │
│  └─────────────────┘  │  {Entity: {field: [distinct values]}}│ │
│                       └──────────────────────────────────────┘ │
│                                                                 │
│  ┌─────────────────┐  ┌──────────────────┐                     │
│  │ execute_cypher  │  │ format_results   │                     │
│  │                 │  │                  │                     │
│  │ 执行只读 Cypher │  │ 结果序列化为      │                     │
│  │ 返回行数据      │  │ markdown/table   │                     │
│  └────────┬────────┘  └────────┬─────────┘                     │
│           │                    │                               │
│  ┌────────▼────────┐  ┌───────▼──────────┐                     │
│  │ Neo4jExecutor   │  │ ResultSerializer │                     │
│  └─────────────────┘  │ + AnswerGenerator│                     │
│                       └──────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 各工具详细说明

#### `get_schema_context(question: str)`

**作用**：将 YAML schema 渲染为 LLM 可理解的文本上下文。

**关键机制 — Focus 推断**：
- 根据问题中的关键词匹配，只返回相关实体的 schema（减少 token 消耗）
- 关键词来源：schema description、filterable_fields、硬编码中文词、domain 枚举值

```
输入: "乘客电梯有哪些型号？"
        │
        ▼
  _infer_focus() 匹配关键词:
    "电梯" → Category (via description "电梯类型")
    "型号" → Model (via 硬编码关键词 "型号")
        │
        ▼
  render_schema_context() 只输出 Category + Model 的 schema
  + 相关 relationships (BELONGS_TO)
  + 典型路径 (Category -> Model)
```

**输出结构**：
```
{
  "schema_context": "## 图谱 Schema\n- Model: id: string, name: string, ...\n...",
  "summary": {"dataset": "elevator_poc", "entity_count": 5, ...}
}
```

#### `list_domain_values(kind: str | null)`

**作用**：查看图谱中实际存在的枚举值，帮助 LLM 生成精确的 Cypher 过滤条件。

**调用方式**：
- `kind=null` → 返回所有实体的所有 filterable field 值
- `kind="Customer"` → 返回指定实体的所有 field 值
- `kind="Model.brand"` → 返回指定实体的指定 field 值
- `kind="brands"` → 别名解析为 `Model.brand`

**输出结构**：
```json
{
  "Customer": {"name": ["绿城", "中海", ...], "industry": [...], ...},
  "Model": {"brand": ["奥的斯", "三菱", ...], "drive_type": [...], ...},
  ...
}
```

#### `validate_cypher(cypher: str)`

**作用**：在执行前校验 Cypher 语句的安全性。

**校验规则**：
1. 单语句检查 — 不允许分号分隔的多语句
2. 起始关键词 — 必须以 `MATCH` / `WITH` / `UNWIND` 开头
3. 禁止写操作 — `CREATE` / `MERGE` / `DELETE` / `SET` / `REMOVE` / `DROP` / `LOAD CSV`
4. 属性映射比较检测 — 拒绝 `{field: ">= 5"}` 形式的错误写法

#### `execute_cypher(cypher: str)`

**作用**：执行只读 Cypher 查询，返回结果行。

**关键特性**：
- 可选 `EXPLAIN` 预检（由 `NEO4J_VALIDATE_WITH_EXPLAIN` 控制）
- 结果归一化：Neo4j Node/Relationship/Path → JSON dict
- 日期/时间类型 → ISO 格式字符串

#### `format_results(question: str, rows: list)`

**作用**：将原始查询行转换为结构化展示格式。

**格式决策逻辑**：
```
rows 为空？
├── 是 → format="empty"
└── 否
    ├── 单行且字段 > 2 且非比较类问题？ → format="key_value" (指标卡片)
    ├── 问题含比较/聚合关键词 或 intent 为 AGGREGATION？ → format="markdown_table"
    ├── 行中存在 list 类型值？ → format="numbered_list"
    └── 默认 → format="table"
```

---

## 6. LLM 交互设计

### 6.1 决策生成（Agent 推理）

每一步循环中，`_decide_next_action()` 向 LLM 发送结构化 prompt：

```
┌── System Prompt ────────────────────────────────────────────────┐
│ 你是企业知识图谱问答 Agent。                                     │
│ 原则：不确定时先读 schema/domain；                                │
│ 执行 Cypher 前先 validate；拿到 rows 后调 format_results。       │
│ 只输出 JSON，不要解释。                                          │
└─────────────────────────────────────────────────────────────────┘

┌── User Prompt ──────────────────────────────────────────────────┐
│ 当前问题：{question}                                            │
│ 会话消息：[最近 10 条 role+content]                               │
│ 工具观察：[最近 6 条 {tool_name, tool_args, tool_result}]         │
│ 是否已有格式化结果：true/false                                    │
│ 可用工具：[5 个工具的 name + description + args_schema]           │
│                                                                 │
│ 请输出 JSON：                                                    │
│ {"thought": str, "action": "call_tool|finish",                  │
│  "tool_name": str|null, "tool_args": object,                    │
│  "final_answer": str|null, "auto_finish_after_format": bool}    │
└─────────────────────────────────────────────────────────────────┘
```

**LLM 调用失败时的 fallback 策略**：
- 无观察记录 → 默认调用 `get_schema_context`
- 已有格式化结果 → 直接 `finish`
- 其他 → 调用 `list_domain_values`

### 6.2 最终回答生成

`AnswerGenerator.compose_with_llm()` 将结构化结果翻译为自然语言：

```
┌── System Prompt ────────────────────────────────────────────────┐
│ 你是知识图谱问答助手，只能根据提供的数据回答。                      │
└─────────────────────────────────────────────────────────────────┘

┌── User Prompt ──────────────────────────────────────────────────┐
│ 请基于以下结构化结果，用中文给出准确、简洁、不可编造的回答。          │
│ 问题：{question}                                                │
│ 意图：SINGLE_DOMAIN / CROSS_DOMAIN / AGGREGATION / MULTI_STEP  │
│ 结构化结果：{markdown 表格/列表}                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 数据流完整路径

以一次完整的用户问答为例，数据从前端到后端再返回的全链路：

```
              Frontend                    API                      Agent
              ───────                    ───                      ─────
    POST /chat ──────────────────►  chat(request)
    {threadId, messages,               │
     scenarioId, state}                │
                                       ├─► resolve scenario
                                       ├─► get_kgqa_agent()
                                       │     (缓存或新建)
                                       │
                                       └─► agent.stream_chat()
                                              │
              ◄── RUN_STARTED ──────────────── ┤
                                               │
              ◄── STEP_STARTED ─────────────── ├── Step 1
                                               │     │
                                               │     ├── LLM: _decide_next_action()
                                               │     │     → {tool_name: "get_schema_context"}
                                               │     │
              ◄── TOOL_CALL_START ──────────── │     ├── toolbox.invoke()
              ◄── TOOL_CALL_ARGS ───────────── │     │     → SchemaRegistry.render_schema_context()
              ◄── TOOL_CALL_END ────────────── │     │
              ◄── TOOL_CALL_RESULT ─────────── │     │
              ◄── STATE_SNAPSHOT ───────────── │     │
                                               │     │
              ◄── STEP_FINISHED ────────────── │     │
                                               │     │
              ◄── STEP_STARTED ─────────────── ├── Step 2
                                               │     │
                         ...                   │    ...  (validate → execute → format)
                                               │
              ◄── STEP_FINISHED ────────────── │
                                               │
                                               ├── compose_answer()
                                               │     └── LLM 生成自然语言回答
                                               │
              ◄── TEXT_MESSAGE_START ────────── │
              ◄── TEXT_MESSAGE_CONTENT (×N) ─── │  (每 80 字符一个 chunk)
              ◄── TEXT_MESSAGE_END ──────────── │
                                               │
              ◄── RUN_FINISHED ─────────────── │
```

---

## 8. 多场景机制

### 8.1 场景注册

`scenario.py` 中通过 `_SCENARIOS` 字典注册所有场景：

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Scenario Registry                               │
│                                                                        │
│  ┌──────────────────────────┐    ┌──────────────────────────────────┐  │
│  │  hvac                    │    │  elevator                        │  │
│  │  ├── label: HVAC 冷水机组 │    │  ├── label: 建筑行业 · 电梯设备   │  │
│  │  ├── dataset: kgqa_poc   │    │  ├── dataset: elevator_poc       │  │
│  │  ├── schema: schema.yaml │    │  ├── schema: schema_elevator.yaml│  │
│  │  ├── seed: seed_data.    │    │  ├── seed: seed_data_            │  │
│  │  │   cypher              │    │  │   elevator.cypher              │  │
│  │  └── eval: test_         │    │  └── eval: test_scenarios_       │  │
│  │      scenarios.yaml      │    │      elevator.yaml               │  │
│  └──────────────────────────┘    └──────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### 8.2 场景切换路径

```
前端选择 scenarioId="elevator"
        │
        ▼
POST /chat {scenarioId: "elevator"}
        │
        ▼
_resolve_scenario("elevator")
  → ScenarioDefinition(dataset_name="elevator_poc",
                        schema_file="data/schema_elevator.yaml", ...)
        │
        ▼
get_kgqa_agent(settings, scenario)
  → build_scenario_settings()  ← 用 scenario 字段覆盖 settings
  → 计算 cache key            ← dataset_name + schema_file 参与
  → 命中/新建 agent           ← 不同场景 → 不同 agent 实例
        │
        ▼
KGQAAgent.__init__(elevator_settings)
  → DomainRegistry.load()     ← 读 schema_elevator.yaml
  → 从 Neo4j 加载电梯数据的枚举值
  → SchemaRegistry 构建电梯相关 focus keywords
```

### 8.3 场景隔离要点

| 维度 | 隔离方式 |
|---|---|
| Neo4j 数据 | 通过 `n.dataset = '{dataset_name}'` 过滤，同一数据库内隔离 |
| Agent 实例 | 按 cache key 区分，不同场景各自缓存 |
| Schema/Domain | 各场景独立加载各自的 schema.yaml |
| 会话 | Session 绑定 scenario_id，切换时拒绝（HTTP 409） |
| 种子数据 | `MATCH (n) WHERE n.dataset=$dataset DETACH DELETE n` 清理后重载 |

---

## 9. 缓存体系

系统使用多层缓存减少重复初始化开销：

```
┌─────────────────────────────────────────────────────────────┐
│                        Cache Layers                         │
│                                                             │
│  Layer 1: Agent Cache (_AGENT_CACHE)                        │
│  ├── Key: (neo4j×3, llm×3, dataset, schema_file)           │
│  ├── Value: KGQAAgent instance                              │
│  ├── Lifetime: process-scoped, cleared on shutdown          │
│  └── Thread-safe: Lock-based double-check                   │
│                                                             │
│  Layer 2: Neo4j Driver Cache (_DRIVER_CACHE)                │
│  ├── Key: (neo4j_uri, username, password)                   │
│  ├── Value: neo4j.Driver                                    │
│  └── Lifetime: process-scoped                               │
│                                                             │
│  Layer 3: HTTP Client Cache (_CLIENT_CACHE)                 │
│  ├── Key: (llm_base_url, llm_api_key)                      │
│  ├── Value: httpx.Client                                    │
│  └── Lifetime: process-scoped                               │
│                                                             │
│  Layer 4: LLM Status Cache (_LLM_STATUS_CACHE)              │
│  ├── TTL: 60 seconds                                        │
│  └── Purpose: 避免频繁调用 /llm/status                       │
│                                                             │
│  Layer 5: Session Store (_SESSION_STORE)                     │
│  ├── Key: session_id (UUID)                                 │
│  ├── Value: ChatSessionRecord                               │
│  ├── In-memory, no persistence                              │
│  └── Thread-safe: Lock-based                                │
└─────────────────────────────────────────────────────────────┘
```

---

## 10. SSE 事件协议

Agent 通过 Server-Sent Events 向前端推送实时状态：

```
事件时序:

RUN_STARTED ─────────────────────────────────────────────────►
│
├── STEP_STARTED ────────────────────────────────────────────►
│   ├── TOOL_CALL_START ─────────────────────────────────────►
│   ├── TOOL_CALL_ARGS ──────────────────────────────────────►
│   ├── TOOL_CALL_END ───────────────────────────────────────►
│   ├── TOOL_CALL_RESULT ────────────────────────────────────►
│   ├── STATE_SNAPSHOT ──────────────────────────────────────►
│   └── CUSTOM (kgqa_ui_payload, 仅 format_results 时) ─────►
├── STEP_FINISHED ───────────────────────────────────────────►
│
├── STEP_STARTED ... STEP_FINISHED  (重复 2-5 次)
│
├── TEXT_MESSAGE_START ──────────────────────────────────────►
├── TEXT_MESSAGE_CONTENT (×N, 每 80 字符) ──────────────────►
├── TEXT_MESSAGE_END ────────────────────────────────────────►
│
RUN_FINISHED ────────────────────────────────────────────────►
```

**事件缓冲机制**：工具执行期间产生的事件暂存于 `state["_event_buffer"]`，执行完毕后由 `drain_buffered_events()` 统一 yield。这样确保工具执行是原子操作，所有相关事件按序发出。

---

## 11. DomainRegistry 泛化设计（Phase 1 成果）

Phase 1 的核心改造是将 DomainRegistry 从硬编码 7 个属性查询改为 **schema 驱动的动态加载**：

```
Phase 1 之前（硬编码）              Phase 1 之后（schema 驱动）
─────────────────────              ────────────────────────

self._customers = ...              for entity in schema["entities"]:
self._brands = ...                     for field in entity["filterable_fields"]:
self._cities = ...                         if _should_load_field(field):
self._project_types = ...                      MATCH (n:{entity})
self._project_statuses = ...                   RETURN DISTINCT n.{field}
self._categories = ...
self._refrigerants = ...           结果结构:
                                   {
结果结构:                              "Customer": {"name": [...], "industry": [...]},
{                                      "Project":  {"type": [...], "city": [...]},
  "customers": [...],                  "Model":    {"brand": [...], "drive_type": [...]},
  "brands": [...],                     ...
  "refrigerants": [...],           }
  ...
}
```

**过滤逻辑** (`_should_load_field`)：排除 `id`、`dataset`、以 `_id` 结尾的外键字段，只加载真正有业务含义的枚举字段。

**兼容性**：保留了 `customers`、`brands` 等 property 快捷方式和 `_resolve_alias` 别名映射，但这些本质上已委托给通用的 `get_values(entity, field)` 方法。

---

## 12. 当前架构中的硬编码依赖（Phase 2 待解决）

| 位置 | 硬编码内容 | 影响范围 |
|---|---|---|
| `schema.py:96-100` | `Customer→客户` / `Model→设备,型号,品牌` 等 5 组 | focus 推断对异构场景失效 |
| `schema.py:104-113` | `self._domain.customers` / `.brands` 等属性名 | 依赖 HVAC/Elevator 的实体名 |
| `query.py:224-232` | `_resolve_alias` 中的 `"refrigerants"→("Model","refrigerant")` | 仅适用于 HVAC 场景 |
| `tools.py:133-141` | `_infer_intent` 中的中文关键词 `"客户"/"项目"/"品牌"` | 物业经营场景的实体名完全不同 |

---

## 13. 目录结构全景

```
kg-qa-poc/
├── data/
│   ├── schema.yaml                  # HVAC 场景 schema 定义
│   ├── schema_elevator.yaml         # 电梯场景 schema 定义
│   ├── seed_data.cypher             # HVAC 种子数据 (~136KB)
│   └── seed_data_elevator.cypher    # 电梯种子数据 (~125KB)
│
├── src/kgqa/
│   ├── __init__.py
│   ├── config.py          # 42 LOC   Settings (env vars, paths)
│   ├── scenario.py        # 73 LOC   场景定义与注册
│   ├── models.py          # 94 LOC   Pydantic 数据模型
│   ├── llm.py             # 96 LOC   LLM API 客户端
│   ├── query.py           # 293 LOC  Neo4j 执行器 + Domain + 安全校验
│   ├── schema.py          # 127 LOC  Schema 注册与 focus 推断
│   ├── tools.py           # 142 LOC  5 工具实现 + 意图推断
│   ├── serializer.py      # 60 LOC   结果格式化
│   ├── generator.py       # 34 LOC   LLM 回答生成
│   ├── session.py         # 131 LOC  会话存储
│   ├── agent.py           # 411 LOC  Agent 主循环 ← 核心
│   ├── api.py             # 215 LOC  FastAPI 路由
│   └── cli.py             # 38 LOC   CLI 入口
│
├── eval/
│   └── run_eval.py                  # 评估框架 + HTML 报告
│
├── tests/
│   ├── test_scenarios.yaml          # HVAC 评估用例
│   ├── test_scenarios_elevator.yaml # 电梯评估用例
│   └── test_*.py                    # 单元测试
│
├── frontend/                        # Vite + React 前端
├── docker-compose.yml               # Neo4j 服务
└── pyproject.toml                   # 项目配置
```
