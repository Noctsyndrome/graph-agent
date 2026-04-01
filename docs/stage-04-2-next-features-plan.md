# Stage 04-2: 图谱可视化 · 会话持久化 · Prompt 优化

## 背景

Agent 架构迁移（Stage 04-1）已完成。当前 Agent 以 ReAct 循环（最多 5 步）驱动 5 个工具完成问答。本阶段针对三个方向进行增强。

---

## 实施顺序

```
Feature 2: 会话持久化（后端，无前端改动）
    │
    ├── 可并行 ──┐
    │            ▼
    │   Feature 3: Prompt 优化（后端，需 eval 验证）
    │
    ▼
Feature 1: 图谱可视化（全栈，代码量最大）
```

Feature 2 和 Feature 3 互相独立，可并行。Feature 1 独立但代码最多，建议最后做。

---

## Feature 1: 图谱可视化

### 目标

在前端 Inspector Drawer 中新增 "图谱视图" Tab，展示 Schema 级别的实体关系图，并在查询完成后高亮涉及的实体类型。

### 技术选型

| 选项 | 优劣 |
|---|---|
| `react-force-graph-2d` | ✅ 轻量 (~40KB gzip)，简单 `{nodes, links}` 数据结构，TypeScript 支持 |
| `@react-sigma/core` | 适合大图，本场景（5 实体 6 关系）过重 |
| `reactflow` | 流程图导向，需要手动布局 |

**选择：`react-force-graph-2d`**

### 两种展示模式

1. **Schema Overview（静态）**：实体类型作为节点，关系类型作为边
2. **Query Highlight（动态）**：查询完成后，高亮最近 Cypher 涉及的实体类型节点

### 后端改动

#### 1. `src/kgqa/schema.py` — 新增 `graph_data()`

将 `schema.yaml` 转换为前端图形库所需的 node-link 结构：

```python
def graph_data(self) -> dict[str, Any]:
    nodes = []
    for entity in self._schema.get("entities", []):
        nodes.append({
            "id": entity["name"],
            "label": entity.get("description", entity["name"]),
            "properties": list(entity.get("properties", {}).keys()),
        })
    links = []
    for rel in self._schema.get("relationships", []):
        links.append({
            "source": rel["from"],
            "target": rel["to"],
            "label": rel["name"],
            "cardinality": rel.get("cardinality", ""),
        })
    return {"nodes": nodes, "links": links}
```

产出数据示例（5 个实体节点、6 条关系边）：

```json
{
  "nodes": [
    {"id": "Customer", "label": "客户信息", "properties": ["id", "name", "industry", ...]},
    {"id": "Project", "label": "项目信息", "properties": [...]},
    ...
  ],
  "links": [
    {"source": "Customer", "target": "Project", "label": "OWNS_PROJECT", "cardinality": "1:N"},
    {"source": "Project", "target": "Installation", "label": "HAS_INSTALLATION", "cardinality": "1:N"},
    ...
  ]
}
```

#### 2. `src/kgqa/api.py` — 新增端点

```python
@app.get("/schema/graph")
def schema_graph() -> dict[str, object]:
    return SchemaRegistry(settings, domain=get_kgqa_agent(settings).domain).graph_data()
```

### 前端改动

#### 3. 安装依赖

```bash
cd frontend && npm install react-force-graph-2d
```

`@radix-ui/react-tabs` 已在 package.json 中，无需额外安装。

#### 4. `frontend/src/types.ts` — 新增类型

```typescript
export interface GraphNode {
  id: string;
  label: string;
  properties: string[];
}

export interface GraphLink {
  source: string;
  target: string;
  label: string;
  cardinality?: string;
}

export interface SchemaGraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}
```

#### 5. `frontend/src/api.ts` — 新增 `fetchSchemaGraph()`

```typescript
export function fetchSchemaGraph(): Promise<SchemaGraphData> {
  return readJson<SchemaGraphData>("/schema/graph");
}
```

#### 6. `frontend/src/components/graph-view.tsx` — 新建组件

Props：
- `schemaGraph: SchemaGraphData | null`
- `activeEntities: string[]` — 最近查询涉及的实体类型

实现要点：
- 使用 `ForceGraph2D` 组件
- `nodeCanvasObject` 回调绘制圆形 + 实体名标签
- `activeEntities` 中的节点着重色（蓝色），其他节点浅灰色
- `linkDirectionalArrowLength` 显示方向，`linkLabel` 显示关系名
- 容器尺寸从父级 drawer panel 继承

#### 7. `frontend/src/App.tsx` — Drawer 改为 Tab 布局

将当前 Drawer 的单一内容区改为 `Tabs.Root` / `Tabs.List` / `Tabs.Content`：

- Tab 1: **工具详情**（现有 `ResultRenderer` / `ToolDetailRenderer`）
- Tab 2: **图谱视图**（新 `GraphView` 组件）

新增 state：
```typescript
const [schemaGraph, setSchemaGraph] = useState<SchemaGraphData | null>(null);
```

在 `refreshMeta()` 中并行 fetch `fetchSchemaGraph()`。

