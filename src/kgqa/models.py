from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    SINGLE_DOMAIN = "SINGLE_DOMAIN"
    CROSS_DOMAIN = "CROSS_DOMAIN"
    AGGREGATION = "AGGREGATION"
    MULTI_STEP = "MULTI_STEP"


class IntentResult(BaseModel):
    intent: IntentType
    confidence: float = 0.0
    reason: str = ""


class QueryPlanStep(BaseModel):
    id: str
    goal: str
    query_type: IntentType
    question: str
    depends_on: list[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    strategy: str
    steps: list[QueryPlanStep] = Field(default_factory=list)


class SerializedResult(BaseModel):
    format: str
    markdown: str
    preview: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    question: str
    intent: IntentType
    strategy: str
    cypher: str | None = None
    plan: QueryPlan | None = None
    result_preview: list[dict[str, Any]] = Field(default_factory=list)
    answer: str
    latency_ms: int


class LLMResponse(BaseModel):
    content: str
    raw: dict[str, Any] = Field(default_factory=dict)

