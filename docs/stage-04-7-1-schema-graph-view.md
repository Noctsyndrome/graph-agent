# Stage 04-7-1: 图谱可视化（初版方案）

## 状态说明

本文档保留为 `stage-04-7` 的第一版方案归档。

定位：

- 保留基本方向：右侧图谱侧栏、Schema 视图、查询相关节点可视化
- 但其中若干设计在当前代码基线上过于理想化，不能直接作为实施基线

后续执行请以 `stage-04-7-2` 为准。

## 背景

`stage-04-2` 中曾规划过"图谱可视化"，但该设计已废弃（多场景、prompt 体系、会话持久化均已重构）。本方案基于当前代码状态重新设计。

核心需求变化：不仅仅展示 schema 结构，用户还需要看到查询过程中涉及的**实际节点**。因此图谱侧栏支持两种模式。

---

## 目标

1. 在 workspace 右侧新增**可收起的图谱侧栏**
2. 支持两种视图模式：
   - **Schema 模式**：展示当前场景的实体类型 + 关系类型结构图
   - **实例模式（渐进式）**：随对话推进，从每轮查询结果中提取实际节点实例，逐步累积构建图谱；节点间的边通过 schema 关系定义推断
3. 三个场景均可工作，完全 schema 驱动，无硬编码实体名
4. 不破坏现有 chat / 工具详情 Dialog / 结果渲染功能

## 非目标

- 不在本阶段实现精确的实例级关系连线（需要改造 Cypher RETURN 路径对象，留给后续迭代）
- 不在本阶段实现图谱交互编辑（点击节点跳转、右键菜单等）
- 不在本阶段实现全量图数据浏览或图谱搜索/过滤

## 演进路径

本阶段使用 **schema 推断**为实例节点补画关系边——只要累积的节点集合中存在两种实体类型、且 schema 中定义了它们之间的关系，就画一条推断边。这对 PoC 足够，但不精确（无法确定具体哪两个实例之间有边）。

后续迭代可改造 agent 的 Cypher 生成策略，让 RETURN 子句包含路径/关系变量，从而获得精确的实例级边。

---

## 可视化库选型

**选择 `react-force-graph-2d`**（~40KB gzip）

- schema 图 5-6 节点；实例图随对话增长通常 10-50 节点，偶尔上百——该库足以应对
- `{nodes, links}` 数据模型与两种模式的数据结构直接映射
- Canvas 渲染 + `nodeCanvasObject` 支持中文标签自定义绘制
- 自带力导向布局、缩放/平移，新增节点时自动重新布局

排除：cytoscape.js（~170KB，过重）、vis-network（~200KB，无 React wrapper）、裸 d3-force（从零搭渲染层工作量不匹配）

---

## UI 布局

### 当前

```
.app-shell (grid: 320px 1fr)
├── .sidebar (会话列表 + 状态)
└── .workspace (flex column)
    ├── .workspace-header
    ├── .thread-shell
    │   ├── .thread-shell-header (context badges)
    │   └── .thread-panel (聊天区)
    └── Dialog (工具详情，点击 tool chip 按需弹出)
```

### 改造后

```
.app-shell (grid: 320px 1fr)
├── .sidebar (不变)
└── .workspace (flex column)
    ├── .workspace-header (不变)
    ├── .workspace-body (grid: 1fr [320px])
    │   ├── .thread-shell (不变)
    │   └── .graph-sidebar (可收起)
    │       ├── .graph-sidebar-header (模式切换 + 收起按钮)
    │       └── .graph-sidebar-body (ForceGraph2D)
    └── Dialog (完全不变)
```

- `.workspace-body` 默认 `grid-template-columns: 1fr`；展开图谱时 `1fr 320px`
- Toggle 入口：`thread-shell-header` context badges 行末尾
- 侧栏 header 内放模式切换（Schema / 实例），收起按钮
- 工具详情 Dialog 是 `position: fixed` 浮层，与侧栏无冲突
- `≤ 980px` 隐藏图谱栏

---

## 技术方案

### 一、后端

#### 1. `src/kgqa/schema.py` — 新增三个方法

**`graph_data()`** — Schema 模式的 node-link 数据：

```python
def graph_data(self) -> dict[str, Any]:
    nodes = [
        {
            "id": e["name"],
            "label": e.get("description", e["name"]),
            "properties": list(e.get("properties", {}).keys()),
        }
        for e in self._schema.get("entities", [])
    ]
    links = [
        {
            "source": r["from"],
            "target": r["to"],
            "label": r["name"],
            "cardinality": r.get("cardinality", ""),
            "description": r.get("description", ""),
        }
        for r in self._schema.get("relationships", [])
    ]
    return {"nodes": nodes, "links": links}
```

