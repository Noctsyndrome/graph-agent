import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CopilotChat,
  CopilotKitProvider,
  HttpAgent,
  UseAgentUpdate,
  useAgent,
  type Message,
  type State,
} from "@copilotkitnext/react";
import "@copilotkit/react-ui/styles.css";
import "@copilotkit/react-core/v2/styles.css";
import "./index.css";

import {
  fetchExampleGroups,
  fetchHealth,
  fetchLlmStatus,
  fetchSchemaSummary,
  fetchSessionPayload,
  fetchSessions,
  getApiBase,
} from "./api";
import type {
  ChatSessionPayload,
  ChatSessionSummary,
  ExampleGroup,
  HealthPayload,
  LlmStatusPayload,
  SchemaSummaryPayload,
} from "./types";

const AGENT_ID = "kgqa-agent";

function createEmptySession(sessionId: string): ChatSessionPayload {
  const now = Date.now() / 1000;
  return {
    session_id: sessionId,
    title: "新会话",
    created_at: now,
    updated_at: now,
    messages: [],
    state: {},
    status: "idle",
  };
}

function timestampText(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN", {
    hour12: false,
  });
}

function summarizeValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value.length > 120 ? `${value.slice(0, 120)}...` : value;
  }
  const text = JSON.stringify(value, null, 2);
  return text.length > 160 ? `${text.slice(0, 160)}...` : text;
}

