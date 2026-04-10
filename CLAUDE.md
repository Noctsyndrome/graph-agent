# CLAUDE.md

This file records the current state of this repository for Claude Code.

## What This Is

`kg-qa-poc` is a Chinese knowledge-graph QA demo built around a single agent-driven chat path:

- FastAPI backend with SSE chat streaming
- Neo4j as the graph store
- OpenAI-compatible LLM backend
- React/Vite frontend for multi-session chat

The runtime is agent-only. User QA goes through `POST /chat`; there is no separate pipeline or Streamlit runtime path.

## Current Local Commands

```powershell
# Prerequisites
docker compose up -d neo4j
pip install -e .

# Seed data
python -m kgqa.cli seed-load
python -m kgqa.cli seed-load --scenario elevator
python -m kgqa.cli seed-load --scenario property

# Evaluation
python -m kgqa.cli eval-run
python -m kgqa.cli eval-run --scenario elevator
python -m kgqa.cli eval-run --scenario property

# API
uvicorn kgqa.api:app --reload

# Frontend
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173

# Combined local start/stop
powershell -ExecutionPolicy Bypass -File .\scripts\start_local_services.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop_local_services.ps1

# Tests
pytest
pytest tests/test_chat_api.py
pytest tests/test_hardening.py

# Frontend build
cd frontend
npm run build
```

## Current Runtime Architecture

### Backend entrypoint

- Main app: `src/kgqa/api.py`
- FastAPI exposes health, scenario, schema, seed-load, session, and chat endpoints
- `/chat` streams SSE events from `KGQAAgent.stream_chat()`

### Agent loop

- Core agent: `src/kgqa/agent.py`
- The agent runs a bounded ReAct-style loop and records tool execution history in `state["toolHistory"]`
- The normal query path is:
  - `get_schema_context`
  - optional `inspect_recent_executions`
  - `plan_query`
  - `validate_cypher`
  - `execute_cypher`
  - `format_results`
- `plan_query` is required before the first `validate_cypher` in each turn
- `plan_query` may return `needs_clarification = true`; in that case the turn ends with a clarification prompt instead of executing Cypher
- `inspect_recent_executions` is only for reading prior successful execution history; it is optional and not a mandatory precursor to `plan_query`
- Public session state is filtered by `_public_state()` before persistence; internal fields such as `_latest_rows`, `_latest_graph_delta`, and `_budget` are excluded

### Tool chain

Primary tools live in `src/kgqa/tools.py`:

1. `get_schema_context`
2. `list_domain_values`
3. `inspect_recent_executions`
4. `plan_query`
5. `validate_cypher`
6. `execute_cypher`
7. `format_results`

Cypher execution is read-only and validated before execution.

### Multi-scenario support

Scenarios are defined in `src/kgqa/scenario.py`. Current registered scenarios are:

- `hvac`
- `elevator`
- `property`

Each scenario carries its own dataset name, schema file, seed file, and evaluation file. Scenario selection is locked per chat session.

### Session persistence

- Session storage lives in `src/kgqa/session.py`
- Sessions are persisted in SQLite, not in memory
- Default DB path: `data/sessions.db`
- Storage includes:
  - `session_id`
  - `title`
  - `scenario_id`
  - `scenario_label`
  - `dataset_name`
  - `messages`
  - `state`
  - `status`
  - timestamps
- Server shutdown closes the SQLite connection
- `POST /seed/load` clears sessions for the loaded scenario
- Individual sessions can be deleted through `DELETE /chat/{session_id}`
- `toolHistory` persists recent tool observations, including `plan_query` and successful execution records used by follow-up turns

## Current API Surface

Implemented endpoints in `src/kgqa/api.py`:

- `GET /health`
- `GET /llm/status`
- `GET /scenarios`
- `GET /schema`
- `GET /schema/graph`
- `GET /examples`
- `POST /seed/load`
- `GET /chat/sessions`
- `GET /chat/{session_id}/messages`
- `DELETE /chat/{session_id}`
- `POST /chat`

## Schema and Graph Support

- Schema loading and rendering live in `src/kgqa/schema.py`
- `SchemaRegistry.summary()` powers `/schema`
- `SchemaRegistry.graph_data()` powers `/schema/graph`
- `SchemaRegistry.extract_active_types()` extracts entity and relationship hits from generated Cypher
- Successful `execute_cypher` calls append `graph_delta` to the matching `toolHistory` item

## Frontend

Frontend code is under `frontend/src/`.

Current frontend stack:

- React 19
- Vite
- `@assistant-ui/react`
- `react-force-graph-2d`
- Radix UI primitives

Current UI capabilities:

- Multi-session chat list with delete support
- Session restore from persisted backend history
- Scenario picker for new sessions
- Tool detail drawer for tool call inspection
- Right-side schema graph panel
- Active schema type highlighting driven by backend `graph_delta`
- Resizable chat/graph split layout
- Collapsible left sidebar

The graph panel currently visualises the schema graph, not a reconstructed instance/query graph.

## Data and File Layout

Key project areas:

- `src/kgqa/` — backend runtime
- `frontend/` — React frontend
- `data/` — schema YAML, seed Cypher, SQLite session DB
- `tests/` — pytest coverage
- `docs/` — stage planning and implementation notes
- `scripts/` — local start/stop and data helper scripts

Important schema/data files:

- `data/schema.yaml`
- `data/schema_elevator.yaml`
- `data/schema_property.yaml`
- `data/seed_data.cypher`
- `data/seed_data_elevator.cypher`
- `data/seed_data_property.cypher`

Important evaluation files:

- `tests/test_scenarios.yaml`
- `tests/test_scenarios_elevator.yaml`
- `tests/test_scenarios_property.yaml`

## Current Conventions

- Source package: `kgqa`
- Entry points: `kgqa.api:main`, `kgqa.cli:main`
- User-facing prompts and UI copy are Chinese
- Session summaries include `message_count`, but frontend session cards no longer display it
- Session history and graph highlight recovery rely on persisted backend session state, not frontend-only memory
- Schema graph state in the frontend is driven by `/schema/graph` plus persisted `toolHistory.graph_delta`
