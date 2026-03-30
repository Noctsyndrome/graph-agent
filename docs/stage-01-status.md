# KG-QA PoC 阶段性完成说明

更新时间：2026-03-30

## 1. 当前阶段目标

本阶段目标是从零搭建一个可在本机运行的知识图谱智能问答 PoC，优先打通完整链路：

- 模拟图谱数据生成与导入
- 自然语言问题接入
- 查询路由、查询规划、Cypher 执行
- 查询结果序列化与最终回答输出
- FastAPI / CLI / Streamlit 演示入口
- 自动化测试与评估报告

## 2. 已完成内容

### 2.1 工程与运行环境

- 初始化 Git 仓库
- 建立 Python 项目结构，使用 `pyproject.toml` 管理依赖
- 提供 `Dockerfile` 和 `docker-compose.yml`
- 提供 `.env.example`，并完成本机 `.env` 配置模板
- 在本机验证可运行环境：
  - Python 3.13
  - Docker Desktop
  - Neo4j Community

### 2.2 图谱数据层

- 定义图谱 Schema：[schema.yaml](/C:/Code/kg-qa-poc/data/schema.yaml)
- 编写 few-shot 示例库：[few_shots.yaml](/C:/Code/kg-qa-poc/data/few_shots.yaml)
- 实现种子数据生成脚本：[generate_seed_data.py](/C:/Code/kg-qa-poc/scripts/generate_seed_data.py)
- 实现种子数据导入脚本：[load_seed_data.py](/C:/Code/kg-qa-poc/scripts/load_seed_data.py)
- 已成功导入演示数据到 Neo4j，覆盖：
  - 10 个客户
  - 30 个项目
  - 20 个设备类型
  - 50 个设备型号
  - 200 条安装记录
  - 替代关系 `CAN_REPLACE`

### 2.3 应用链路

- 实现配置管理：[config.py](/C:/Code/kg-qa-poc/src/kgqa/config.py)
- 实现意图路由：[router.py](/C:/Code/kg-qa-poc/src/kgqa/router.py)
- 实现查询规划：[planner.py](/C:/Code/kg-qa-poc/src/kgqa/planner.py)
- 实现 Cypher 生成、安全校验与 Neo4j 执行：[query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py)
- 实现结果序列化：[serializer.py](/C:/Code/kg-qa-poc/src/kgqa/serializer.py)
- 实现最终回答生成：[generator.py](/C:/Code/kg-qa-poc/src/kgqa/generator.py)
- 实现统一服务编排：[service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)

### 2.4 接入面

- 提供 FastAPI 接口：[api.py](/C:/Code/kg-qa-poc/src/kgqa/api.py)
  - `GET /health`
  - `GET /schema`
  - `POST /query`
  - `POST /seed/load`
- 提供 CLI 入口：[cli.py](/C:/Code/kg-qa-poc/src/kgqa/cli.py)
  - `ask`
  - `seed-load`
  - `eval-run`
- 提供 Streamlit 页面：[app.py](/C:/Code/kg-qa-poc/ui/app.py)
  - 已完成与 FastAPI 的本机联调
  - 页面可以展示意图、Cypher、最终回答和结果预览

### 2.5 测试与评估

- 编写测试场景：[test_scenarios.yaml](/C:/Code/kg-qa-poc/tests/test_scenarios.yaml)
- 编写端到端测试：[test_e2e.py](/C:/Code/kg-qa-poc/tests/test_e2e.py)
- 编写评估报告脚本：[run_eval.py](/C:/Code/kg-qa-poc/eval/run_eval.py)
- 当前结果：
  - `pytest` 通过
  - 20 条评估用例通过率 100%
  - 评估报告已生成到 `eval/report.html`

## 3. 当前 LLM 生效范围

本阶段已经接入阿里云千问兼容接口，并验证调用成功。

当前各环节的行为模式如下：

- 意图识别：规则驱动
- Cypher 生成：规则优先，LLM 兜底
- 多步规划：LLM 优先，规则兜底
- 回答生成：LLM 优先，模板兜底

因此，当前版本已经不是纯规则系统，但也还没有完全切换到“LLM 主导”的查询与规划模式。

## 4. 当前版本的边界与限制

- 意图识别仍然主要依赖静态规则
- 大多数已覆盖场景仍优先走规则生成 Cypher
- 评估集当前与现有数据和规则高度匹配，主要用于验证主链路通畅
- 目前图谱领域仍以空调设备为主，尚未抽象成更通用的跨品类能力
- 查询结果虽然真实来自 Neo4j，但泛化能力还不足以覆盖开放业务问法

## 5. 下一阶段建议

下一阶段应把重点从“规则版可运行 PoC”转向“LLM 主导 PoC”：

- 将意图识别改成 LLM 主导，规则仅作 fallback
- 将 NL2Cypher 改成 LLM 主导，规则模板仅覆盖极少数保底问题
- 将多步规划进一步统一到 LLM 规划结果
- 扩大测试集，加入数据变动与跨品类问题
- 针对空调、电梯等不同设备域，验证 Schema 注入和 few-shot 迁移能力
- 增加执行链路可观测性，明确每次请求在哪些环节使用了 LLM
