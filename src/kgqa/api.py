from __future__ import annotations

from fastapi import FastAPI, HTTPException

from kgqa.config import get_settings
from kgqa.models import QueryRequest, QueryResponse
from kgqa.service import KGQAService

settings = get_settings()
app = FastAPI(title="kg-qa-poc", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "dataset": settings.dataset_name}


@app.get("/schema")
def schema_summary() -> dict[str, object]:
    service = KGQAService(settings)
    return service.schema.summary()


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    service = KGQAService(settings)
    try:
        return service.process_question(request.question)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/seed/load")
def seed_load() -> dict[str, str]:
    service = KGQAService(settings)
    try:
        service.load_seed_data()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "loaded"}


def main() -> None:
    import uvicorn

    uvicorn.run("kgqa.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()

