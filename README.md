# kg-qa-poc

知识图谱智能问答演示验证项目，包含 FastAPI 后端、Neo4j 图数据库和 Vite/React 前端。

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
python -m kgqa.cli seed-load --scenario elevator
python -m kgqa.cli seed-load --scenario property
```

## 5. 启动本地服务

推荐直接使用统一启动脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_local_services.ps1
```

停止本地服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_local_services.ps1
```

默认地址：

- API：[http://127.0.0.1:8000](http://127.0.0.1:8000)
- Frontend：[http://127.0.0.1:5173](http://127.0.0.1:5173)

运行日志写入：

- `logs/api_*.out.log`
- `logs/api_*.err.log`
- `logs/frontend_*.out.log`
- `logs/frontend_*.err.log`

## 6. 直接运行 API / Frontend

API：

```powershell
uvicorn kgqa.api:app --reload
```

Frontend：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

默认读取 `VITE_KGQA_API_BASE_URL`，未配置时访问 `http://127.0.0.1:8000`。

## 7. 常用 CLI 命令

```powershell
python -m kgqa.cli seed-load
python -m kgqa.cli seed-load --scenario elevator
python -m kgqa.cli seed-load --scenario property
python -m kgqa.cli eval-run
python -m kgqa.cli eval-run --scenario elevator
python -m kgqa.cli eval-run --scenario property
```

## 8. 测试与构建

```powershell
pytest
pytest tests/test_chat_api.py
pytest tests/test_hardening.py
python -m kgqa.cli eval-run
```

前端构建：

```powershell
cd frontend
npm run build
```