function ResultRenderer({ latestResult }: { latestResult: Record<string, unknown> | null }) {
  if (!latestResult) {
    return <div className="empty-card">等待查询结果</div>;
  }

  const renderer = String(latestResult.renderer ?? "raw_json");
  const payload = latestResult.payload;

  if (renderer === "metric_cards" && Array.isArray(payload) && payload.length > 0 && typeof payload[0] === "object" && payload[0] !== null) {
    return (
      <div className="metric-grid">
        {Object.entries(payload[0] as Record<string, unknown>).map(([key, value]) => (
          <div key={key} className="metric-card">
            <div className="metric-label">{key}</div>
            <div className="metric-value">{String(value)}</div>
          </div>
        ))}
      </div>
    );
  }

  if (renderer === "table" && Array.isArray(payload) && payload.length > 0) {
    const rows = payload.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
    const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    return (
      <div className="table-wrap">
        <table className="result-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`row-${rowIndex}`}>
                {columns.map((column) => (
                  <td key={`${rowIndex}-${column}`}>{summarizeValue(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return <pre className="json-view">{JSON.stringify(latestResult, null, 2)}</pre>;
}

interface WorkspaceProps {
  apiBase: string;
  agent: HttpAgent;
  currentSessionId: string;
  sessionPayload: ChatSessionPayload;
  sessions: ChatSessionSummary[];
  exampleGroups: ExampleGroup[];
  health: HealthPayload | null;
  llmStatus: LlmStatusPayload | null;
  schemaSummary: SchemaSummaryPayload | null;
  loadingState: string;
  globalError: string | null;
  onRefreshMeta: () => Promise<void>;
  onRefreshSessions: () => Promise<void>;
  onSelectSession: (sessionId: string) => Promise<void>;
  onCreateSession: () => void;
}

function Workspace({
  apiBase,
  agent,
  currentSessionId,
  sessionPayload,
  sessions,
  exampleGroups,
  health,
  llmStatus,
  schemaSummary,
  loadingState,
  globalError,
  onRefreshMeta,
  onRefreshSessions,
  onSelectSession,
  onCreateSession,
}: WorkspaceProps) {
  const { agent: boundAgent } = useAgent({
    agentId: AGENT_ID,
    updates: [UseAgentUpdate.OnMessagesChanged, UseAgentUpdate.OnStateChanged, UseAgentUpdate.OnRunStatusChanged],
  });
  const [messages, setMessages] = useState<Message[]>(agent.messages);
  const [agentState, setAgentState] = useState<Record<string, unknown>>((agent.state as Record<string, unknown>) ?? {});
  const [isRunning, setIsRunning] = useState<boolean>(agent.isRunning);
  const [eventLog, setEventLog] = useState<Array<{ title: string; detail?: string }>>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [selectedGroupName, setSelectedGroupName] = useState<string>("");
  const [selectedExampleId, setSelectedExampleId] = useState<string>("");

  useEffect(() => {
    setMessages([...boundAgent.messages]);
    setAgentState({ ...((boundAgent.state as Record<string, unknown>) ?? {}) });
    setIsRunning(boundAgent.isRunning);
    setChatError(null);
    setEventLog([]);

    const subscription = boundAgent.subscribe({
      onMessagesChanged: ({ messages: nextMessages }) => {
        setMessages([...nextMessages]);
      },
      onStateChanged: ({ state: nextState }) => {
        setAgentState({ ...((nextState as Record<string, unknown>) ?? {}) });
      },
      onRunStartedEvent: async () => {
        setIsRunning(true);
        setChatError(null);
        setEventLog((current) => [{ title: "开始执行", detail: "Agent 已启动" }, ...current].slice(0, 24));
        await onRefreshSessions();
      },
      onStepStartedEvent: ({ event }) => {
        setEventLog((current) => [{ title: "步骤开始", detail: String(event.stepName ?? "-") }, ...current].slice(0, 24));
      },
      onToolCallStartEvent: ({ event }) => {
        setEventLog((current) => [{ title: "工具调用", detail: String(event.toolCallName ?? "-") }, ...current].slice(0, 24));
      },
      onToolCallResultEvent: ({ event }) => {
        setEventLog((current) => [{ title: "工具结果", detail: summarizeValue(event.content) }, ...current].slice(0, 24));
      },
      onCustomEvent: ({ event }) => {
        setEventLog((current) => [{ title: `自定义事件 ${event.name}`, detail: summarizeValue(event.value) }, ...current].slice(0, 24));
      },
      onRunFinishedEvent: async () => {
        setIsRunning(false);
        setEventLog((current) => [{ title: "执行完成" }, ...current].slice(0, 24));
        await onRefreshSessions();
      },
      onRunErrorEvent: async ({ event }) => {
        setIsRunning(false);
        setChatError(String(event.message ?? "执行失败"));
        setEventLog((current) => [{ title: "执行失败", detail: String(event.message ?? "") }, ...current].slice(0, 24));
        await onRefreshSessions();
      },
    });

    return () => subscription.unsubscribe();
  }, [boundAgent, onRefreshSessions]);

  useEffect(() => {
    if (exampleGroups.length === 0) {
      setSelectedGroupName("");
      setSelectedExampleId("");
      return;
    }
    setSelectedGroupName((current) => {
      if (current && exampleGroups.some((group) => group.name === current)) {
        return current;
      }
      return exampleGroups[0].name;
    });
  }, [exampleGroups]);

  useEffect(() => {
    const selectedGroup = exampleGroups.find((group) => group.name === selectedGroupName);
    if (!selectedGroup || selectedGroup.cases.length === 0) {
      setSelectedExampleId("");
      return;
    }
    setSelectedExampleId((current) => {
      if (current && selectedGroup.cases.some((item) => item.id === current)) {
        return current;
      }
      return selectedGroup.cases[0].id;
    });
  }, [exampleGroups, selectedGroupName]);

  const latestResult = (agentState.latestResult as Record<string, unknown> | undefined) ?? null;
  const toolHistory = Array.isArray(agentState.toolHistory)
    ? (agentState.toolHistory as Array<Record<string, unknown>>)
    : [];
  const selectedGroup = exampleGroups.find((group) => group.name === selectedGroupName) ?? null;
  const selectedExample =
    selectedGroup?.cases.find((item) => item.id === selectedExampleId) ?? selectedGroup?.cases[0] ?? null;

  const submitQuestion = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || boundAgent.isRunning) {
        return;
      }
      setChatError(null);
      boundAgent.addMessage({
        id: crypto.randomUUID(),
        role: "user",
        content: text,
      });
      setMessages([...boundAgent.messages]);
      try {
        await boundAgent.runAgent();
      } catch (error) {
        setChatError(error instanceof Error ? error.message : String(error));
      }
    },
    [boundAgent],
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="panel">
          <div className="panel-header">
            <h2>会话</h2>
            <button className="ghost-button" onClick={onCreateSession}>
              新会话
            </button>
          </div>
          <div className="session-list">
            {sessions.length === 0 ? <div className="muted">暂无已保存会话</div> : null}
            {sessions.map((session) => (
              <button
                key={session.session_id}
                className={`session-item ${session.session_id === currentSessionId ? "active" : ""}`}
                onClick={() => void onSelectSession(session.session_id)}
              >
                <div className="session-title">{session.title}</div>
                <div className="session-meta">
                  <span>{session.message_count} 条消息</span>
                  <span>{timestampText(session.updated_at)}</span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>测试用例</h2>
            <button className="ghost-button" onClick={onRefreshMeta}>
              刷新状态
            </button>
          </div>
          <div className="form-block">
            <label className="field-label" htmlFor="example-group-select">
              用例分组
            </label>
            <select
              id="example-group-select"
              className="field-select"
              value={selectedGroupName}
              onChange={(event) => setSelectedGroupName(event.target.value)}
            >
              {exampleGroups.map((group) => (
                <option key={group.name} value={group.name}>
                  {group.name} ({group.cases.length})
                </option>
              ))}
            </select>
            {selectedGroup ? <div className="example-group-desc">{selectedGroup.description}</div> : null}
          </div>

          <div className="form-block">
            <label className="field-label" htmlFor="example-case-select">
              选择用例
            </label>
            <select
              id="example-case-select"
              className="field-select"
              value={selectedExampleId}
              onChange={(event) => setSelectedExampleId(event.target.value)}
              disabled={!selectedGroup}
            >
              {(selectedGroup?.cases ?? []).map((example) => (
                <option key={example.id} value={example.id}>
                  {example.id} | {example.question}
                </option>
              ))}
            </select>
          </div>

          {selectedExample ? (
            <div className="example-preview">
              <div className="example-id">{selectedExample.id}</div>
              <div className="example-question">{selectedExample.question}</div>
              {selectedExample.expected_contains && selectedExample.expected_contains.length > 0 ? (
                <div className="trace-body">期望包含：{selectedExample.expected_contains.join("、")}</div>
              ) : null}
              {selectedExample.note ? <div className="trace-body">备注：{selectedExample.note}</div> : null}
              <button
                className="example-run-button"
                onClick={() => void submitQuestion(selectedExample.question)}
                disabled={isRunning}
              >
                使用此用例发起对话
              </button>
            </div>
          ) : (
            <div className="empty-card">当前没有可用测试用例</div>
          )}
        </div>
      </aside>

      <main className="main-content">
        <section className="status-grid">
          <div className="status-card">
            <div className="status-label">API</div>
            <div className="status-value">{health?.status ?? "-"}</div>
            <div className="status-note">{apiBase}</div>
          </div>
          <div className="status-card">
            <div className="status-label">数据集</div>
            <div className="status-value">{health?.dataset ?? "-"}</div>
            <div className="status-note">实体 {schemaSummary?.entity_count ?? "-"} / 关系 {schemaSummary?.relationship_count ?? "-"}</div>
          </div>
          <div className="status-card">
            <div className="status-label">LLM</div>
            <div className="status-value">{llmStatus?.connected ? "connected" : "offline"}</div>
            <div className="status-note">
              {llmStatus?.model ?? "-"} {llmStatus?.latency_ms ? `· ${llmStatus.latency_ms} ms` : ""}
            </div>
          </div>
          <div className="status-card">
            <div className="status-label">当前会话</div>
            <div className="status-value">{sessionPayload.title}</div>
            <div className="status-note">{currentSessionId}</div>
          </div>
        </section>

        <section className="workspace-grid">
          <div className="chat-panel">
            <div className="panel-header">
              <div>
                <h2>CopilotKit Chat</h2>
                <div className="muted">
                  {loadingState || (isRunning ? "Agent 正在执行图谱问答工具链" : "支持多轮问答、工具轨迹与结构化结果渲染")}
                </div>
              </div>
            </div>
            <div className="chat-host">
              <CopilotChat
                agentId={AGENT_ID}
                threadId={currentSessionId}
                labels={{
                  modalHeaderTitle: "知识图谱 Agent",
                  welcomeMessageText: "可以直接提问，也可以继续追问，Agent 会在同一会话里保持上下文。",
                  chatInputPlaceholder: "请输入问题，例如：万科名下各项目都在用哪些冷水机品牌？",
                }}
              />
            </div>
            {chatError || globalError ? <div className="error-banner">{chatError ?? globalError}</div> : null}
          </div>

          <div className="inspector-panel">
            <div className="panel">
              <div className="panel-header">
                <h2>工具轨迹</h2>
                <span className={`run-badge ${isRunning ? "running" : "idle"}`}>{isRunning ? "running" : "idle"}</span>
              </div>
              {toolHistory.length === 0 ? <div className="empty-card">等待工具调用</div> : null}
              <div className="trace-list">
                {toolHistory.slice().reverse().map((item, index) => (
                  <div className="trace-item" key={`${index}-${String(item.tool_name ?? "tool")}`}>
                    <div className="trace-title">{String(item.tool_name ?? "tool")}</div>
                    <div className="trace-body">参数：{summarizeValue(item.tool_args)}</div>
                    <div className="trace-body">结果：{summarizeValue(item.tool_result)}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="panel">
              <div className="panel-header">
                <h2>结果渲染壳</h2>
                <span className="muted">table / metric_cards / raw_json</span>
              </div>
              <ResultRenderer latestResult={latestResult} />
            </div>

            <div className="panel">
              <div className="panel-header">
                <h2>事件摘要</h2>
                <span className="muted">{messages.length} 条消息</span>
              </div>
              {eventLog.length === 0 ? <div className="empty-card">等待流式事件</div> : null}
              <div className="trace-list compact">
                {eventLog.map((item, index) => (
                  <div className="trace-item" key={`${item.title}-${index}`}>
                    <div className="trace-title">{item.title}</div>
                    {item.detail ? <div className="trace-body">{item.detail}</div> : null}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default function App() {
  const apiBase = getApiBase();
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [llmStatus, setLlmStatus] = useState<LlmStatusPayload | null>(null);
  const [schemaSummary, setSchemaSummary] = useState<SchemaSummaryPayload | null>(null);
  const [exampleGroups, setExampleGroups] = useState<ExampleGroup[]>([]);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string>(() => crypto.randomUUID());
  const [sessionPayload, setSessionPayload] = useState<ChatSessionPayload>(() => createEmptySession(crypto.randomUUID()));
  const [loadingState, setLoadingState] = useState<string>("正在加载系统状态");
  const [globalError, setGlobalError] = useState<string | null>(null);

  const refreshMeta = useCallback(async () => {
    setLoadingState("正在刷新系统状态");
    setGlobalError(null);
    try {
      const [nextHealth, nextLlmStatus, nextSchema, nextExamples] = await Promise.all([
        fetchHealth(),
        fetchLlmStatus(),
        fetchSchemaSummary(),
        fetchExampleGroups(),
      ]);
      setHealth(nextHealth);
      setLlmStatus(nextLlmStatus);
      setSchemaSummary(nextSchema);
      setExampleGroups(nextExamples);
      setLoadingState("");
    } catch (error) {
      setGlobalError(error instanceof Error ? error.message : String(error));
      setLoadingState("");
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      const nextSessions = await fetchSessions();
      setSessions(nextSessions);
    } catch (error) {
      setGlobalError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  const selectSession = useCallback(async (sessionId: string) => {
    setLoadingState("正在恢复会话");
    setGlobalError(null);
    try {
      const payload = await fetchSessionPayload(sessionId);
      setCurrentSessionId(sessionId);
      setSessionPayload(payload);
      setLoadingState("");
    } catch (error) {
      setGlobalError(error instanceof Error ? error.message : String(error));
      setLoadingState("");
    }
  }, []);

  const createSession = useCallback(() => {
    const sessionId = crypto.randomUUID();
    setCurrentSessionId(sessionId);
    setSessionPayload(createEmptySession(sessionId));
    setGlobalError(null);
    setLoadingState("");
  }, []);

  useEffect(() => {
    void (async () => {
      await refreshMeta();
      await refreshSessions();
    })();
  }, [refreshMeta, refreshSessions]);

  useEffect(() => {
    if (sessionPayload.session_id !== currentSessionId) {
      setSessionPayload(createEmptySession(currentSessionId));
    }
  }, [currentSessionId, sessionPayload.session_id]);

  const agent = useMemo(
    () =>
      new HttpAgent({
        agentId: AGENT_ID,
        threadId: currentSessionId,
        url: `${apiBase}/chat`,
        initialMessages: sessionPayload.messages as Message[],
        initialState: sessionPayload.state as State,
        debug: false,
      }),
    [apiBase, currentSessionId, sessionPayload.messages, sessionPayload.state],
  );

  return (
    <CopilotKitProvider
      selfManagedAgents={{ [AGENT_ID]: agent }}
      showDevConsole={false}
      a2ui={{}}
    >
      <Workspace
        apiBase={apiBase}
        agent={agent}
        currentSessionId={currentSessionId}
        sessionPayload={sessionPayload}
        sessions={sessions}
        exampleGroups={exampleGroups}
        health={health}
        llmStatus={llmStatus}
        schemaSummary={schemaSummary}
        loadingState={loadingState}
        globalError={globalError}
        onRefreshMeta={refreshMeta}
        onRefreshSessions={refreshSessions}
        onSelectSession={selectSession}
        onCreateSession={createSession}
      />
    </CopilotKitProvider>
  );
}