**`extract_active_types()`** — 从 Cypher 文本中提取涉及的实体/关系名：

```python
def extract_active_types(self, cypher: str) -> dict[str, list[str]]:
    entities = [e["name"] for e in self._schema.get("entities", []) if e["name"] in cypher]
    rels = [r["name"] for r in self._schema.get("relationships", []) if r["name"] in cypher]
    return {"entities": entities, "relationships": rels}
```

**`extract_instance_nodes()`** — 从查询结果行中提取实例节点：

```python
def extract_instance_nodes(
    self, rows: list[dict[str, Any]], active_types: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """从 execute_cypher 的结果行中提取实例级图节点。

    策略：
    1. 如果行中包含 __type__: "node" 的值，直接提取（agent RETURN 了完整节点时）
    2. 否则，按照列名与 schema entity 属性的重叠度，将行匹配到最佳实体类型，
       构造虚拟实例节点

    返回值中每个节点: {id, type, label, properties}
    """
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()

    entity_props = {
        e["name"]: set(e.get("properties", {}).keys()) - {"id", "dataset"}
        for e in self._schema.get("entities", [])
    }

    active_entity_names = set(active_types.get("entities", []))

    for row in rows:
        # 1) 显式 node 对象
        for value in row.values():
            if isinstance(value, dict) and value.get("__type__") == "node":
                node_id = value.get("element_id", "")
                if node_id and node_id not in seen:
                    seen.add(node_id)
                    labels = value.get("labels", [])
                    props = value.get("properties", {})
                    nodes.append({
                        "id": node_id,
                        "type": labels[0] if labels else "Unknown",
                        "label": str(props.get("name", node_id)),
                        "properties": props,
                    })

        # 2) 标量行 → 匹配到最佳实体类型
        if not any(
            isinstance(v, dict) and v.get("__type__") == "node"
            for v in row.values()
        ):
            col_names = set(row.keys())
            best_type, best_overlap = "", 0
            for etype in active_entity_names:
                props = entity_props.get(etype, set())
                overlap = len(col_names & props)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_type = etype
            if best_type and best_overlap > 0:
                name_val = str(
                    row.get("name", "")
                    or row.get(next((c for c in row if "name" in c.lower()), ""), "")
                    or next(
                        (str(v) for v in row.values() if isinstance(v, str) and v.strip()), ""
                    )
                )
                node_id = f"{best_type}:{name_val}"
                if node_id not in seen and name_val:
                    seen.add(node_id)
                    nodes.append({
                        "id": node_id,
                        "type": best_type,
                        "label": name_val,
                        "properties": {k: v for k, v in row.items() if k != "dataset"},
                    })

    return nodes
```

> 虚拟节点的 id 使用 `EntityType:name_value` 格式，跨轮次自动去重（同一实体不会重复添加）。
> 当 agent RETURN 完整 node 对象时使用 `element_id`，精度更高。

#### 2. `src/kgqa/api.py` — 新增 `GET /schema/graph`

```python
@app.get("/schema/graph")
def schema_graph(scenario_id: str | None = None) -> dict[str, object]:
    scenario_settings = build_scenario_settings(settings, scenario_id)
    return SchemaRegistry(scenario_settings).graph_data()
```

#### 3. `src/kgqa/agent.py` — CUSTOM 事件携带实例节点 + 高亮类型

在 `_run_tool()` 中 `execute_cypher` 成功时：

```python
if tool_name == "execute_cypher" and tool_status == "ok":
    state["_latest_rows"] = list(tool_result.get("rows", []))
    cypher_text = str(tool_args.get("cypher", ""))
    active_types = self.schema.extract_active_types(cypher_text)
    state["_active_graph_types"] = active_types
    state["_instance_nodes"] = self.schema.extract_instance_nodes(
        tool_result.get("rows", []), active_types
    )
```

在 CUSTOM 事件中携带：

```python
if tool_name == "format_results" and tool_status == "ok":
    event_bundle.append({
        "type": "CUSTOM",
        "name": "kgqa_ui_payload",
        "value": tool_result,
        "activeGraphTypes": state.get("_active_graph_types", {}),
        "instanceNodes": state.get("_instance_nodes", []),
        "timestamp": time.time(),
    })
```

