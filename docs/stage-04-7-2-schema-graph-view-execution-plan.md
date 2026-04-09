# Stage 04-7-2: 图谱可视化（执行规划）

## 背景

`stage-04-7-1` 保留了图谱可视化的一版完整设想，方向总体正确，但在当前代码基线上存在几个直接影响落地的问题：

- 实例图状态仅依赖实时 `CUSTOM` 事件，无法和已实现的会话持久化打通
- 对标量结果行的实例节点提取过于乐观，和当前 `execute_cypher` 返回的 alias 列形态并不匹配
- “只要类型同时存在就两两补边”的推断规则误导性较强，容易画出看似真实但其实没有证据支持的边

因此，本阶段不应直接照搬 `04-7-1`，而是保留其基本方向并重构为可执行版本。

---

## 目标

1. 在聊天主区右侧增加可收起的图谱侧栏。
2. 提供两个模式：
   - `Schema`：稳定展示当前场景的实体类型与关系结构
   - `查询图`：展示当前会话中已经识别出的查询相关节点，以及有证据支持的边
3. 图谱状态可随会话恢复，不依赖页面常驻内存。
4. 不破坏现有聊天、工具详情 Dialog、结果渲染和会话持久化能力。

---

## 非目标

- 本阶段不做“完整实例图”承诺。
- 不做基于类型笛卡尔积的全量推断边。
- 不做图谱编辑、点击跳转、图上筛选、关系搜索。
- 不要求所有标量查询都能恢复出节点。

---

## 设计原则

1. 宁可少画，不画错。
2. 图谱状态必须可恢复。
3. 先做稳定 MVP，再做增强，不把高风险推断塞进第一版。
4. 查询图中的边必须来自“明确证据”或“受约束的行内推断”，不能来自纯类型共现。

---

## 总体方案

### 一、模式定义

#### 1. Schema 模式

直接展示当前场景 schema 中的：

- 实体类型节点
- 关系类型边

并根据最近一次成功查询的 `active_types` 做高亮。

这是第一阶段 MVP，风险最低，且对用户理解图谱结构已经有明显价值。

#### 2. 查询图模式

展示当前会话中已经识别出的“查询相关节点”和“有证据支持的边”。

这里不使用“实例模式”命名，原因是第一版并不保证获得完整、精确的实例级关系；`查询图` 更贴合真实能力边界。

---

## 数据模型重构

### 1. 后端统一输出 `graph_delta`

不再拆成松散的：

- `extract_active_types()`
- `extract_instance_nodes()`

改为统一的增量输出：

```python
{
  "active_types": {
    "entities": [...],
    "relationships": [...]
  },
  "nodes": [...],
  "edges": [...],
  "inference_level": "exact" | "row_inferred"
}
```

其中：

- `active_types` 用于 Schema 模式高亮
- `nodes / edges` 用于查询图累积
- `inference_level` 用于前端视觉区分或后续调试

### 2. 图谱状态必须进入可恢复链路

图相关数据不能只通过实时 `CUSTOM` 事件给前端。

本阶段采用以下策略：

- `execute_cypher` 成功后，将 compact `graph_delta` 写入对应的 `toolHistory` 项
- `CUSTOM` 事件仍可携带同样数据，供当前会话实时更新 UI
- 前端在 `hydrateSession()` 时，从持久化的 `toolHistory` 重建查询图

这样可以同时满足：

- 实时交互体验
- 页面刷新后恢复
- 服务重启后恢复
- 重新点开旧会话后恢复

这是 `04-7-1` 最需要纠正的结构点。

---

## 节点与边的提取策略

### 1. `active_types` 提取

不使用“实体名出现在 Cypher 字符串中就算命中”的粗略逻辑。

建议复用当前 `CypherSafetyValidator` 中已有的 regex 解析思路：

- 从节点 pattern 中提取 label
- 从 relationship pattern 中提取 relationship name
- 再与 schema 中的实体名、关系名交叉过滤

这样能显著减少误判。

### 2. 节点提取分层

按证据强度分三层处理：

#### 第一层：显式 `node`

当 row 中存在：

```python
{"__type__": "node", ...}
```

直接提取。

这是最可靠的节点来源，应优先支持。

#### 第二层：显式 `path`

当 row 中存在：

```python
{"__type__": "path", ...}
```

直接拆出路径中的节点与边。

这也是精确图的最佳来源。

#### 第三层：受约束的标量行识别

仅在满足明确命名模式时，才从标量行构造节点，例如：

- `space_id + space_name`
- `tenant_name`
- `lease_id + start_date/end_date`
- `foo.name / foo.id / foo.status` 这类带别名前缀列名

不要依赖“任意列名与属性重叠度最高者即实体类型”的宽松策略。

若无法稳定识别，直接跳过。

### 3. 边提取规则

边只允许来自两类证据：

#### 精确边

来源：

- `relationship` payload
- `path` payload

#### 行内推断边

