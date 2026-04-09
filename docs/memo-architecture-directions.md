# 架构方向备忘：GraphRAG 定位 & Agent 拆分

> 记录时间：2026-04-02
> 背景：stage-04-7 图谱可视化规划期间，对两个架构层面问题的思考。

---

## 一、NL2Cypher vs GraphRAG：我们在做什么，行业在做什么

### 当前系统定位

本系统是 **KGQA（Knowledge Graph Question Answering）**，核心链路：

```
自然语言问题 → Agent ReAct 循环 → Cypher 生成 → Neo4j 精确查询 → 结果格式化 → 自然语言回答
```

本质是 **NL2Cypher**，类似 NL2SQL，但目标数据库是图数据库。

### GraphRAG 是什么

GraphRAG（Microsoft Research 2024）解决的是一个不同的问题：

1. 从**非结构化文本**（文档、报告）出发，用 LLM 自动抽取实体和关系，**构建**知识图谱
2. 对图做社区检测（Leiden 算法），生成层级摘要
3. 查询时检索相关子图/社区摘要，喂给 LLM 生成回答
4. 两种模式：Local Search（实体邻域检索）和 Global Search（社区摘要，回答宏观问题）

### 对比

| 维度 | 我们（KGQA / NL2Cypher） | GraphRAG |
|------|--------------------------|----------|
| 图谱来源 | 已有结构化 KG（手工建模 + seed data） | 从非结构化文本自动构建 |
| 查询方式 | 精确 Cypher 查询 | 近似检索（embedding + 社区摘要） |
| 回答精度 | 高——基于精确查询结果 | 中——基于检索到的上下文片段 |
| 适用场景 | schema 清晰的领域数据（物业资产、设备台账） | 大量文档、知识散落在文本中 |
| 短板 | 依赖 schema 质量和 Cypher 生成能力 | 图谱质量依赖 LLM 抽取效果，难以保证精确性 |

### 结论

**互补，非替代。**

- NL2Cypher 适合**精确查询已有结构化数据**
- GraphRAG 适合**从文档构建知识、回答模糊/宏观问题**
- 一个完整产品可能同时需要两者：GraphRAG 负责图谱构建 + 开放问题回答，NL2Cypher 负责结构化数据精确查询
- 当前路线没有问题。如果后续需求涉及"从非结构化文档构建图谱"，可引入 GraphRAG 的图构建思路，但查询层的 NL2Cypher 仍有独立价值

---

## 二、Agent 拆分时机与方向

### 当前单 Agent 职责

```
KGQAAgent
├── 对话理解（意图识别、多轮上下文追踪）
├── Schema 感知（focus inference、domain values 加载）
├── Cypher 生成 + 校验 + 执行 + 错误自修复
├── 结果序列化 + 格式选择
├── 自然语言答案生成
└── (stage-04-7 新增) 实例节点提取 → 图谱构建数据
```

### 潜在拆分方向

```
Orchestrator（编排 Agent）
│  对话理解、任务拆解、结果呈现、子 agent 调度
│
├── Graph Query Agent（图查询）
│   Schema 理解、Cypher 生成/校验/执行、错误诊断/自修复
│
├── Answer Agent（回答生成）
│   结果序列化、自然语言答案合成
│
└── Graph Builder Agent（图谱构建，未来）
    从查询结果/文档中提取实例节点、关系推断/补全、图谱可视化数据
```

### 现阶段不拆分的理由

1. 当前规模（3 场景、5-7 工具、单轮 max 16 步）仍在单 agent 可控范围
2. 拆分引入 agent 间通信复杂度（state 传递、错误传播、调试链路变长）
3. 过早拆分容易切在错误的边界上——需要更多场景验证

### 建议的演进策略

- **现阶段**：保持单 agent，代码层面做职责分离（现有的 `tools.py` / `serializer.py` / `generator.py` / `schema.py` 已是此思路）
- **拆分信号**（出现以下情况时启动）：
  - System prompt 超过 ~4000 token，LLM 开始丢失指令
  - 不同任务需要不同 model（如 Cypher 生成用强推理模型，格式化用轻量模型降成本）
  - 新增职责（如 GraphRAG 文档抽取）与现有查询链路完全正交
- **拆分顺序**：Answer Agent 最容易先剥离（输入/输出接口最清晰），其次是 Graph Query Agent