在 `_public_state()` 排除列表中添加 `"_active_graph_types"` 和 `"_instance_nodes"`。

---

### 二、前端

#### 4. `frontend/src/types.ts` — 新增类型

```typescript
// ---- Schema 模式 ----
export interface SchemaGraphNode {
  id: string;          // 实体名 "OperatingCompany"
  label: string;       // 中文描述 "物业经营平台公司"
  properties: string[];
}

export interface SchemaGraphLink {
  source: string;
  target: string;
  label: string;       // 关系名 "MANAGES_PROJECT"
  cardinality: string;
  description: string;
}

export interface SchemaGraphData {
  nodes: SchemaGraphNode[];
  links: SchemaGraphLink[];
}

// ---- 实例模式 ----
export interface InstanceGraphNode {
  id: string;          // element_id 或 "EntityType:name"
  type: string;        // 实体类型名
  label: string;       // 显示名
  properties: Record<string, unknown>;
}

export interface ActiveGraphTypes {
  entities: string[];
  relationships: string[];
}
```

#### 5. `frontend/src/api.ts` — 新增 `fetchSchemaGraph()`

```typescript
export function fetchSchemaGraph(scenarioId?: string): Promise<SchemaGraphData> {
  const suffix = scenarioId ? `?scenario_id=${encodeURIComponent(scenarioId)}` : "";
  return readJson<SchemaGraphData>(`/schema/graph${suffix}`);
}
```

#### 6. `frontend/src/App.tsx` — 布局改造 + 双模式状态

**新增 state：**

```typescript
const [schemaGraph, setSchemaGraph] = useState<SchemaGraphData | null>(null);
const [activeGraphTypes, setActiveGraphTypes] = useState<ActiveGraphTypes | null>(null);
const [instanceNodes, setInstanceNodes] = useState<Map<string, InstanceGraphNode>>(new Map());
const [graphSidebarOpen, setGraphSidebarOpen] = useState(false);
const [graphMode, setGraphMode] = useState<"schema" | "instance">("schema");
```

**场景切换时：**

在 `refreshScenarioMeta` 的 `Promise.all` 中加入 `fetchSchemaGraph(scenarioId)`。切换场景时清空 `instanceNodes` 和 `activeGraphTypes`。

**CUSTOM 事件处理 — 累积实例节点：**

```typescript
if (event.type === "CUSTOM" && event.name === "kgqa_ui_payload") {
  // 高亮类型
  const types = event.activeGraphTypes as ActiveGraphTypes | undefined;
  if (types) setActiveGraphTypes(types);

  // 累积实例节点（跨轮去重）
  const newNodes = event.instanceNodes as InstanceGraphNode[] | undefined;
  if (newNodes?.length) {
    setInstanceNodes(prev => {
      const next = new Map(prev);
      for (const node of newNodes) {
        next.set(node.id, node);
      }
      return next;
    });
  }
}
```

**新会话时清空实例节点：**

创建新会话或切换会话时，`setInstanceNodes(new Map())`。

**Toggle 按钮：**

在 `thread-shell-header` 的 context badges 区域末尾：

```tsx
<button
  className="context-badge graph-toggle"
  onClick={() => setGraphSidebarOpen(prev => !prev)}
>
  <GitBranch size={13} />
  图谱
</button>
```

**布局改造：**

在 `workspace-header` 和 Dialog 之间插入 `.workspace-body`（grid），左列 `.thread-shell`（不变），右列 `.graph-sidebar`：

```tsx
<div className={`workspace-body${graphSidebarOpen ? " graph-open" : ""}`}>
  <div className="thread-shell">{/* 完全不变 */}</div>

  {graphSidebarOpen && (
    <aside className="graph-sidebar">
      <div className="graph-sidebar-header">
        <div className="graph-mode-switch">
          <button
            className={graphMode === "schema" ? "active" : ""}
            onClick={() => setGraphMode("schema")}
          >Schema</button>
          <button
            className={graphMode === "instance" ? "active" : ""}
            onClick={() => setGraphMode("instance")}
          >实例</button>
        </div>
        <button onClick={() => setGraphSidebarOpen(false)}>
          <ChevronLeft size={14} />
        </button>
      </div>
      <div className="graph-sidebar-body">
        <SchemaGraphView
          mode={graphMode}
          schemaGraph={schemaGraph}
          activeTypes={activeGraphTypes}
          instanceNodes={instanceNodes}
        />
      </div>
    </aside>
  )}
</div>
```

