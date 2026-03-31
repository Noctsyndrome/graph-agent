from __future__ import annotations

import time

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from kgqa.agent import close_all_kgqa_agents, get_kgqa_agent
from kgqa.config import get_settings
from kgqa.llm import LLMClient, close_all_llm_clients
from kgqa.models import ChatRequest
from kgqa.query import Neo4jExecutor, close_all_neo4j_drivers, load_seed_data
from kgqa.schema import SchemaRegistry
from kgqa.session import clear_sessions, get_session_payload, list_sessions

settings = get_settings()
app = FastAPI(title="kg-qa-poc", version="0.1.0")
_LLM_STATUS_CACHE: dict[str, object] = {
    "checked_at": 0.0,
    "payload": None,
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_app_url, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_llm_status_payload(force: bool = False) -> dict[str, object]:
    now = time.time()
    cached = _LLM_STATUS_CACHE.get("payload")
    checked_at = float(_LLM_STATUS_CACHE.get("checked_at", 0.0))
    if not force and cached and now - checked_at < 60:
        return dict(cached)

    if not settings.has_llm:
        payload = {
            "configured": False,
            "connected": False,
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "latency_ms": None,
            "detail": "LLM 配置不可用",
            "checked_at": now,
        }
    else:
        client = LLMClient(settings)
        started = time.perf_counter()
        try:
            response = client.generate("只回复 OK", system_prompt="你是连通性检查助手，只能回复 OK。")
            latency_ms = int((time.perf_counter() - started) * 1000)
            payload = {
                "configured": True,
                "connected": True,
                "base_url": settings.llm_base_url,
                "model": settings.llm_model,
                "latency_ms": latency_ms,
                "detail": response.content,
                "checked_at": now,
            }
        except Exception as exc:
            payload = {
                "configured": True,
                "connected": False,
                "base_url": settings.llm_base_url,
                "model": settings.llm_model,
                "latency_ms": None,
                "detail": str(exc),
                "checked_at": now,
            }

    _LLM_STATUS_CACHE["checked_at"] = now
    _LLM_STATUS_CACHE["payload"] = payload
    return dict(payload)


@app.on_event("startup")
def startup_event() -> None:
    executor = Neo4jExecutor(settings)
    executor.warmup()
    get_kgqa_agent(settings)


@app.on_event("shutdown")
def shutdown_event() -> None:
    clear_sessions()
    close_all_kgqa_agents()
    close_all_llm_clients()
    close_all_neo4j_drivers()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "dataset": settings.dataset_name,
        "llm_configured": settings.has_llm,
        "llm_model": settings.llm_model,
    }


@app.get("/llm/status")
def llm_status(force: bool = False) -> dict[str, object]:
    return _get_llm_status_payload(force=force)


@app.get("/schema")
def schema_summary() -> dict[str, object]:
    return SchemaRegistry(settings, domain=get_kgqa_agent(settings).domain).summary()


@app.get("/examples")
def examples() -> dict[str, object]:
    scenarios = yaml.safe_load(settings.evaluation_file.read_text(encoding="utf-8")) or {}
    return scenarios


@app.post("/seed/load")
def seed_load() -> dict[str, str]:
    try:
        load_seed_data(settings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    clear_sessions()
    close_all_kgqa_agents()
    _LLM_STATUS_CACHE["checked_at"] = 0.0
    _LLM_STATUS_CACHE["payload"] = None
    return {"status": "loaded"}


@app.get("/chat/sessions")
def chat_sessions() -> list[dict[str, object]]:
    return [item.model_dump() for item in list_sessions()]


@app.get("/chat/{session_id}/messages")
def chat_session_messages(session_id: str) -> dict[str, object]:
    payload = get_session_payload(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return payload.model_dump()


@app.post("/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    agent = get_kgqa_agent(settings)

    def event_stream() -> object:
        for event in agent.stream_chat(request):
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main() -> None:
    import uvicorn

    uvicorn.run("kgqa.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
