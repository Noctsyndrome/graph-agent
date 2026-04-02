# Stage 04-6: 会话持久化（重规划）

## 背景

`stage-04-2` 中曾规划过一次“会话持久化”，但那份设计已经落后于当前代码状态：

- 当前系统已经完成 `stage-04-3` 多场景泛化，会话模型包含 `scenario_id / scenario_label / dataset_name`
- 当前 agent 已完成 `stage-04-4` / `stage-04-5` 的循环与 prompt 编排改造，会在一次问答中多次调用 `upsert_session(...)`
- 当前 API 已提供 `/chat/sessions` 与 `/chat/{session_id}/messages`，但底层仍是内存 dict，服务重启后会话全部丢失

在继续优化“基于对话上下文的追问效果”之前，需要先完成会话持久化。这样无论是前端手动对话，还是后续 agent 继续承接同一会话，都可以读取重启前已经保存的消息与状态，而不是依赖人工重复触发上下文。

---

## 目标

1. 将会话存储从内存切换为 SQLite，服务重启后会话仍可恢复。
2. 保持现有 API 形态不变，不引入前端协议改动。
3. 保持当前会话模型不变，完整持久化 `messages / state / status` 以及多场景元数据。
4. 保持当前 agent 的增量落盘语义不变：开始运行、pre-step、每次工具执行后、完成、失败都能更新同一会话。
5. 保持现有场景锁定语义不变：已有会话不可切换 `scenarioId`，`seed/load` 仍按场景清理旧会话。

---

## 非目标

- 不在本阶段引入向量记忆、跨会话召回、长期记忆摘要。
- 不在本阶段持久化原始 SSE 事件流或完整运行日志。
- 不在本阶段修改前端会话 UI。
- 不处理从旧内存 store 到 SQLite 的历史迁移。PoC 当前只要求“上线后持久化”，不要求导入此前进程内数据。

---

## 当前代码基线

### 1. 会话模型已经是多场景版本

当前 `ChatSessionRecord / ChatSessionSummary / ChatSessionPayload` 均包含以下字段：

- `session_id`
- `title`
- `scenario_id`
- `scenario_label`
- `dataset_name`
- `created_at`
- `updated_at`
- `messages`
- `state`
- `status`

因此新的存储层不能退回到旧版的简化 schema。

### 2. Agent 不是“只在结束时保存一次”

当前 `KGQAAgent.stream_chat()` 会在多个节点调用 `upsert_session(...)`：

- 进入运行态时，写入 `status="running"`
- schema pre-step 完成后再次写入
- 决策异常或预算异常时写入最新 state
- 每次工具执行后写入最新 messages / state
- 正常结束时写入 `status="completed"`
- 异常退出时写入 `status="failed"`

因此会话持久化设计必须支持高频、同 session 的部分更新，而不是只做一次性覆盖。

### 3. API 语义已经稳定

当前依赖 `session.py` 的接口包括：

- `upsert_session(...)`
- `get_session(session_id)`
- `list_sessions()`
- `get_session_payload(session_id)`
- `clear_sessions(scenario_id=None)`

本阶段应保持这些公开接口和返回模型不变，只替换内部存储实现，并新增 `close_session_db()` 用于优雅关闭 SQLite 连接。

---

## 设计原则

1. 兼容当前代码，不倒逼上层接口改动。
2. 优先低风险和可调试性，而不是过度抽象。
3. 单文件 SQLite 即可满足 PoC，需要零额外依赖。
4. 所有 JSON 序列化字段都以应用层显式编码/解码，不依赖 SQLite 特殊扩展。
5. 对并发请求保持线程安全，避免 FastAPI 多线程下连接误用。

---

## 存储方案

### 技术选型

- SQLite，使用 Python 标准库 `sqlite3`
- 会话主体存储在 `data/sessions.db`
- `messages` 与 `state` 以 JSON 文本列存储
- 打开 `WAL` 模式，提升读写并发兼容性
- 使用 `check_same_thread=False`，配合模块内锁控制连接访问

### 配置

在 `src/kgqa/config.py` 中新增：

```python
session_db_path: Path = ROOT / "data" / "sessions.db"
```

### 数据表结构