工具详情 Dialog **完全不动**。

#### 7. `frontend/src/components/schema-graph-view.tsx` — 新建（~180 行）

**Props：**

```typescript
interface Props {
  mode: "schema" | "instance";
  schemaGraph: SchemaGraphData | null;
  activeTypes: ActiveGraphTypes | null;
  instanceNodes: Map<string, InstanceGraphNode>;
}
```

**Schema 模式渲染：**

- 数据直接使用 `schemaGraph.nodes` / `schemaGraph.links`
- `nodeCanvasObject`：圆形 + 中文 label（entity description）
- 高亮逻辑：`activeTypes` 非空时命中的实体/关系用 `#3b82f6`，其余 30% 透明度
- `linkDirectionalArrowLength: 6` 显示箭头
- `linkCanvasObject` 绘制关系名标签
- `linkCurvature` 处理自引用和平行边

**实例模式渲染：**

- **节点**：从 `instanceNodes` Map 构造，按 `type` 分配颜色（每种实体类型一个颜色）
- `nodeCanvasObject`：小圆形 + `label`（实例名）+ 下方小字标注实体类型
- **边（schema 推断）**：遍历 `schemaGraph.links`，对每条 schema 关系 `A→B`，如果 `instanceNodes` 中同时存在 type=A 和 type=B 的节点，则为每对 (A_instance, B_instance) 画一条推断边。
  - 推断边用虚线标识，与 schema 边的实线区分
  - 由于推断不精确，当某类型节点数 × 另一类型节点数过大时，可限制连线数量避免视觉混乱
- **空状态**：无累积节点时显示"尚未查询，提问后图谱将逐步呈现"

**共享行为：**

- `ResizeObserver` 监听容器尺寸动态更新 `width/height`
- `cooldownTicks: 100`，`d3AlphaDecay: 0.05` 保证小图快速稳定
- CJK 字体回退：`'PingFang SC', 'Microsoft YaHei', sans-serif`

#### 8. `frontend/src/index.css` — 新增样式

```css
/* workspace 内部两列布局 */
.workspace-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
.workspace-body.graph-open {
  grid-template-columns: 1fr 320px;
}

/* 图谱侧栏 */
.graph-sidebar {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 24px;
  background: var(--surface);
  box-shadow: var(--shadow-soft);
  overflow: hidden;
}
.graph-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}
.graph-sidebar-body {
  flex: 1;
  min-height: 0;
  position: relative;
}

/* 模式切换按钮组 */
.graph-mode-switch {
  display: flex;
  gap: 4px;
  padding: 2px;
  border-radius: 10px;
  background: var(--surface-muted);
}
.graph-mode-switch button {
  padding: 4px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 12px;
  color: var(--text-muted);
}
.graph-mode-switch button.active {
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow-soft);
}

/* toggle 按钮 */
.graph-toggle {
  cursor: pointer;
  transition: background 140ms ease, border-color 140ms ease;
}
.graph-toggle:hover {
  background: var(--surface-muted);
  border-color: var(--border-strong);
}

/* 响应式 */
@media (max-width: 980px) {
  .graph-sidebar { display: none; }
  .workspace-body.graph-open { grid-template-columns: 1fr; }
}
```

---

## 实例模式的数据流

```
用户提问 "深圳万象城有哪些铺位"
  → agent 生成 Cypher: MATCH (p:OperatingProject)-[:HAS_SPACE]->(s:Space) ...
  → execute_cypher 返回 rows（标量属性行）
  → extract_active_types → {entities: ["OperatingProject", "Space"]}
  → extract_instance_nodes → [
      {id: "OperatingProject:深圳万象城", type: "OperatingProject", label: "深圳万象城", ...},
      {id: "Space:B101", type: "Space", label: "B101", ...},
      {id: "Space:A201", type: "Space", label: "A201", ...},
      ...
    ]
  → CUSTOM 事件携带 instanceNodes
  → 前端 setInstanceNodes 累积

用户追问 "这些铺位的商户"
  → execute_cypher 返回 Tenant 行
  → extract_instance_nodes → [{id: "Tenant:星巴克", ...}, ...]
  → 前端累积，现在有 OperatingProject + Space + Tenant 三类节点

前端渲染实例模式图谱：
  - 节点：深圳万象城、B101、A201、星巴克 ...
  - 推断边：schema 说 OperatingProject→Space (HAS_SPACE)
           → 画 深圳万象城──B101、深圳万象城──A201（虚线）
           schema 说 Space→Tenant (OCCUPIED_BY)
           → 画 B101──星巴克（虚线）
```

