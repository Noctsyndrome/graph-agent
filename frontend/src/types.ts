export interface HealthPayload {
  status: string;
  dataset: string;
  llm_configured: boolean;
  llm_model: string;
}

export interface ScenarioSummary {
  id: string;
  label: string;
  description: string;
  dataset_name: string;
}

export interface LlmStatusPayload {
  configured: boolean;
  connected: boolean;
  base_url: string;
  model: string;
  latency_ms: number | null;
  detail: string;
  checked_at: number;
}

export interface SchemaSummaryPayload {
  dataset: string;
  description?: string;
  entity_count: number;
  relationship_count: number;
  paths?: string[];
}

export interface ExampleCase {
  id: string;
  question: string;
  expected_contains?: string[];
  note?: string;
}

export interface ExampleGroup {
  name: string;
  description: string;
  cases: ExampleCase[];
}

export interface ChatSessionSummary {
  session_id: string;
  title: string;
  scenario_id: string;
  scenario_label: string;
  dataset_name: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  status: string;
}

export interface ChatSessionPayload {
  session_id: string;
  title: string;
  scenario_id: string;
  scenario_label: string;
  dataset_name: string;
  created_at: number;
  updated_at: number;
  messages: BackendChatMessage[];
  state: Record<string, unknown>;
  status: string;
}

export interface BackendToolCall {
  id: string;
  type?: string;
  function?: {
    name?: string;
    arguments?: string;
  };
}

export interface BackendChatMessage {
  id?: string;
  role: string;
  content?: unknown;
  toolCalls?: BackendToolCall[];
  toolCallId?: string | null;
  created_at?: number;
}

export interface ChatStreamEvent {
  type: string;
  [key: string]: unknown;
}