建议采用单表方案：

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    scenario_id    TEXT NOT NULL,
    scenario_label TEXT NOT NULL,
    dataset_name   TEXT NOT NULL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    messages_json  TEXT NOT NULL DEFAULT '[]',
    state_json     TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'idle'
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
ON sessions(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_scenario_id
ON sessions(scenario_id);
```

说明：

- 单表足够覆盖当前需求，避免过早拆分 message 表和 event 表
- `messages_json / state_json` 字段名显式体现其序列化形态，降低误解
- `scenario_id` 索引用于 `clear_sessions(scenario_id=...)`
- `updated_at` 索引用于会话列表倒序展示

---

## Session 模块重写方案

### 保留的公开 API

```python
upsert_session(
    session_id,
    scenario_id,
    scenario_label,
    dataset_name,
    messages=None,
    state=None,
    status=None,
) -> ChatSessionRecord

get_session(session_id) -> ChatSessionRecord | None
list_sessions() -> list[ChatSessionSummary]
get_session_payload(session_id) -> ChatSessionPayload | None
clear_sessions(scenario_id: str | None = None) -> None
close_session_db() -> None
```

### 内部结构

建议 `src/kgqa/session.py` 改为以下结构：

- `_DB_CONN: sqlite3.Connection | None`
- `_DB_LOCK = threading.Lock()`
- `_get_db()`：懒加载连接，首次使用时创建目录、连接数据库、初始化 schema、打开 WAL
- `_init_schema(conn)`：建表与建索引
- `_row_to_record(row)`：将 SQLite 行反序列化为 `ChatSessionRecord`
- `_json_dumps(value)` / `_json_loads(value)`：统一处理 JSON 编解码

### `upsert_session(...)` 语义

必须保持与当前内存实现一致的“部分更新”行为：

- 若会话不存在，则创建新记录
- 若会话已存在，保留原 `created_at`
- 每次写入都刷新 `updated_at`
- `messages is not None` 时才覆盖消息列表
- `state is not None` 时才覆盖状态
- `status is not None` 时才覆盖状态字段
- 每次写入都刷新 `scenario_id / scenario_label / dataset_name`
- `title` 仍由首条有效 user message 派生；若未提供新消息，则保留旧 title

实现方式建议：

- 先 `SELECT` 当前记录
- 在 Python 中合成更新后的完整 `ChatSessionRecord`
- 再执行 `INSERT OR REPLACE` 或等价的 `UPDATE`

不建议直接使用“字段缺失即默认值覆盖”的粗暴写法，否则会把高频增量落盘语义破坏掉。

### 读取接口

- `get_session(session_id)`：返回完整 `ChatSessionRecord`
- `get_session_payload(session_id)`：从 `get_session()` 构造 `ChatSessionPayload`
- `list_sessions()`：按 `updated_at DESC` 返回 summary，`message_count` 从反序列化后的 `messages` 长度计算

### 删除与关闭

- `clear_sessions()`：删除全部会话
- `clear_sessions(scenario_id=...)`：仅删除指定场景会话
- `close_session_db()`：提交并关闭连接，清空 `_DB_CONN`，函数需幂等

---

## API 集成改动

### 1. `src/kgqa/api.py`

`shutdown_event()` 从：

```python
clear_sessions()
```

改为：

```python
close_session_db()
```

这样服务关闭不会删除会话，只会关闭底层连接。

### 2. `POST /seed/load`

保留当前逻辑：

```python
clear_sessions(scenario_id=scenario.scenario_id)
```

原因：

- 重新导入同一场景数据后，旧会话中的 toolHistory、latestResult、查询语义上下文都可能失效
- 按场景清理即可，不应误删其他场景会话

### 3. `.gitignore`

新增：

```gitignore
data/sessions.db
data/sessions.db-wal
data/sessions.db-shm
```

---

## 失败处理与边界条件

### 1. 线程安全

虽然 SQLite 允许 `check_same_thread=False`，但 `session.py` 仍应通过模块锁串行化建连与写操作，避免连接初始化竞争和并发写入混乱。

### 2. JSON 反序列化失败

理论上这些 JSON 只由本应用写入。若读取失败，应直接抛出清晰异常，而不是悄悄吞掉并返回空数据；否则会把真实数据损坏伪装成“无会话内容”。

### 3. 进程内缓存

`session.py` 允许保留单连接缓存，但不能再保留内存级 session dict 作为真实数据源。SQLite 必须是唯一事实来源。

### 4. 测试隔离

测试环境下需要确保：

- 可将 `session_db_path` 指向临时目录
- 每个测试结束后调用 `close_session_db()`
- 若复用 `get_settings()` 缓存，需要显式清理或通过 monkeypatch 控制读取路径

否则不同测试可能共享同一个 SQLite 文件，导致互相污染。

---

## 文件改动清单

| 操作 | 文件 |
|---|---|
| 修改 | `src/kgqa/config.py` — 新增 `session_db_path` |
| 重写 | `src/kgqa/session.py` — SQLite 存储实现 |
| 修改 | `src/kgqa/api.py` — shutdown 改为 `close_session_db()` |
| 修改 | `.gitignore` — 忽略 SQLite 产物 |
| 修改 | `tests/test_chat_api.py` — 调整为 SQLite 隔离测试 |
| 可选新增 | `tests/test_session_sqlite.py` — 覆盖持久化/清理/重开场景 |

---

## 验证标准

### 自动化

1. 现有 `test_chat_api.py` 继续通过。
2. 新增或补强以下断言：
   - `upsert_session()` 后可读回完整 payload
   - `close_session_db()` 后重新读取，同一 SQLite 文件中的会话仍存在
   - `clear_sessions(scenario_id=...)` 只删除对应场景会话
   - `list_sessions()` 按 `updated_at DESC` 排序

### 手动验证

1. 启动服务，发起一轮对话，确认 `/chat/sessions` 中出现该会话。
2. 停止服务后重新启动，确认 `/chat/sessions` 和 `/chat/{session_id}/messages` 仍能读取该会话。
3. 对同一 `threadId` 继续追问，确认后端能读到此前消息与状态。
4. 执行指定场景的 `/seed/load`，确认仅该场景会话被清理，其他场景会话仍保留。
5. 确认工作区出现 `data/sessions.db`，但 `git status` 不跟踪该文件。

---

## 实施建议（评审补充）

以下为方案评审时识别的实现细节，不改变整体设计，但在编码时应予以注意。

### 1. 锁粒度：写加锁、读可不加锁

方案提到"模块内锁串行化建连与写操作"。实现时建议区分读写：

- **写操作**（`upsert_session`、`clear_sessions`）：必须在 `_DB_LOCK` 内完成
- **读操作**（`get_session`、`list_sessions`、`get_session_payload`）：WAL 模式下 SQLite 原生支持并发读，可以不加锁

当前内存实现的 `_SESSION_LOCK` 已经是全局锁，所以即使全部加锁行为也一致，但在 PoC 演示多浏览器 tab 同时查看会话列表时，读不阻塞写体验会更好。

### 2. upsert 实现：避免 INSERT OR REPLACE 的隐式 DELETE

SQLite 的 `INSERT OR REPLACE` 遇到主键冲突时实际执行 DELETE + INSERT，会重置 rowid 并触发 DELETE trigger。虽然当前没有 trigger，但更干净的做法有两种（任选）：

- **方式 A**：`INSERT ... ON CONFLICT(session_id) DO UPDATE SET ...`，在 SQL 层完成合并
- **方式 B**：先 `SELECT` 读出旧记录 → Python 中合成完整字段 → `UPDATE`（不存在则 `INSERT`）

两种方式都必须在同一把锁内完成以避免 TOCTOU。方案原文倾向方式 B，可以直接采用。

### 3. `_public_state()` 过滤内部字段——需显式注释

当前 `KGQAAgent` 的 `_public_state(state)` 会过滤 `_` 前缀字段（如 `_latest_rows`、`_budget`），所以这些不会进入 `upsert_session` 的 `state` 参数。这意味着 `state_json` 不会包含大体积查询结果，是正确的。

但这个隐含依赖在 `session.py` 侧不可见。建议在 `session.py` 的 `upsert_session` 实现顶部加注释说明：

```python
# 注意：调用方（agent.py）在传入 state 前已通过 _public_state() 剥离
# 了 _latest_rows 等大体积内部字段。如果未来有新调用方直接传入原始
# state，可能导致 state_json 膨胀。
```

### 4. `list_sessions()` 的读取效率

当前设计中 `list_sessions()` 需要返回 `message_count`，而 `messages_json` 是 JSON 文本列。如果直接 `SELECT *` 再在 Python 中反序列化整个 messages 只为算长度，在会话数较多时会有不必要的开销。

两种优化方向（任选，PoC 阶段可暂不实施但值得标记）：

- 在表中新增 `message_count INTEGER` 列，每次 upsert 时同步更新
- 使用 `SELECT session_id, title, ..., json_array_length(messages_json) AS message_count` 让 SQLite 在 SQL 层计算

当前 PoC 规模（几十个会话）下不会成为瓶颈，可作为后续优化项。

---

## 实施顺序

1. 先在 `config.py` 增加 `session_db_path` 与 `.gitignore`。
2. 重写 `session.py`，先跑通模块级读写与关闭逻辑。
3. 调整 `api.py` 的 shutdown 行为。
4. 更新并补强测试，确保 SQLite 文件级持久化在测试里可复现。
5. 最后做一次手动重启验证。

---

## 与后续上下文追问优化的关系

完成本阶段后，系统至少具备“会话在进程重启后仍可恢复”的基础能力。这并不自动等于“上下文追问已经足够好”，但它解决了一个更底层的问题：

- 追问优化可以基于真实、连续的历史会话进行
- 手动演示或人工纠偏后的上下文不会在重启后消失
- 后续如需做“从持久化会话中提取更长历史摘要”或“补充跨回合 schema grounding”，也有了稳定的数据基础

因此，`stage-04-6` 应视为后续上下文追问优化的前置依赖。