> 推断边的局限：无法确定星巴克是在 B101 还是 A201。推断边为所有可能的 type 组合画线，视觉上可能产生多余的连线。后续改造 Cypher 返回路径后可解决。

---

## 变更文件清单

| 操作 | 文件 | 改动 |
|------|------|------|
| 修改 | `src/kgqa/schema.py` | +50 行（graph_data + extract_active_types + extract_instance_nodes） |
| 修改 | `src/kgqa/api.py` | +8 行（/schema/graph 端点） |
| 修改 | `src/kgqa/agent.py` | +8 行（instance nodes 提取 + 事件携带 + state 排除） |
| 修改 | `frontend/src/types.ts` | +25 行（schema + instance 类型） |
| 修改 | `frontend/src/api.ts` | +5 行 |
| 修改 | `frontend/src/App.tsx` | ~60 行（state + event + 布局 + 模式切换） |
| 修改 | `frontend/src/index.css` | +70 行（sidebar + mode switch + 响应式） |
| **新建** | `frontend/src/components/schema-graph-view.tsx` | ~180 行 |
| npm | `frontend/package.json` | +1 依赖 react-force-graph-2d |

---

## 实施顺序

**Phase 1: 后端**（可独立验证）

1. `schema.py` — `graph_data()` + `extract_active_types()` + `extract_instance_nodes()`
2. `api.py` — `/schema/graph` 端点
3. `agent.py` — CUSTOM 事件携带 `activeGraphTypes` + `instanceNodes`

验证：`curl /schema/graph?scenario_id=property` 返回 6 nodes + 6 links。pytest 全部通过。

**Phase 2: 前端骨架**（侧栏结构 + 状态管理）

4. `types.ts` + `api.ts` — 类型和 API 函数
5. `index.css` — workspace-body / graph-sidebar / mode-switch 样式
6. `App.tsx` — state + event handler + 布局改造 + toggle + 模式切换

验证：侧栏可展开/收起，两个模式按钮可切换，Tab 2 暂时空白。

**Phase 3: 图谱组件**（端到端联调）

7. `npm install react-force-graph-2d`
8. `schema-graph-view.tsx` — Schema 模式渲染
9. `schema-graph-view.tsx` — 实例模式渲染 + 推断边

验证：完整问答流程中图谱随对话增长。

---

## 验证方案

### 后端自动测试

- `graph_data()` 对三个 schema YAML 返回正确的 node/link 数量
- `extract_active_types("MATCH (p:OperatingProject)-[:HAS_SPACE]->(s:Space)")` → `entities: ["OperatingProject", "Space"], relationships: ["HAS_SPACE"]`
- `extract_instance_nodes()` 对含 `__type__: "node"` 的行提取出正确节点
- `extract_instance_nodes()` 对标量行按属性重叠度匹配到正确实体类型
- `/schema/graph?scenario_id=property` 返回 6 nodes + 6 links
- 全部现有 pytest 通过

### 前端手动验证

1. 默认侧栏收起，聊天区全宽
2. 点击"图谱"按钮 → 侧栏展开 320px
3. Schema 模式：显示当前场景实体/关系结构，中文标签可读
4. 执行查询 → Schema 模式中涉及的实体/关系高亮
5. 切到实例模式 → 显示从查询结果中提取的实际节点
6. 继续追问 → 新节点累积到图上，推断边自动补画
7. 切换场景 → 两个模式均刷新，实例节点清空
8. 新建会话 → 实例节点清空
9. 工具详情 Dialog 不受影响
10. `npm run build` 打包成功，bundle 增量 < 50KB gzip

---

## 边界情况

| 情况 | 处理 |
|------|------|
| 自引用关系（`CAN_REPLACE: Model→Model`） | `linkCurvature` 绘制弧线 |
| 平行边（同一对节点多条关系） | 弧度分离 |
| 聚合查询（COUNT/AVG）无实例节点 | `extract_instance_nodes` 返回空，图谱不变 |
| 实例模式推断边过多 | 限制单对实体类型最多画 N 条推断边 |
| 空图 / 未查询 | Schema: "暂无图谱数据"；实例: "尚未查询" |
| 小屏（≤ 980px） | 隐藏图谱栏 |
| 标量行无法匹配实体类型 | 属性重叠度为 0 时跳过，不生成虚拟节点 |
| 同名节点跨类型 | id 为 `EntityType:name`，不会混淆 |
