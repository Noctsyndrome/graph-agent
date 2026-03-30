from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    SINGLE_DOMAIN = "SINGLE_DOMAIN"
    CROSS_DOMAIN = "CROSS_DOMAIN"
    AGGREGATION = "AGGREGATION"
    MULTI_STEP = "MULTI_STEP"


class SourceType(str, Enum):
    LLM = "llm"
    NONE = "none"


class IntentResult(BaseModel):
    intent: IntentType
    confidence: float = 0.0
    reason: str = ""
    entities: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    needs_aggregation: bool = False
    needs_multi_step: bool = False


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


class LLMResponse(BaseModel):
    content: str
    raw: dict[str, Any] = Field(default_factory=dict)


class IntentTrace(BaseModel):
    source: SourceType
    reason: str = ""
    latency_ms: int = 0
    confidence: float = 0.0
    entities: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    needs_aggregation: bool = False
    needs_multi_step: bool = False
    attempts: int = 1


class PlanTrace(BaseModel):
    source: SourceType = SourceType.NONE
    reason: str = ""
    latency_ms: int = 0
    strategy: str = ""
    steps: list[QueryPlanStep] = Field(default_factory=list)
    attempts: int = 0


class CypherTrace(BaseModel):
    source: SourceType = SourceType.NONE
    reason: str = ""
    latency_ms: int = 0
    text: str | None = None
    valid: bool = False
    attempts: int = 0


class AnswerTrace(BaseModel):
    source: SourceType
    reason: str = ""
    latency_ms: int = 0
    attempts: int = 1


class ExecutionTrace(BaseModel):
    intent: IntentTrace
    plan: PlanTrace
    cypher: CypherTrace
    answer: AnswerTrace
    query_success: bool = False
    query_row_count: int = 0
    total_latency_ms: int = 0


class QueryResponse(BaseModel):
    question: str
    intent: IntentType
    strategy: str
    cypher: str | None = None
    plan: QueryPlan | None = None
    result_preview: list[dict[str, Any]] = Field(default_factory=list)
    answer: str
    latency_ms: int
    trace: ExecutionTrace
