import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as ScrollArea from "@radix-ui/react-scroll-area";
import {
  Bot,
  ChevronLeft,
  Database,
  GitBranch,
  Plus,
  Server,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { AppendMessage } from "@assistant-ui/react";

import "./index.css";
import { AssistantThread, type ToolSelection } from "./components/assistant-thread";
import {
  applyStreamEventToRawMessages,
  appendUserRawMessage,
  extractTextFromAppendMessage,
  rawMessagesToThreadMessages,
  statusFromEvent,
} from "./assistant-runtime";
import {
  fetchExampleGroups,
  fetchHealth,
  fetchLlmStatus,
  fetchSchemaSummary,
  fetchScenarios,
  fetchSessionPayload,
  fetchSessions,
  streamChat,
} from "./api";
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

function createEmptySession(sessionId: string, scenario: ScenarioSummary | null = null): ChatSessionPayload {
  const now = Date.now() / 1000;
  return {
    session_id: sessionId,
    title: "新会话",
    scenario_id: scenario?.id ?? "",
    scenario_label: scenario?.label ?? "",
    dataset_name: scenario?.dataset_name ?? "",
    created_at: now,
    updated_at: now,
    messages: [],
    state: {},
    status: "idle",
  };
}

function formatTimestamp(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function summarizeValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value.length > 180 ? `${value.slice(0, 180)}...` : value;
  }
  const text = JSON.stringify(value, null, 2);
  return text.length > 260 ? `${text.slice(0, 260)}...` : text;
}

function sessionTitleFromPayload(payload: ChatSessionPayload): string {
  const userMessage = payload.messages.find((message) => message.role === "user");
  if (payload.title && payload.title !== "新会话") {
    return payload.title;
  }
  if (typeof userMessage?.content === "string" && userMessage.content.trim()) {
    return userMessage.content.trim().slice(0, 32);
  }
  return payload.scenario_label ? `${payload.scenario_label}会话` : "新会话";
}

function countVisibleMessages(messages: BackendChatMessage[]): number {
  return messages.filter((item) => item.role === "user" || item.role === "assistant").length;
}

