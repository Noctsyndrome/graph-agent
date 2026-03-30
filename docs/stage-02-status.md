# KG-QA PoC 阶段二完成说明

更新时间：2026-03-30

## 1. 阶段目标

本阶段目标是将项目从“规则版可运行 PoC”推进为“LLM 主导 PoC”，重点包括：

- 将意图识别、查询规划、Cypher 生成、回答生成逐步切到 LLM 主路径
- 增加执行链路可观测性，明确每次请求各阶段的来源、耗时和 fallback
- 修复多步场景中的步骤衔接、字段漂移和替代方案查询稳定性
- 改善本机演示体验，包括 Streamlit 页面、调试信息和预置问题
- 优化 Neo4j 访问方式，消除重复冷连接带来的额外延迟

## 2. 本阶段已完成内容

### 2.1 LLM 主导链路

- 已接入阿里云千问兼容接口，并在本机验证调用成功
- LLM 请求已显式关闭深度思考模式：
  - `enable_thinking = false`
- 当前主链路行为：
  - 意图识别：LLM 优先，规则 fallback
  - 多步规划：LLM 优先，规则 fallback
  - Cypher 生成：LLM 优先，规则 fallback
  - 回答生成：LLM 优先，模板 fallback

相关实现：

- [llm.py](/C:/Code/kg-qa-poc/src/kgqa/llm.py)
- [router.py](/C:/Code/kg-qa-poc/src/kgqa/router.py)
- [planner.py](/C:/Code/kg-qa-poc/src/kgqa/planner.py)
- [query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py)
- [generator.py](/C:/Code/kg-qa-poc/src/kgqa/generator.py)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)

### 2.2 Trace / Source 可观测性

- 新增统一的阶段追踪模型，接口返回中包含完整 `trace`
- 当前可追踪信息包括：
  - `intent.source / reason / latency_ms`
  - `plan.source / strategy / steps`
  - `cypher.source / text / valid / attempts`
  - `answer.source`
  - `fallbacks[]`
  - `query_row_count`
  - `total_latency_ms`

相关实现：

- [models.py](/C:/Code/kg-qa-poc/src/kgqa/models.py)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)
- [api.py](/C:/Code/kg-qa-poc/src/kgqa/api.py)

### 2.3 多步场景修复

已重点修复“最低能效设备 + 替代方案”一类多步问题：

- planner 不再允许产出不可直接执行的嵌套 `MULTI_STEP` 子步骤
- LLM 计划中的子步骤会规范化为可直接执行的 `CROSS_DOMAIN / AGGREGATION / SINGLE_DOMAIN`
- 步骤间上下文已支持字段别名和英文占位符：
  - 如 `设备名称 / 设备型号 / 型号 / name`
- 多步结果合并逻辑已支持：
  - `型号`
  - `可替代型号`
  - `可替代设备`
- 替代方案查询的 `CAN_REPLACE` 方向约束已增强
- 对“替代方案类空结果”的判定更细化，减少误判

当前多步问题已能正确返回：

- 第一步定位目标设备
- 第二步定位可替代方案
- 最终在回答和结果预览中合并展示

### 2.4 合规检查场景修复

已修复“2023年后的项目中，有没有还在用 R-22 制冷剂设备”场景中的两类问题：

- 修正时间范围生成约束：
  - 避免错误生成 `{start_date: '>2023'}`
  - 明确使用 `WHERE p.start_date >= date('2024-01-01')`
- 修复 API 响应序列化错误：
  - Neo4j `Date` 类型统一转为字符串，避免 FastAPI 返回 `500`

当前该场景可正确返回命中结果，并在结果中展示：

- 项目
- 城市
- 开始日期
- 型号
- 品牌
- 制冷剂

### 2.5 Neo4j 访问优化

- 引入进程级 Neo4j driver 复用
- API 启动时执行 warm-up
- 支持统一关闭 driver cache
- `EXPLAIN` 校验已做成配置项，并默认关闭：
  - `NEO4J_VALIDATE_WITH_EXPLAIN=false`

相关实现：

- [config.py](/C:/Code/kg-qa-poc/src/kgqa/config.py)
- [query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py)
- [api.py](/C:/Code/kg-qa-poc/src/kgqa/api.py)

### 2.6 Streamlit 演示面增强

- 新增按类型分组的预置问题：
  - 单域查询
  - 跨域查询
  - 聚合统计
  - 多步推理
  - 挑战问题
- 新增页面级系统信息：
  - API 状态
  - 数据集
  - 实体数
  - 关系数
- 新增更完整的执行呈现：
  - 意图识别详情
  - 执行策略
  - 各阶段来源、耗时、尝试次数
  - fallback 明细
  - 执行计划
  - Cypher
  - 结果预览
  - 原始 trace JSON

相关实现：

- [app.py](/C:/Code/kg-qa-poc/ui/app.py)

### 2.7 测试与评估

- 将测试集拆分为：
  - `baseline`
  - `challenge`
- 新增多步专项测试文件：
  - [test_multistep.py](/C:/Code/kg-qa-poc/tests/test_multistep.py)
- 当前已补充覆盖：
  - 嵌套多步计划校验
  - 多步结果合并
  - 字段别名映射
  - 过滤条件规范化
  - 时间比较非法写法校验
  - Neo4j 日期类型归一化

## 3. 当前阶段结论

阶段二已经完成了从“规则优先”向“LLM 主路径”迁移的核心骨架搭建，并且把两个关键高风险场景修到了可演示状态：

- 多步推理场景
- 合规检查场景

同时，系统现在已经具备更好的可观测性，可以在 API 和 Streamlit 中直接看到：

- 本次请求哪些阶段用了 LLM
- 哪些阶段发生了 fallback
- 生成了什么计划
- 实际执行了什么 Cypher
- 各阶段分别花了多久

## 4. 当前边界与问题

- 虽然主路径已改为 LLM 优先，但部分场景仍会落到规则 fallback
- 不同轮次下，LLM 对字段命名、返回列名、语义细节仍可能存在漂移
- 多步和合规场景的正确性明显提升，但整体响应时间仍偏高
- 当前还没有把“步骤级原始 prompt / raw response”完整展示在 UI 中
- 图谱领域仍以空调设备为主，尚未验证到电梯等其他设备域

## 5. 下一阶段建议

- 继续减少 LLM 场景中的 fallback 发生率
- 补充步骤级调试视图：
  - 子问题
  - 子阶段耗时
  - 原始 LLM 输出
- 优化回答生成，让合规/审计类问题默认输出更强的“证据型结果”
- 继续收敛延迟，优先压缩多次串行 LLM 调用带来的响应时间
- 在不依赖空调特定词表的前提下，验证 schema/few-shot 组织方式的可扩展性