从 `threadState.toolHistory` 提取 `activeEntities`：解析最近 `execute_cypher` 调用的 Cypher 文本，检查其中包含的 schema 实体名。

#### 8. `frontend/src/index.css` — 图谱容器样式

```css
.graph-container {
  width: 100%;
  height: 100%;
  min-height: 300px;
}
```

### 文件清单

| 操作 | 文件 |
|---|---|
| 修改 | `src/kgqa/schema.py` — 新增 `graph_data()` |
| 修改 | `src/kgqa/api.py` — 新增 `/schema/graph` |
| 新建 | `frontend/src/components/graph-view.tsx` |
| 修改 | `frontend/src/types.ts` — 新增图谱类型 |
| 修改 | `frontend/src/api.ts` — 新增 `fetchSchemaGraph()` |
| 修改 | `frontend/src/App.tsx` — Tab 布局 + 图谱 state |
| 修改 | `frontend/src/index.css` — 图谱容器样式 |

### 验证

1. `GET /schema/graph` 返回 5 nodes + 6 links
2. Drawer 打开后可切换 "图谱视图" Tab，显示力导向布局
3. 执行查询后，涉及的实体类型节点高亮
4. 现有聊天、工具芯片、工具详情功能不受影响

---

## Feature 2: 会话持久化

### 目标

将会话存储从内存 dict 切换到 SQLite，重启后会话不丢失。

### 技术选型

- **SQLite**（Python 标准库 `sqlite3`）：零依赖，单文件，PoC 级别最佳选择
- JSON 文本列存储 messages 和 state
- WAL 模式 + `check_same_thread=False` 兼容 FastAPI 多线程

### 实现步骤

#### 1. `src/kgqa/config.py` — 新增配置

```python
session_db_path: Path = ROOT / "data" / "sessions.db"
```

#### 2. `src/kgqa/session.py` — 重写为 SQLite

保持完全相同的公共 API：
- `upsert_session(session_id, messages, state, status) -> ChatSessionRecord`
- `get_session(session_id) -> ChatSessionRecord | None`
- `list_sessions() -> list[ChatSessionSummary]`
- `get_session_payload(session_id) -> ChatSessionPayload | None`
- `clear_sessions() -> None`
- 新增 `close_session_db() -> None`

内部结构：

```python
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kgqa.models import ChatSessionPayload, ChatSessionRecord, ChatSessionSummary

_DB_CONN: sqlite3.Connection | None = None
_DB_LOCK = threading.Lock()


def _get_db() -> sqlite3.Connection:
    global _DB_CONN
    if _DB_CONN is not None:
        return _DB_CONN
    with _DB_LOCK:
        if _DB_CONN is not None:
            return _DB_CONN
        from kgqa.config import get_settings
        db_path = get_settings().session_db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(conn)
        _DB_CONN = conn
        return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            messages   TEXT NOT NULL DEFAULT '[]',
            state      TEXT NOT NULL DEFAULT '{}',
            status     TEXT NOT NULL DEFAULT 'idle'
        )
    """)
    conn.commit()
```

`upsert_session` 使用 `INSERT OR REPLACE`（或先 SELECT 再 INSERT/UPDATE 以实现部分更新语义）。

`list_sessions` 使用 `ORDER BY updated_at DESC`，message_count 通过 Python 解析 JSON 计算。

#### 3. `src/kgqa/api.py` — shutdown 改动

```python
@app.on_event("shutdown")
def shutdown_event() -> None:
    close_session_db()          # 替代 clear_sessions()
    close_all_kgqa_agents()
    close_all_llm_clients()
    close_all_neo4j_drivers()
```

`seed_load` 端点保留 `clear_sessions()` 调用（重新导入数据时应清理旧会话）。

#### 4. `.gitignore` — 排除数据库文件

```
data/sessions.db
data/sessions.db-wal
data/sessions.db-shm
```

### 文件清单

| 操作 | 文件 |
|---|---|
| 修改 | `src/kgqa/config.py` — 新增 `session_db_path` |
| 重写 | `src/kgqa/session.py` — SQLite 后端 |
| 修改 | `src/kgqa/api.py` — shutdown 调用 `close_session_db` |
| 修改 | `.gitignore` — 排除 sessions.db |

### 验证

1. 现有测试 `test_chat_api.py` 通过（API 接口不变）
2. 手动验证：启动 → 提问 → 停止 → 重启 → `GET /chat/sessions` 返回之前的会话
3. `POST /seed/load` 后会话被清空
4. `data/sessions.db` 被创建但不被 git 追踪

---

## Feature 3: Agent Prompt 优化

### 目标

减少 LLM 决策调用次数，将典型问题的调用链从 5-6 次降到 2-3 次。

### 当前调用链（6 次 LLM 调用）

以 "开利有哪些型号的冷水机组" 为例：

| # | 类型 | 动作 |
|---|---|---|
| 1 | LLM 决策 | → `get_schema_context` |
| 2 | LLM 决策 | → `list_domain_values` |
| 3 | LLM 决策 | 生成 Cypher → `validate_cypher` |
| 4 | LLM 决策 | → `execute_cypher` |
| 5 | LLM 决策 | → `format_results` |
| 6 | LLM 组合 | 生成最终回答 |

