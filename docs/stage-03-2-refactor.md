# KG-QA PoC 阶段三重构进展说明

更新时间：2026-03-30

## 1. 文档定位

本文件用于记录阶段三执行过程中的实际重构进展。

- 规划文档仍以 [stage-03-plan.md](/C:/Code/kg-qa-poc/docs/stage-03-plan.md) 为准
- 本文件不替代规划文档，只补充“当前已经做到哪里”
- [report.html](/C:/Code/kg-qa-poc/eval/report.html) 保留此前的双模式评估结果，作为重构决策参考，不在本轮重构中覆盖

## 2. 当前重构背景

在阶段三前半程，我们已经完成过以下验证动作：

- 纯 LLM 模式评估
- 双模式对比评估
- 基于评估结果确认：纯 LLM 主路径已经具备较好的可用性

在此基础上，当前代码基线开始进入“移除规则逻辑”的重构阶段。也就是说：

- `FORCE_LLM`
- 双模式评估入口
- 规则 fallback 主路径

这些能力在决策验证阶段曾经存在并被使用，但在本轮重构中已不再作为长期运行能力保留。

## 3. 当前已完成的重构内容

### 3.1 主执行链路已切为纯 LLM 路径

当前核心链路已经不再依赖规则 fallback：

- 意图识别走 LLM
- 多步规划走 LLM
- Cypher 生成走 LLM
- 回答生成走 LLM

相关代码：

- [router.py](/C:/Code/kg-qa-poc/src/kgqa/router.py)
- [planner.py](/C:/Code/kg-qa-poc/src/kgqa/planner.py)
- [query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py)
- [generator.py](/C:/Code/kg-qa-poc/src/kgqa/generator.py)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)

### 3.2 规则残留主干已被移除

截至当前版本，源代码主路径中已不再保留以下典型规则接口：

- `classify_with_rules`
- `plan_with_rules`
- `generate_with_rules`
- `compose_with_template`
- 规则 fallback 记录链路

同时，trace 模型也已同步收口为 LLM 主路径表达，不再保留规则来源枚举。

相关代码：

- [models.py](/C:/Code/kg-qa-poc/src/kgqa/models.py)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)

### 3.3 领域硬编码解耦已推进

#### 3.3.1 实体枚举值动态化

已引入 `DomainRegistry`，在运行时从 Neo4j 读取领域值，而不是在代码中维护固定名单。

当前已动态加载：

- 客户
- 品牌
- 城市
- 项目类型
- 设备类别
- 制冷剂

相关代码：

- [query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py)

#### 3.3.2 多步上下文字段别名配置化

多步问题中的上下文字段映射，已改为从 schema 配置读取，而不是在 service 中硬编码。

当前别名定义已放入：

- [schema.yaml](/C:/Code/kg-qa-poc/data/schema.yaml)

当前已覆盖：

- 项目
- 型号
- 品牌
- 能效比
- 制冷量

相关代码：

- [schema.yaml](/C:/Code/kg-qa-poc/data/schema.yaml)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)

#### 3.3.3 few-shot 评分已开始转向 schema/domain 驱动

few-shot 选择权重已经不再完全写死在代码里，而是开始基于以下信息自动派生：

- schema 实体描述
- 关系名
- 字段别名
- domain 中的类别值、品牌值、制冷剂值、项目类型值

同时，`schema.yaml` 中新增了评分配置项：

- `few_shot_scoring`

相关代码：

- [schema.py](/C:/Code/kg-qa-poc/src/kgqa/schema.py)
- [schema.yaml](/C:/Code/kg-qa-poc/data/schema.yaml)

### 3.4 UI 演示面已做三轮增强

#### 第一轮

- 支持按 `baseline / challenge / generalization` 分组选择测试用例
- 显示意图、策略、Cypher、执行计划、trace 等关键调试信息

#### 第二轮

- 优化系统状态请求机制
- 切换用例时不再自动触发 `/health` 和 `/schema`
- 改为首次加载一次，之后通过“刷新系统状态”按钮显式刷新

