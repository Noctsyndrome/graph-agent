from __future__ import annotations

import threading
import time
import uuid

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from kgqa.agent import close_all_kgqa_agents, get_kgqa_agent
from kgqa.config import get_settings
from kgqa.llm import LLMClient, close_all_llm_clients
from kgqa.models import ChatRequest, QueryRequest, QueryResponse
from kgqa.query import Neo4jExecutor, close_all_neo4j_drivers
from kgqa.session import clear_sessions, get_session_payload, list_sessions
from kgqa.service import close_all_kgqa_services, get_kgqa_service

settings = get_settings()
app = FastAPI(title="kg-qa-poc", version="0.1.0")
_QUERY_JOB_STORE: dict[str, dict[str, object]] = {}
_QUERY_JOB_LOCK = threading.Lock()
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


def _set_query_job(job_id: str, **fields: object) -> None:
    with _QUERY_JOB_LOCK:
        current = _QUERY_JOB_STORE.get(job_id, {})
        current.update(fields)
        _QUERY_JOB_STORE[job_id] = current


def _run_query_job(job_id: str, question: str) -> None:
    service = get_kgqa_service(settings)
    _set_query_job(job_id, status="running", stage="queued", message="请求已提交，等待执行")

    def progress_callback(stage: str, message: str) -> None:
        _set_query_job(job_id, status="running", stage=stage, message=message, updated_at=time.time())

    try:
        response = service.process_question(question, progress_callback=progress_callback)
        _set_query_job(
            job_id,
            status="completed",
            stage="completed",
            message="知识图谱问答链路执行完成",
            response=response.model_dump(),
            updated_at=time.time(),
        )
    except Exception as exc:
        _set_query_job(
            job_id,
            status="failed",
            stage="failed",
            message=str(exc),
            error=str(exc),
            updated_at=time.time(),
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
    get_kgqa_service(settings)
    get_kgqa_agent(settings)


@app.on_event("shutdown")
def shutdown_event() -> None:
    clear_sessions()
    close_all_kgqa_agents()
    close_all_kgqa_services()
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
    service = get_kgqa_service(settings)
    return service.schema.summary()


@app.get("/examples")
def examples() -> dict[str, object]:
    scenarios = yaml.safe_load(settings.evaluation_file.read_text(encoding="utf-8")) or {}
    return scenarios


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    service = get_kgqa_service(settings)
    try:
        return service.process_question(request.question)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/query/jobs")
def submit_query_job(request: QueryRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    job_id = str(uuid.uuid4())
    created_at = time.time()
    _set_query_job(
        job_id,
        status="queued",
        stage="queued",
        message="请求已提交，等待执行",
        question=request.question,
        created_at=created_at,
        updated_at=created_at,
    )
    background_tasks.add_task(_run_query_job, job_id, request.question)
    return {"request_id": job_id, "status": "queued"}


@app.get("/query/jobs/{job_id}")
def get_query_job(job_id: str) -> dict[str, object]:
    with _QUERY_JOB_LOCK:
        payload = _QUERY_JOB_STORE.get(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Query job not found.")
    return dict(payload)


@app.post("/seed/load")
def seed_load() -> dict[str, str]:
    service = get_kgqa_service(settings)
    try:
        service.load_seed_data()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
