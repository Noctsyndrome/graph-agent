# kg-qa-poc

知识图谱智能问答演示验证项目。当前实现目标是从零在本机快速搭建一套可运行的 PoC，覆盖：

- 模拟图谱数据生成与导入
- 自然语言问题到 Cypher 的规则优先转换
- 多类查询场景的统一执行链路
- FastAPI 接口、CLI 命令行和 Streamlit 演示页面
- 自动化测试与 HTML 评估报告

## 1. 环境准备

- Windows 11 或兼容环境
- Python 3.11 到 3.13
- Docker Desktop
- 可访问的 OpenAI-compatible LLM API

## 2. 本地安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .
Copy-Item .env.example .env
```

按需修改 `.env`：

- `NEO4J_URI`：本机运行 Python 时通常用 `bolt://localhost:7687`
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`：云端模型配置
- 如果暂时不配置 LLM，系统会走内置模板回答和规则规划

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
- `POST /seed/load`

## 6. 运行 CLI

```powershell
python -m kgqa.cli ask "万科的项目分别用了哪些品牌的冷水机组？"
python -m kgqa.cli eval-run
```

## 7. 运行 Web UI

```powershell
streamlit run ui/app.py
```

默认读取 `KGQA_API_BASE_URL`，未配置时访问 `http://localhost:8000`。

如果希望同时启动本地 API 和 Streamlit，并把运行日志统一写入 `logs/` 目录，推荐使用：

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
- `logs/ui_*.out.log`
- `logs/ui_*.err.log`

## 8. Docker Compose 一键运行

如果希望 API 和 UI 也放进容器：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

提示：

- 容器内默认用 `bolt://neo4j:7687`
- 如果你从宿主机直接运行 Python，请把 `NEO4J_URI` 保持为 `bolt://localhost:7687`

## 9. 测试与评估

```powershell
pytest
python eval/run_eval.py
```

报告输出到 `eval/report.html`。

## 10. 当前实现说明

- 单域、跨域、聚合、多步问题都已预置规则优先策略
- 若配置了 LLM，则会优先用于回答润色与复杂多步规划回退
- 无结果时统一返回“图谱中未找到相关信息”
- 写操作 Cypher 会被拒绝执行