function hashString(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function pickSuggestionPool(groups: ExampleGroup[], seed: string): Array<{ id: string; question: string }> {
  return groups
    .flatMap((group) =>
      group.cases.map((item, index) => ({
        id: item.id,
        question: item.question,
        score: hashString(`${seed}:${group.name}:${item.id}:${index}`),
      })),
    )
    .sort((left, right) => left.score - right.score)
    .map((item) => ({
      id: item.id,
      question: item.question,
    }));
}

function normalizeRendererPayload(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function ResultRenderer({
  selectedToolCall,
}: {
  selectedToolCall: ToolSelection | null;
}) {
  const contextualResult =
    selectedToolCall?.toolName === "format_results"
      ? normalizeRendererPayload(selectedToolCall.result)
      : null;

  if (!contextualResult) {
    return <div className="drawer-empty">当前选中的工具调用没有可渲染结果。</div>;
  }

  const renderer = String(contextualResult.renderer ?? "raw_json");
  const payload = contextualResult.payload;

  if (
    renderer === "metric_cards" &&
    Array.isArray(payload) &&
    payload.length > 0 &&
    typeof payload[0] === "object" &&
    payload[0] !== null
  ) {
    return (
      <div className="metric-cards">
        {Object.entries(payload[0] as Record<string, unknown>).map(([key, value]) => (
          <div key={key} className="metric-card">
            <span>{key}</span>
            <strong>{String(value)}</strong>
          </div>
        ))}
      </div>
    );
  }

  if (renderer === "table" && Array.isArray(payload) && payload.length > 0) {
    const rows = payload.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
    const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));

    return (
      <div className="result-table-shell">
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

  return <pre className="drawer-json">{JSON.stringify(contextualResult, null, 2)}</pre>;
}

function ToolDetailRenderer({ selectedToolCall }: { selectedToolCall: ToolSelection | null }) {
  if (!selectedToolCall) {
    return <div className="drawer-empty">点击线程中的工具调用卡片查看详情。</div>;
  }

  return (
    <div className="drawer-list">
      <article className="drawer-card drawer-card-primary">
        <div className="drawer-card-title">
          <Wrench size={14} />
          <span>{selectedToolCall.toolName}</span>
        </div>
        <div className="drawer-card-copy">
          <label>输入摘要</label>
          <p>{summarizeValue(selectedToolCall.args)}</p>
        </div>
        <div className="drawer-card-copy">
          <label>输出摘要</label>
          <p>{summarizeValue(selectedToolCall.result)}</p>
        </div>
        <details className="drawer-json-details">
          <summary>查看原始 JSON</summary>
          <pre className="drawer-json">
            {JSON.stringify(
              {
                toolCallId: selectedToolCall.toolCallId,
                toolName: selectedToolCall.toolName,
                args: selectedToolCall.args,
                result: selectedToolCall.result,
              },
              null,
              2,
            )}
          </pre>
        </details>
      </article>
    </div>
  );
}

function StatusItem({
  icon,
  label,
  connected,
}: {
  icon: ReactNode;
  label: string;
  connected: boolean;
}) {
  return (
    <div className="sidebar-status-item" aria-label={`${label}:${connected ? "online" : "offline"}`} title={`${label}: ${connected ? "online" : "offline"}`}>
      <span className="sidebar-status-icon">{icon}</span>
      <span className={`sidebar-status-dot ${connected ? "online" : "offline"}`} />
    </div>
  );
}

function ScenarioPickerDialog({
  open,
  scenarios,
  onOpenChange,
  onSelect,
}: {
  open: boolean;
  scenarios: ScenarioSummary[];
  onOpenChange: (open: boolean) => void;
  onSelect: (scenario: ScenarioSummary) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="scenario-overlay" />
        <Dialog.Content className="scenario-dialog">
          <div className="scenario-dialog-header">
            <Dialog.Title>选择图谱场景</Dialog.Title>
            <Dialog.Description>新会话会绑定到所选场景，开始对话后不可修改。</Dialog.Description>
          </div>
          <div className="scenario-grid">
            {scenarios.map((scenario) => (
              <button
                key={scenario.id}
                type="button"
                className="scenario-card"
                onClick={() => onSelect(scenario)}
              >
                <div className="scenario-card-title">{scenario.label}</div>
                <div className="scenario-card-copy">{scenario.description}</div>
                <div className="scenario-card-meta">{scenario.dataset_name}</div>
              </button>
            ))}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default function App() {
  const initialSessionId = useRef(crypto.randomUUID()).current;
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [llmStatus, setLlmStatus] = useState<LlmStatusPayload | null>(null);
  const [schemaSummary, setSchemaSummary] = useState<SchemaSummaryPayload | null>(null);
  const [exampleGroups, setExampleGroups] = useState<ExampleGroup[]>([]);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string>(initialSessionId);
  const [sessionPayload, setSessionPayload] = useState<ChatSessionPayload>(() => createEmptySession(initialSessionId));
  const [rawMessages, setRawMessages] = useState<BackendChatMessage[]>([]);
  const [threadState, setThreadState] = useState<Record<string, unknown>>({});
  const [selectedToolCall, setSelectedToolCall] = useState<ToolSelection | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [statusText, setStatusText] = useState("准备就绪");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [scenarioPickerOpen, setScenarioPickerOpen] = useState(false);
  const [loadingState, setLoadingState] = useState("正在加载系统状态");
  const [globalError, setGlobalError] = useState<string | null>(null);

  const currentSessionIdRef = useRef(currentSessionId);
  const rawMessagesRef = useRef(rawMessages);
  const threadStateRef = useRef(threadState);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  useEffect(() => {
    rawMessagesRef.current = rawMessages;
  }, [rawMessages]);

  useEffect(() => {
    threadStateRef.current = threadState;
  }, [threadState]);

  const refreshScenarioMeta = useCallback(async (scenarioId: string) => {
    if (!scenarioId) {
      setSchemaSummary(null);
      setExampleGroups([]);
      return;
    }
    const [nextSchema, nextExamples] = await Promise.all([
      fetchSchemaSummary(scenarioId),
      fetchExampleGroups(scenarioId),
    ]);
    setSchemaSummary(nextSchema);
    setExampleGroups(nextExamples);
  }, []);

  const refreshMeta = useCallback(async () => {
    setLoadingState("正在刷新系统状态");
    setGlobalError(null);
    try {
      const [nextHealth, nextLlmStatus, nextScenarios] = await Promise.all([
        fetchHealth(),
        fetchLlmStatus(),
        fetchScenarios(),
      ]);
      setHealth(nextHealth);
      setLlmStatus(nextLlmStatus);
      setScenarios(nextScenarios);
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

  const hydrateSession = useCallback(
    async (sessionId: string, showLoading = true) => {
      if (showLoading) {
        setLoadingState("正在恢复会话");
      }
      setGlobalError(null);
      try {
        const payload = await fetchSessionPayload(sessionId);
        setCurrentSessionId(sessionId);
        setSessionPayload(payload);
        setRawMessages(payload.messages);
        setThreadState(payload.state ?? {});
        setSelectedToolCall(null);
        setStatusText(payload.status === "running" ? "正在恢复执行状态" : "准备就绪");
        setIsRunning(payload.status === "running");
        setScenarioPickerOpen(false);
        await refreshScenarioMeta(payload.scenario_id);
        setLoadingState("");
      } catch (error) {
        setGlobalError(error instanceof Error ? error.message : String(error));
        setLoadingState("");
      }
    },
    [refreshScenarioMeta],
  );

  const startScenarioSession = useCallback(
    async (scenario: ScenarioSummary) => {
      const sessionId = crypto.randomUUID();
      const payload = createEmptySession(sessionId, scenario);
      setCurrentSessionId(sessionId);
      setSessionPayload(payload);
      setRawMessages([]);
      setThreadState({});
      setSelectedToolCall(null);
      setStatusText("准备就绪");
      setIsRunning(false);
      setGlobalError(null);
      setScenarioPickerOpen(false);
      await refreshScenarioMeta(scenario.id);
    },
    [refreshScenarioMeta],
  );

  const openScenarioPicker = useCallback(() => {
    setScenarioPickerOpen(true);
  }, []);

  useEffect(() => {
    void (async () => {
      await refreshMeta();
      await refreshSessions();
    })();
  }, [refreshMeta, refreshSessions]);

  useEffect(() => {
    if (!scenarios.length) {
      return;
    }
    if (!sessionPayload.scenario_id && rawMessages.length === 0) {
      setScenarioPickerOpen(true);
    }
  }, [rawMessages.length, scenarios, sessionPayload.scenario_id]);

  const threadMessages = useMemo(() => rawMessagesToThreadMessages(rawMessages), [rawMessages]);
  const currentTitle = sessionTitleFromPayload(sessionPayload);
  const visibleMessageCount = countVisibleMessages(rawMessages);
  const suggestionCards = useMemo(
    () => pickSuggestionPool(exampleGroups, `${currentSessionId}:${sessionPayload.scenario_id}:all-examples`),
    [currentSessionId, exampleGroups, sessionPayload.scenario_id],
  );
  const activeToolSelection = selectedToolCall ?? null;

  const handleToolSelection = useCallback((selection: ToolSelection) => {
    setSelectedToolCall(selection);
    setInspectorOpen(true);
  }, []);

  const syncSnapshotState = useCallback((snapshot: Record<string, unknown>) => {
    setThreadState(snapshot);
  }, []);

  const handleStreamEvent = useCallback(
    (event: ChatStreamEvent) => {
      const nextStatus = statusFromEvent(event);
      if (nextStatus) {
        setStatusText(nextStatus);
      }

      if (event.type === "RUN_STARTED") {
        setIsRunning(true);
      }
      if (event.type === "STATE_SNAPSHOT" && event.snapshot && typeof event.snapshot === "object") {
        syncSnapshotState(event.snapshot as Record<string, unknown>);
      }
      if (event.type === "RUN_FINISHED") {
        setIsRunning(false);
        setStatusText("已完成");
      }
      if (event.type === "RUN_ERROR") {
        setIsRunning(false);
        setGlobalError(String(event.message ?? "执行失败"));
      }

      setRawMessages((current) => applyStreamEventToRawMessages(current, event));
    },
    [syncSnapshotState],
  );

  const runQuestion = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || isRunning) {
        return;
      }
      if (loadingState) {
        setStatusText(loadingState);
        return;
      }
      if (!sessionPayload.scenario_id) {
        setScenarioPickerOpen(true);
        return;
      }

      setGlobalError(null);
      setStatusText("正在准备执行");
      setIsRunning(true);

      const nextRawMessages = appendUserRawMessage(rawMessagesRef.current, text);
      setRawMessages(nextRawMessages);

      try {
        await streamChat(
          currentSessionIdRef.current,
          nextRawMessages,
          threadStateRef.current,
          sessionPayload.scenario_id || undefined,
          handleStreamEvent,
        );
        await refreshSessions();
        const payload = await fetchSessionPayload(currentSessionIdRef.current);
        setSessionPayload(payload);
        syncSnapshotState(payload.state ?? {});
      } catch (error) {
        setIsRunning(false);
        setStatusText("执行失败");
        setGlobalError(error instanceof Error ? error.message : String(error));
      }
    },
    [handleStreamEvent, isRunning, loadingState, refreshSessions, sessionPayload.scenario_id, syncSnapshotState],
  );

  const handleComposerSubmit = useCallback(
    async (message: AppendMessage) => {
      const question = extractTextFromAppendMessage(message);
      await runQuestion(question);
    },
    [runQuestion],
  );

  const sidebarSessions = useMemo(() => {
    if (sessions.some((session) => session.session_id === currentSessionId)) {
      return sessions;
    }
    return [
      {
        session_id: sessionPayload.session_id,
        title: currentTitle,
        scenario_id: sessionPayload.scenario_id,
        scenario_label: sessionPayload.scenario_label,
        dataset_name: sessionPayload.dataset_name,
        created_at: sessionPayload.created_at,
        updated_at: sessionPayload.updated_at,
        message_count: visibleMessageCount,
        status: sessionPayload.status,
      },
      ...sessions,
    ];
  }, [currentSessionId, currentTitle, sessionPayload, sessions, visibleMessageCount]);

  const graphSummary = schemaSummary
    ? `${schemaSummary.entity_count} 实体 / ${schemaSummary.relationship_count} 关系`
    : sessionPayload.scenario_id
      ? "等待图谱摘要"
      : "请先选择场景";
  const neo4jConnected = Boolean(schemaSummary);
  const startupPending = Boolean(loadingState);
  const composerDisabledReason = startupPending
    ? `${loadingState}，暂时不能发起问答。`
    : !sessionPayload.scenario_id
      ? "请先选择场景，然后开始提问。"
      : null;

  return (
    <div className="app-shell">
      <ScenarioPickerDialog
        open={scenarioPickerOpen}
        scenarios={scenarios}
        onOpenChange={setScenarioPickerOpen}
        onSelect={(scenario) => {
          void startScenarioSession(scenario);
        }}
      />

      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <div className="brand-icon">
              <GitBranch size={16} />
            </div>
            <div className="brand-copy">
              <strong>KG-QA Copilot</strong>
              <span>Knowledge Graph Chat</span>
            </div>
          </div>

          <button className="new-thread-button" onClick={openScenarioPicker}>
            <Plus size={16} />
            <span>新会话</span>
          </button>
        </div>

        <ScrollArea.Root className="sidebar-scroll">
          <ScrollArea.Viewport className="sidebar-scroll-viewport">
            <div className="session-list">
              {sidebarSessions.map((session) => (
                <button
                  key={session.session_id}
                  className={`session-card ${session.session_id === currentSessionId ? "active" : ""}`}
                  onClick={() => void hydrateSession(session.session_id)}
                >
                  <div className="session-card-title">{session.title}</div>
                  {session.scenario_label ? (
                    <div className="session-card-badges">
                      <span className="session-badge">{session.scenario_label}</span>
                    </div>
                  ) : null}
                  <div className="session-card-meta">
                    <span>{session.message_count} 条消息</span>
                    <span>{formatTimestamp(session.updated_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          </ScrollArea.Viewport>
          <ScrollArea.Scrollbar className="scrollbar" orientation="vertical">
            <ScrollArea.Thumb className="scrollbar-thumb" />
          </ScrollArea.Scrollbar>
        </ScrollArea.Root>

        <div className="sidebar-footer">
          <StatusItem icon={<Server size={12} />} label="API" connected={health?.status === "ok"} />
          <StatusItem icon={<Database size={12} />} label="Neo4j" connected={neo4jConnected} />
          <StatusItem icon={<Bot size={12} />} label="LLM" connected={Boolean(llmStatus?.connected)} />
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-header">
          <div className="workspace-heading">
            <h1>{currentTitle}</h1>
            {sessionPayload.scenario_label ? <p>{sessionPayload.scenario_label}</p> : null}
          </div>
        </header>

        {globalError ? <div className="error-banner">{globalError}</div> : null}

        <div className="thread-shell">
          <div className="thread-shell-header">
            <div className="thread-context">
              {llmStatus?.model ? (
                <span className="context-badge">
                  <Sparkles size={13} />
                  {llmStatus.model}
                </span>
              ) : null}
              {sessionPayload.scenario_label ? (
                <span className="context-badge">
                  <GitBranch size={13} />
                  {sessionPayload.scenario_label}
                </span>
              ) : null}
              {sessionPayload.dataset_name ? (
                <span className="context-badge">
                  <Database size={13} />
                  {sessionPayload.dataset_name}
                </span>
              ) : null}
              <span className="context-badge">
                <Database size={13} />
                {graphSummary}
              </span>
            </div>
          </div>

          <section className="thread-panel">
            <AssistantThread
              messages={threadMessages}
              isRunning={isRunning}
              statusText={statusText}
              suggestions={suggestionCards}
              startupHint={startupPending ? loadingState : null}
              composerDisabled={startupPending || !sessionPayload.scenario_id}
              composerDisabledReason={composerDisabledReason}
              onSubmit={handleComposerSubmit}
              onSuggestionClick={(question) => {
                void runQuestion(question);
              }}
              onToolClick={handleToolSelection}
            />
          </section>
        </div>

        <Dialog.Root open={inspectorOpen} onOpenChange={setInspectorOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="drawer-overlay" />
            <Dialog.Content className="drawer">
              <div className="drawer-header">
                <div>
                  <Dialog.Title>执行详情</Dialog.Title>
                  <Dialog.Description>
                    {activeToolSelection
                      ? `当前选中：${activeToolSelection.toolName}`
                      : "点击线程中的工具调用卡片查看详情"}
                  </Dialog.Description>
                </div>
                <Dialog.Close asChild>
                  <button className="drawer-close" aria-label="关闭详情">
                    <ChevronLeft size={16} />
                  </button>
                </Dialog.Close>
              </div>

              <ScrollArea.Root className="drawer-scroll">
                <ScrollArea.Viewport className="drawer-scroll-viewport">
                  {activeToolSelection?.toolName === "format_results" ? (
                    <ResultRenderer selectedToolCall={activeToolSelection} />
                  ) : (
                    <ToolDetailRenderer selectedToolCall={activeToolSelection} />
                  )}
                </ScrollArea.Viewport>
                <ScrollArea.Scrollbar className="scrollbar" orientation="vertical">
                  <ScrollArea.Thumb className="scrollbar-thumb" />
                </ScrollArea.Scrollbar>
              </ScrollArea.Root>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </main>
    </div>
  );
}