若同一条 row 中识别出了两个不同类型的节点，且 schema 中存在这两个类型之间的关系，则可补一条“row_inferred”边。

例如：

- row 中同时出现 `space_id/space_name` 和 `tenant_name`
- schema 中存在 `Space -> Tenant`
- 则允许补一条 `Space -> Tenant`

禁止做的事情：

- 仅因为图里同时存在 `Space` 和 `Tenant` 类型节点，就给所有节点两两连线

这条规则必须明确写死，否则查询图会迅速失真。

---

## 后端实施规划

### Phase 1: Schema graph + active highlight

#### 1. `src/kgqa/schema.py`

新增：

- `graph_data()`
- `extract_active_types(cypher: str)`

此阶段先不做标量行节点提取。

#### 2. `src/kgqa/api.py`

新增：

- `GET /schema/graph`

接口实现要基于当前真实 Scenario API：

- 先 `get_scenario_definition(scenario_id)`
- 再 `build_scenario_settings(settings, scenario)`
- 再构造 `SchemaRegistry`

不能直接把 `scenario_id` 传给 `build_scenario_settings()`。

#### 3. `src/kgqa/agent.py`

在 `execute_cypher` 成功后：

- 提取 `active_types`
- 写入 `toolHistory` 当前条目
- 同时通过 `CUSTOM` 事件发给前端

此阶段 `CUSTOM` 事件里只需要：

```python
{
  "type": "CUSTOM",
  "name": "kgqa_ui_payload",
  "value": tool_result,
  "graph_delta": {
    "active_types": {...},
    "nodes": [],
    "edges": [],
    "inference_level": "exact"
  }
}
```

这样前端即使还没做查询图，也可以先支持 Schema 高亮。

### Phase 2: 查询图 v1

#### 4. `src/kgqa/schema.py`

新增：

- `extract_graph_delta(cypher: str, rows: list[dict[str, Any]])`

内部调用：

- `extract_active_types()`
- `extract_graph_nodes_from_rows()`
- `extract_graph_edges_from_rows()`

建议拆成私有方法，避免一个函数承担全部细节。

#### 5. `src/kgqa/agent.py`

在 `execute_cypher` 成功时：

- 计算 `graph_delta`
- 将 compact 结果挂到最新 `toolHistory` 条目，例如：

```python
{
  "tool_name": "execute_cypher",
  ...
  "graph_delta": {
    "active_types": {...},
    "nodes": [...],
    "edges": [...],
    "inference_level": "row_inferred"
  }
}
```

并在 `CUSTOM` 事件中同步携带。

这里不要把完整大图存进独立 public state；以 `toolHistory` 作为恢复数据源更贴合当前架构。

### Phase 3: 查询图增强

仅在前两阶段稳定后再做：

- 更强的 alias 识别
- 限制节点上限和边上限
- 查询图 legend
- tooltip
- 对 property 场景补更稳定的标量映射规则

---

## 前端实施规划

### Phase 1: 右侧侧栏 + Schema 模式

#### 1. `frontend/src/types.ts`

新增：

- `SchemaGraphData`
- `SchemaGraphNode`
- `SchemaGraphLink`
- `GraphDelta`
- `GraphActiveTypes`

此阶段不必先定义完整的“实例图”类型体系。

#### 2. `frontend/src/api.ts`

新增：

- `fetchSchemaGraph(scenarioId?: string)`

#### 3. `frontend/src/App.tsx`

新增 state：

- `schemaGraph`
- `graphSidebarOpen`
- `graphMode`
- `activeGraphTypes`

场景切换时：

- 拉取 `schemaGraph`
- 清空当前会话的查询图缓存

事件处理时：

- 读取 `CUSTOM.graph_delta.active_types`
- 更新 `activeGraphTypes`

#### 4. 布局改造

在现有：

- `workspace-header`
- `thread-shell`
- `Dialog`

之间插入：

- `workspace-body`
- `graph-sidebar`

保留当前工具详情 Dialog 完全不动。

### Phase 2: 查询图模式

#### 5. 前端图状态来源

前端图状态必须支持两种来源：

- 实时：来自 `CUSTOM.graph_delta`
- 恢复：来自 `sessionPayload.state/toolHistory`

建议在 `hydrateSession()` 后运行一次：

- `rebuildQueryGraphFromState(payload.state)`

该函数遍历 `toolHistory` 中所有带 `graph_delta` 的 `execute_cypher` 条目，重建：

- `queryGraphNodes`
- `queryGraphEdges`
- `activeGraphTypes`

#### 6. 查询图状态结构

前端内部建议维护：

```typescript
type QueryGraphNodeMap = Map<string, QueryGraphNode>
type QueryGraphEdgeMap = Map<string, QueryGraphEdge>
```

这样可以跨轮去重。

不要仅存 `instanceNodes`，因为边同样需要持久累积与去重。

#### 7. 图组件

新建：

