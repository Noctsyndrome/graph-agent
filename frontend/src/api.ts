import type {
  ChatSessionPayload,
  ChatSessionSummary,
  ExampleGroup,
  HealthPayload,
  LlmStatusPayload,
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

export function fetchSchemaSummary(): Promise<SchemaSummaryPayload> {
  return readJson<SchemaSummaryPayload>("/schema");
}

export function fetchSessions(): Promise<ChatSessionSummary[]> {
  return readJson<ChatSessionSummary[]>("/chat/sessions");
}

export function fetchSessionPayload(sessionId: string): Promise<ChatSessionPayload> {
  return readJson<ChatSessionPayload>(`/chat/${sessionId}/messages`);
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

export async function fetchExampleGroups(): Promise<ExampleGroup[]> {
  const payload = await readJson<Record<string, unknown>>("/examples");
  return normalizeExampleGroups(payload);
}