### 优化后调用链（2-3 次 LLM 调用）

| # | 类型 | 动作 |
|---|---|---|
| 1 | LLM 决策 | 生成 Cypher → `execute_cypher`（内置校验） |
| 2 | 自动 | `format_results` 自动链式调用 |
| 3 | LLM 组合 | 生成最终回答 |

### 优化 A：预注入 Schema + Domain 上下文

**文件：`src/kgqa/agent.py`**

**关键发现：** `DomainRegistry.prompt_summary()`（query.py:175）已经实现了完美的领域枚举值摘要方法，但从未被调用。`SchemaRegistry.render_schema_context()` 是本地操作，无需工具调用。

在 `KGQAAgent.__init__` 中预计算：

```python
self._base_schema_context = self.schema.render_schema_context(question="")
self._base_domain_summary = self.domain.prompt_summary()
```

在 `_decide_next_action` 中替换 system prompt：

```python
system_prompt = (
    "你是企业知识图谱问答 Agent。\n\n"
    f"{self._base_schema_context}\n\n"
    f"{self._base_domain_summary}\n\n"
    "## 工作流程\n"
    "你已拥有完整的 schema 和领域枚举值，不需要调用 get_schema_context 或 list_domain_values。\n"
    "execute_cypher 内置安全校验，不需要先调用 validate_cypher。\n"
    "标准流程：生成 Cypher → execute_cypher → format_results → finish。\n"
    "生成 Cypher 时，所有节点必须包含 {dataset: 'kgqa_poc'} 过滤条件。\n"
    "用户提到的实体名要与上方枚举值精确匹配。\n"
    "只有 execute_cypher 返回 error 时才需要修正 Cypher 重试。\n"
    "已有格式化结果时直接 finish。\n"
    "只输出 JSON，不要解释。"
)
```

同时精简 tool_specs，仅向决策 prompt 暴露核心工具：

```python
fast_tool_specs = [
    spec for spec in self.toolbox.tool_specs()
    if spec["name"] in ("execute_cypher", "format_results")
]
```

> 注：`get_schema_context` 和 `list_domain_values` 仍保留在 toolbox 中可被调用，但不主动提示 agent 使用。

### 优化 B：execute_cypher 内置校验

**文件：`src/kgqa/tools.py`**

修改 `execute_cypher` 自动先调用 `CypherSafetyValidator`：

```python
def execute_cypher(self, cypher: str) -> dict[str, Any]:
    try:
        self.validator.validate(cypher)
    except Exception as exc:
        return {"valid": False, "cypher": cypher, "error": str(exc), "row_count": 0, "rows": []}

    executor = Neo4jExecutor(self.settings)
    try:
        rows = executor.query(cypher)
    finally:
        executor.close()
    return {"row_count": len(rows), "rows": rows}
```

更新 tool_specs 描述：

```python
{
    "name": "execute_cypher",
    "description": "自动校验并执行只读 Cypher，返回查询结果行。校验失败时返回 error 字段。",
    "args_schema": {"cypher": "string"},
}
```

### 优化 C：execute_cypher 成功后自动链式调用 format_results

**文件：`src/kgqa/agent.py`**

在 `stream_chat` 循环中，`execute_cypher` 成功返回 rows 后，自动执行 `format_results` 并 break 循环：

```python
# 在 observations.append 之后：
if tool_name == "execute_cypher" and tool_result.get("rows") and not tool_result.get("error"):
    fmt_result, messages, state = self._run_tool(
        thread_id=thread_id,
        messages=messages,
        state=state,
        tool_name="format_results",
        tool_args={"question": question, "rows": tool_result["rows"]},
    )
    for event in self.drain_buffered_events(state):
        yield self._sse(event)
    formatted_result = fmt_result
    state["latestResult"] = formatted_result
    upsert_session(thread_id, messages=messages, state=self._public_state(state), status="running")
    break
```

### 优化 D：错误重试路径

当 `execute_cypher` 返回 error 时，不触发 auto-format，agent 继续循环获得第二次 LLM 决策机会来修正 Cypher。当前循环结构天然支持此行为。

### 文件清单

| 操作 | 文件 |
|---|---|
| 修改 | `src/kgqa/agent.py` — 预注入上下文、精简 tool_specs、auto-format |
| 修改 | `src/kgqa/tools.py` — execute_cypher 内置校验、更新描述 |

### 验证

1. **Eval 套件**：`python -m eval.run_eval` 48 条测试用例通过率 ≥ 当前基线
2. **步骤计数**：5 个代表性简单问题，SSE 流中 `STEP_STARTED` 事件从 4-5 个降至 1-2 个
3. **延迟**：简单问题总延迟减半
4. **错误恢复**：引发无效 Cypher 的问题，验证 agent 获得第二次修正机会
5. **复杂查询**：多跳查询（如替代方案）可能仍需 2-3 次决策，属于正常情况
6. **现有测试**：`test_agent_tools.py`、`test_chat_api.py` 通过