相关代码：

- [app.py](/C:/Code/kg-qa-poc/ui/app.py)

#### 第三轮

- 页面顶部新增 LLM 连接状态展示
- 显示当前模型、网关地址、连通耗时
- 查询执行时改为分阶段进度展示，不再只有统一的“请稍候”提示
- 单步问题可看到 `意图识别 -> Cypher -> 最终回答` 的实时推进
- 多步问题可看到按步骤推进的 Cypher 执行状态

这一轮还同时收紧了 UI 到本地 API 的调用方式：

- 本地 API 请求改为复用 `httpx.Client`
- 本地 API 请求显式 `trust_env=False`，避免 `localhost` 请求被系统代理链路拖慢
- 页面首次加载时不再强制真实探活 LLM，改为优先读取缓存；手动点击“刷新系统状态”时再强制探活

相关代码：

- [app.py](/C:/Code/kg-qa-poc/ui/app.py)
- [api.py](/C:/Code/kg-qa-poc/src/kgqa/api.py)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)

### 3.5 性能优化已进入连接复用与服务复用阶段

围绕“纯 LLM 主路径”的时延问题，当前已完成两项关键优化：

- LLM 调用改为复用进程级 `httpx.Client`
- `KGQAService` 改为进程级共享，并在 FastAPI 启动时预热

优化后的效果：

- 最小 LLM 请求不再因为每次新建连接而落入多秒级
- 真实问答链路的服务端业务耗时已压缩到秒级区间
- 首次查询的冷启动成本被前移到服务启动阶段，而不是由用户首条问题承担

相关代码：

- [llm.py](/C:/Code/kg-qa-poc/src/kgqa/llm.py)
- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)
- [api.py](/C:/Code/kg-qa-poc/src/kgqa/api.py)

### 3.6 多步与合规场景修复已保留在当前基线上

当前纯 LLM 基线上，阶段二中修复过的关键复杂场景能力仍然保留：

- “最低能效设备 + 替代方案”多步场景
- “2023 年后是否仍使用 R-22”合规检查场景
- Neo4j 日期类型统一序列化为字符串，避免 API 500

相关代码：

- [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)
- [query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py)
- [test_multistep.py](/C:/Code/kg-qa-poc/tests/test_multistep.py)

## 4. 当前保留但未在本轮重构中覆盖的内容

### 4.1 评估报告文件保留旧结果

[report.html](/C:/Code/kg-qa-poc/eval/report.html) 当前保留的是此前双模式评估产物，用于说明：

- 纯 LLM 模式已达到可接受水平
- 规则逻辑具备被移除的依据

本轮重构中没有重新覆盖该文件，目的是保留决策依据。

### 4.2 阶段三计划文档保持不动

[stage-03-plan.md](/C:/Code/kg-qa-poc/docs/stage-03-plan.md) 当前内容保持不变，用于保留原始实施方案和阶段三的完整背景。

### 4.3 日志目录开始独立整理

为避免仓库根目录持续堆积运行日志，当前已新增独立的 `logs/` 目录，用于后续归拢本地运行日志。

同时已补充本地启动/停止脚本，后续通过仓库内脚本启动服务时，日志默认写入 `logs/`，不再散落在根目录。

本轮提交只包含目录与脚本，不包含任何实际日志内容。

## 5. 当前仍在进行中的事项

截至本文件更新时，仍处于“重构收口中”，主要包括：

- 由人工通过 UI 继续验证关键用例
- 根据后续验证结果，继续收口剩余体验细节

## 6. 当前结论

阶段三当前已经完成了最关键的一步：把系统运行基线从“规则兜底的混合模式”推进到“纯 LLM 主路径”。

同时，围绕这一基线的配套工作也已同步推进：

- 动态领域值加载
- schema 驱动别名映射
- few-shot 评分去硬编码
- UI 状态请求优化

当前代码更接近我们真正要验证的目标：

`让知识图谱问答能力主要由 LLM + Schema + 图查询本身驱动，而不是由硬编码规则模板驱动。`
