# Stage 04-1 Agent-Only Cutover

## Summary

本阶段完成了仓库从历史线性问答链路到 `agent-only` 基线的硬切换。当前运行时只保留：

- FastAPI Agent 主路径：`/chat`
- 会话恢复接口：`/chat/sessions`、`/chat/{session_id}/messages`
- 图谱与运行状态辅助接口：`/health`、`/llm/status`、`/schema`、`/examples`
- Vite/React + `assistant-ui` 前端

旧的 `/query`、`/query/jobs`、`KGQAService`、`IntentRouter`、`QueryPlanner`、`CypherGenerator`、Streamlit 页面和对应测试/配置已经从运行时移除。

## Completed Changes

### 1. Legacy Path Removal

- 删除旧 public API：
  - `POST /query`
  - `POST /query/jobs`
  - `GET /query/jobs/{job_id}`
- 删除旧线性主链路文件：
  - [service.py](/C:/Code/kg-qa-poc/src/kgqa/service.py)
  - [router.py](/C:/Code/kg-qa-poc/src/kgqa/router.py)
  - [planner.py](/C:/Code/kg-qa-poc/src/kgqa/planner.py)
- 删除旧 Streamlit 页面：
  - [app.py](/C:/Code/kg-qa-poc/ui/app.py)
- 删除不再参与运行时的 few-shot 资产：
  - [few_shots.yaml](/C:/Code/kg-qa-poc/data/few_shots.yaml)

### 2. Agent Runtime Consolidation

- [api.py](/C:/Code/kg-qa-poc/src/kgqa/api.py) 只保留 agent 与状态相关接口
- [query.py](/C:/Code/kg-qa-poc/src/kgqa/query.py) 只保留当前仍被 agent 复用的底层能力：
  - `Neo4jExecutor`
  - `CypherSafetyValidator`
  - `DomainRegistry`
  - `load_seed_data`
- [schema.py](/C:/Code/kg-qa-poc/src/kgqa/schema.py) 不再加载 few-shot 运行配置
- [config.py](/C:/Code/kg-qa-poc/src/kgqa/config.py) 移除了 `few_shots_file`
- [cli.py](/C:/Code/kg-qa-poc/src/kgqa/cli.py) 移除了旧 `ask`，仅保留管理命令：
  - `seed-load`
  - `eval-run`

### 3. Frontend Baseline Alignment

- 当前前端已完成从 `CopilotKit` 到 `assistant-ui` 的迁移
- 右侧详情面板改为由线程中的工具调用 chip 驱动
- 结果与工具详情只展示当前选中调用的上下文，不再混入全局事件流

### 4. Test And Evaluation Cleanup

- 删除旧 `/query` e2e 与旧线性链路测试：
  - [test_e2e.py](/C:/Code/kg-qa-poc/tests/test_e2e.py)
  - [test_multistep.py](/C:/Code/kg-qa-poc/tests/test_multistep.py)
- 保留并补充 agent-only 最小测试集：
  - [test_chat_api.py](/C:/Code/kg-qa-poc/tests/test_chat_api.py)
  - [test_agent_tools.py](/C:/Code/kg-qa-poc/tests/test_agent_tools.py)
  - [test_eval_helpers.py](/C:/Code/kg-qa-poc/tests/test_eval_helpers.py)
- [run_eval.py](/C:/Code/kg-qa-poc/eval/run_eval.py) 改为直接消费 agent 执行结果，不再依赖 `KGQAService`

### 5. Public Documentation Update

- [README.md](/C:/Code/kg-qa-poc/README.md) 已更新为当前基线：
  - 以 `/chat` 为唯一问答主路径
  - 以 `Vite + React + assistant-ui` 为当前前端
  - 以本地启动脚本和 agent 会话式接口为主要演示方式

## Current Runtime Surface

### Backend

- `GET /health`
- `GET /llm/status`
- `GET /schema`
- `GET /examples`
- `POST /seed/load`
- `GET /chat/sessions`
- `GET /chat/{session_id}/messages`
- `POST /chat`

### Frontend

- 地址：`http://127.0.0.1:5173`
- 形态：会话列表 + 线程区 + 工具详情 drawer

## Notes

- 历史 `stage-03-*` 文档保留为阶段记录，不追溯性改写。
- `kg-qa-poc-spec.md` 中关于 `Streamlit`、`few_shots.yaml`、`/query` 的描述属于最初 PoC 设计背景，不代表当前运行时形态。
- 完整交互体验仍以手动 UI 验证为准；自动验证仅保留 agent-only smoke 和基础工具层验证。
