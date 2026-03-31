import type {
  BackendChatMessage,
  ChatStreamEvent,
  ChatSessionPayload,
  ChatSessionSummary,
  ExampleGroup,
  HealthPayload,
  LlmStatusPayload,
  ScenarioSummary,
  SchemaSummaryPayload,
} from "./types";

const API_BASE = import.meta.env.VITE_KGQA_API_BASE_URL ?? "http://127.0.0.1:8000";

export function getApiBase(): string {
  return API_BASE;
}

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthPayload> {
  return readJson<HealthPayload>("/health");
}

export function fetchLlmStatus(): Promise<LlmStatusPayload> {
  return readJson<LlmStatusPayload>("/llm/status");
}

export function fetchScenarios(): Promise<ScenarioSummary[]> {
  return readJson<ScenarioSummary[]>("/scenarios");
}

export function fetchSchemaSummary(scenarioId?: string): Promise<SchemaSummaryPayload> {
  const suffix = scenarioId ? `?scenario_id=${encodeURIComponent(scenarioId)}` : "";
  return readJson<SchemaSummaryPayload>(`/schema${suffix}`);
}

export function fetchSessions(): Promise<ChatSessionSummary[]> {
  return readJson<ChatSessionSummary[]>("/chat/sessions");
}

export function fetchSessionPayload(sessionId: string): Promise<ChatSessionPayload> {
  return readJson<ChatSessionPayload>(`/chat/${sessionId}/messages`);
}

export async function streamChat(
  sessionId: string,
  messages: BackendChatMessage[],
  state: Record<string, unknown>,
  scenarioId: string | undefined,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      threadId: sessionId,
      scenarioId,
      messages,
      state,
    }),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Chat stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushChunk = (chunk: string) => {
    const lines = chunk.split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) {
        continue;
      }
      const payload = trimmed.slice(5).trim();
      if (!payload) {
        continue;
      }
      onEvent(JSON.parse(payload) as ChatStreamEvent);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      flushChunk(frame);
    }
  }

  if (buffer.trim()) {
    flushChunk(buffer);
  }
}

function normalizeExampleGroups(payload: Record<string, unknown>): ExampleGroup[] {
  return Object.entries(payload).flatMap(([name, rawValue]) => {
    if (!Array.isArray(rawValue)) {
      return [];
    }
    const cases = rawValue
      .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
      .map((item, index) => ({
        id: String(item.id ?? `${name}-${index + 1}`),
        question: String(item.question ?? ""),
        expected_contains: Array.isArray(item.expected_contains)
          ? item.expected_contains.map((value) => String(value))
          : [],
        note: item.note ? String(item.note) : undefined,
      }))
      .filter((item) => item.question);
    if (cases.length === 0) {
      return [];
    }
    return [
      {
        name,
        description: groupDescription(name),
        cases,
      },
    ];
  });
}

function groupDescription(name: string): string {
  switch (name) {
    case "baseline":
      return "稳定基础场景";
    case "challenge":
      return "挑战场景与复杂问法";
    case "generalization":
      return "泛化问法与新路径";
    default:
      return "测试用例";
  }
}

export async function fetchExampleGroups(scenarioId?: string): Promise<ExampleGroup[]> {
  const suffix = scenarioId ? `?scenario_id=${encodeURIComponent(scenarioId)}` : "";
  const payload = await readJson<Record<string, unknown>>(`/examples${suffix}`);
  return normalizeExampleGroups(payload);
}
