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
    entities: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    needs_aggregation: bool = False
    needs_multi_step: bool = False


class SerializedResult(BaseModel):
    format: str
    markdown: str
    preview: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0


class ChatRequest(BaseModel):
    threadId: str | None = None
    runId: str | None = None
    scenarioId: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    context: list[dict[str, Any]] = Field(default_factory=list)
    forwardedProps: dict[str, Any] = Field(default_factory=dict)


class ChatMessageRecord(BaseModel):
    id: str
    role: str
    content: Any = ""
    toolCalls: list[dict[str, Any]] = Field(default_factory=list)
    toolCallId: str | None = None
    created_at: float


class ChatSessionRecord(BaseModel):
    session_id: str
    title: str
    scenario_id: str
    scenario_label: str
    dataset_name: str
    created_at: float
    updated_at: float
    messages: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    status: str = "idle"


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    scenario_id: str
    scenario_label: str
    dataset_name: str
    created_at: float
    updated_at: float
    message_count: int
    status: str


class ChatSessionPayload(BaseModel):
    session_id: str
    title: str
    scenario_id: str
    scenario_label: str
    dataset_name: str
    created_at: float
    updated_at: float
    messages: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    status: str


class LLMResponse(BaseModel):
    content: str
    raw: dict[str, Any] = Field(default_factory=dict)
