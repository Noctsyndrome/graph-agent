# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Knowledge-graph QA proof-of-concept: a Chinese-language agent that converts natural language questions into Cypher queries against Neo4j, then synthesises a natural language answer. The runtime is **agent-only** (no pipeline/chain fallback). All user interaction goes through `POST /chat` as an SSE stream.

## Commands

```bash
# Prerequisites: Docker running, .env configured, pip install -e .
docker compose up -d neo4j

# Seed data
python -m kgqa.cli seed-load                    # HVAC (default)
python -m kgqa.cli seed-load --scenario elevator # Elevator

# API server
uvicorn kgqa.api:app --reload

# Frontend (separate terminal)
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173

# Tests
pytest                          # all tests
pytest tests/test_agent_tools.py              # single file
pytest tests/test_agent_tools.py::test_name   # single test

# Evaluation (requires running Neo4j + configured LLM)
python -m kgqa.cli eval-run                     # HVAC
python -m kgqa.cli eval-run --scenario elevator # Elevator
# or directly: python eval/run_eval.py --scenario elevator
# Reports go to eval/report-{scenario}.html
```

## Architecture

### Agent loop (`src/kgqa/agent.py`)

`KGQAAgent.stream_chat()` runs a **max-5-step ReAct loop**. Each step: LLM decides next action (JSON) → tool executes → observation recorded. The loop breaks when the LLM returns `action: "finish"` or `format_results` is called with `auto_finish_after_format=true`. After the loop, if no `format_results` was called, the agent auto-formats the last `execute_cypher` rows. Final answer is composed by `AnswerGenerator` (another LLM call).

Agent instances are **cached** by an 8-tuple key `(neo4j×3, llm×3, dataset_name, schema_file)`. Different scenarios produce different agent instances.

### Tool chain (`src/kgqa/tools.py`)

Five tools, always invoked in this typical order:

1. `get_schema_context` — renders relevant subset of YAML schema as text (focus inference narrows to matching entities)
2. `list_domain_values` — returns distinct enum values from Neo4j per entity/field
3. `validate_cypher` — safety check (read-only, single statement, no forbidden keywords)
4. `execute_cypher` — runs Cypher, normalises Neo4j types to JSON
5. `format_results` — serialises rows to markdown table/key-value/list + infers renderer type

### Multi-scenario system (`src/kgqa/scenario.py`)

Scenarios are registered in `_SCENARIOS` dict. Each defines: `dataset_name`, `schema_file`, `seed_file`, `evaluation_file`. `build_scenario_settings()` overlays scenario paths onto the global `Settings`. Neo4j data isolation is via `n.dataset` property on every node.

Current scenarios: `hvac` (HVAC chillers), `elevator` (building elevators — Phase 1 of stage-04-3).

### Schema-driven domain loading (`src/kgqa/query.py` DomainRegistry)

`DomainRegistry.load()` iterates `schema.yaml → entities → filterable_fields` and runs `MATCH (n:{Entity}) RETURN DISTINCT n.{field}` for each. Fields named `id`, `dataset`, or ending in `_id` are skipped. This is the Phase 1 generalisation — no more hardcoded property queries.

### Key data files

- `data/schema.yaml` / `data/schema_elevator.yaml` — entity/relationship/path definitions per scenario
- `data/seed_data.cypher` / `data/seed_data_elevator.cypher` — Cypher CREATE scripts
- `tests/test_scenarios.yaml` / `tests/test_scenarios_elevator.yaml` — eval cases (baseline/challenge/generalization groups)

## Conventions

- All source is under `src/kgqa/`, package is `kgqa`. Entry points: `kgqa.cli:main`, `kgqa.api:main`.
- All prompts and UI text are in **Chinese**. LLM system prompts, tool descriptions, serialised output — all zh-CN.
- Cypher safety is enforced by `CypherSafetyValidator` — never bypass it. All user-facing queries must be read-only.
- Sessions are in-memory only (`session.py`), lost on restart.
- The evaluation framework (`eval/run_eval.py`) uses `must_include` keyword matching with `column_aliases` from schema YAML for flexible assertion.

## Known Hardcoded Dependencies (Phase 2 blockers)

These exist in current code and need generalisation for non-isomorphic schemas:

- `schema.py:96-100` — hardcoded Chinese keywords per entity (`Customer→客户`, `Model→设备,型号,品牌`, etc.)
- `schema.py:104-113` — calls `.customers`, `.brands`, `.cities` etc. by name from DomainRegistry
- `query.py:224-232` — `_resolve_alias` maps like `"refrigerants"→("Model","refrigerant")`
- `tools.py:133-141` — `_infer_intent` uses hardcoded Chinese keywords for intent classification
