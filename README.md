# kg-qa-poc

知识图谱智能问答演示验证项目。当前实现目标是从零在本机快速搭建一套可运行的 PoC，覆盖：

- 模拟图谱数据生成与导入
- FastAPI 自研 Agent 与图谱工具链
- `/query` 对照接口与 `/chat` 流式会话接口
- Vite/React + CopilotKit 前端演示页面
- 自动化测试与 HTML 评估报告

## 1. 环境准备

- Windows 11 或兼容环境
- Python 3.11 到 3.13
- Node.js 22 或更高版本
- Docker Desktop
- 可访问的 OpenAI-compatible LLM API

## 2. 本地安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .
Copy-Item .env.example .env
Copy-Item frontend\.env.example frontend\.env
cd frontend
npm install
cd ..
```

按需修改 `.env`：

- `NEO4J_URI`：本机运行 Python 时通常用 `bolt://localhost:7687`
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`：云端模型配置
- `FRONTEND_APP_URL`：默认 `http://127.0.0.1:5173`

## 3. 启动 Neo4j

```powershell
docker compose up -d neo4j
```

Neo4j Browser 默认地址：[http://localhost:7474](http://localhost:7474)

## 4. 生成并导入种子数据

```powershell
python scripts/generate_seed_data.py
python scripts/load_seed_data.py
```

或者使用 CLI：

```powershell
python -m kgqa.cli seed-load
```

## 5. 运行 API

```powershell
uvicorn kgqa.api:app --reload
```

接口：

- `GET /health`
- `GET /schema`
- `POST /query`
- `GET /examples`
- `GET /chat/sessions`
- `GET /chat/{session_id}/messages`
- `POST /chat`
- `POST /seed/load`

## 6. 运行 CLI

```powershell
python -m kgqa.cli ask "万科的项目分别用了哪些品牌的冷水机组？"
python -m kgqa.cli eval-run
```

## 7. 运行 Web UI

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

默认读取 `VITE_KGQA_API_BASE_URL`，未配置时访问 `http://127.0.0.1:8000`。

如果希望同时启动本地 API 和 React 前端，并把运行日志统一写入 `logs/` 目录，推荐使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_local_services.ps1
```

停止本地服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_local_services.ps1
```

运行日志会写入：

- `logs/api_*.out.log`
- `logs/api_*.err.log`
- `logs/frontend_*.out.log`
- `logs/frontend_*.err.log`

## 8. 测试与评估

```powershell
pytest
python eval/run_eval.py
```

报告输出到 `eval/report.html`。

## 9. 当前实现说明

- `/chat` 为当前 Agent 主实验入口，支持流式回复、工具轨迹和会话恢复
- 前端使用 `Vite + React + CopilotKit`，并预留了结构化结果 renderer 壳
- `/query` 仍保留为迁移期对照接口，后续会逐步下线
- Streamlit 页面已不再作为主 UI，仅保留历史参考价值
- 写操作 Cypher 会被拒绝执行