- `frontend/src/components/schema-graph-view.tsx`

但组件职责应收敛为：

- 接收 `mode`
- 接收 `schemaGraph`
- 接收 `queryGraph`
- 接收 `activeGraphTypes`
- 只负责渲染，不负责推断

所有节点/边推断尽量留在后端或 `App.tsx` 的状态重建层，不要把图推断逻辑散落进 UI 组件。

---

## 视觉与交互建议

### 1. 模式文案

建议用：

- `Schema`
- `查询图`

而不是：

- `Schema`
- `实例`

### 2. 查询图中的视觉区分

若边或节点来自不同证据强度，前端应区分：

- `exact`: 实线 / 实色
- `row_inferred`: 虚线 / 降低透明度

这样用户知道哪些是系统真实拿到的，哪些是受约束推断。

### 3. 小屏策略

`≤980px` 时：

- 隐藏图谱侧栏
- 同时隐藏图谱 toggle 按钮

避免入口仍可点击却无反馈。

---

## 文件变更清单

### Phase 1

| 操作 | 文件 | 改动 |
|---|---|---|
| 修改 | `src/kgqa/schema.py` | `graph_data()` + `extract_active_types()` |
| 修改 | `src/kgqa/api.py` | `/schema/graph` |
| 修改 | `src/kgqa/agent.py` | `graph_delta.active_types` 注入 `toolHistory` + `CUSTOM` |
| 修改 | `frontend/src/types.ts` | schema graph / graph delta 类型 |
| 修改 | `frontend/src/api.ts` | `fetchSchemaGraph()` |
| 修改 | `frontend/src/App.tsx` | 侧栏开关、布局、Schema 模式状态 |
| 修改 | `frontend/src/index.css` | `workspace-body` / `graph-sidebar` 样式 |
| 新建 | `frontend/src/components/schema-graph-view.tsx` | Schema 图渲染 |
| npm | `frontend/package.json` | `react-force-graph-2d` |

### Phase 2

| 操作 | 文件 | 改动 |
|---|---|---|
| 修改 | `src/kgqa/schema.py` | `extract_graph_delta()` 与节点/边提取 |
| 修改 | `src/kgqa/agent.py` | 将 compact `graph_delta` 落入 `toolHistory` |
| 修改 | `frontend/src/App.tsx` | 从 `toolHistory` 重建查询图 |
| 修改 | `frontend/src/components/schema-graph-view.tsx` | 查询图渲染 |

---

## 测试与验证

### 后端自动测试

至少新增以下覆盖：

1. `graph_data()` 对三个 schema 返回正确 node/link 数量。
2. `extract_active_types()` 能从典型 Cypher 提取实体与关系。
3. `extract_graph_delta()` 对 `node` payload 提取正确节点与边。
4. `extract_graph_delta()` 对 `path` payload 提取正确节点与边。
5. `extract_graph_delta()` 对受约束标量行只提取可确认节点，不误补边。
6. `/schema/graph?scenario_id=property` 返回正确结构。
7. `toolHistory` 中写入 `graph_delta` 后，会话持久化与恢复不受影响。

### 前端手动验证

#### Phase 1

1. 默认图谱侧栏收起。
2. 点击图谱按钮后展开右栏。
3. `Schema` 模式正常显示当前场景实体/关系。
4. 执行查询后，相关实体/关系高亮。
5. 切换场景时，Schema 图刷新。

#### Phase 2

6. 执行一个返回明确节点的查询后，查询图出现节点。
7. 继续追问后，查询图增量累积。
8. 刷新页面或重启服务后重新打开同一会话，查询图可恢复。
9. 新建会话时，查询图为空。

---

## 风险控制

### 1. 节点识别失败

处理：

- 跳过，不报错
- Schema 模式仍可用
- 查询图只显示已确认部分

### 2. 查询图过大

处理：

- 首版限制节点总数和边总数
- 超限时仅保留最近 N 个 delta

### 3. toolHistory 膨胀

处理：

- `graph_delta` 存 compact 结构
- 不存完整 `_latest_rows`
- 不在 public state 里存整张展开后的图

### 4. 用户误解推断边

处理：

- 查询图默认展示 legend
- 明确区分 `exact` 与 `row_inferred`

---

## 推荐实施顺序

1. 先完成 Phase 1，交付稳定的 `Schema + active highlight`。
2. 在 Phase 1 验证通过后，再进入 Phase 2 的查询图。
3. Phase 2 先只支持 `node/path payload + 明确标量模式`。
4. 只有在真实对话 case 足够稳定后，才补更强的 alias 推断。

---

## 最终判断

`04-7-1` 的基本方向值得保留，但执行时必须从“完整实例图设想”收敛为“两阶段可交付方案”：

- 第一阶段交付稳定结构图
- 第二阶段交付保守但可恢复的查询图

这样才能在当前项目的 agent、session persistence、前端布局和 Neo4j 返回形态之上真正落地。
