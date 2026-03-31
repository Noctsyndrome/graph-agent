import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessage,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { ArrowDown, Check, ChevronLeft, ChevronRight, Loader2, Plus, SendHorizontal, Wrench } from "lucide-react";

type SuggestionItem = {
  id: string;
  question: string;
};

export type ToolSelection = {
  toolCallId: string;
  toolName: string;
  args: unknown;
  argsText?: string;
  result: unknown;
};

type AssistantThreadProps = {
  messages: ThreadMessage[];
  isRunning: boolean;
  statusText: string;
  suggestions: SuggestionItem[];
  onSubmit: (message: AppendMessage) => Promise<void>;
  onSuggestionClick: (question: string) => void;
  onToolClick: (selection: ToolSelection) => void;
};

function groupPartsByParentId(parts: readonly { parentId?: string }[]) {
  const groups = new Map<string, number[]>();

  for (let index = 0; index < parts.length; index += 1) {
    const groupId = parts[index]?.parentId ?? `__part_${index}`;
    const indices = groups.get(groupId) ?? [];
    indices.push(index);
    groups.set(groupId, indices);
  }

  return Array.from(groups.entries()).map(([groupId, indices]) => ({
    groupKey: groupId.startsWith("__part_") ? undefined : groupId,
    indices,
  }));
}

function MarkdownText({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className={`markdown-paragraph ${compact ? "compact" : ""}`}>{children}</p>,
        ul: ({ children }) => <ul className="markdown-list">{children}</ul>,
        ol: ({ children }) => <ol className="markdown-list ordered">{children}</ol>,
        li: ({ children }) => <li className="markdown-list-item">{children}</li>,
        strong: ({ children }) => <strong className="markdown-strong">{children}</strong>,
        em: ({ children }) => <em className="markdown-em">{children}</em>,
        code: ({ children, ...props }) =>
          String(props.className ?? "").includes("language-") ? (
            <code>{children}</code>
          ) : (
            <code className="markdown-inline-code">{children}</code>
          ),
        pre: ({ children }) => <pre className="markdown-code-block">{children}</pre>,
        table: ({ children }) => (
          <div className="markdown-table-wrap">
            <table className="markdown-table">{children}</table>
          </div>
        ),
        blockquote: ({ children }) => <blockquote className="markdown-blockquote">{children}</blockquote>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

function UserMessageBubble() {
  return (
    <MessagePrimitive.Root className="thread-message thread-message-user">
      <div className="message-bubble-user">
        <MessagePrimitive.Parts
          components={{
            Text: ({ text }) => <MarkdownText text={text} compact />,
          }}
        />
      </div>
    </MessagePrimitive.Root>
  );
}

function TextPart({ text }: { text: string }) {
  return <MarkdownText text={text} />;
}

function ToolFallback({ toolCallId, toolName, args, argsText, result, onToolClick }: ToolCallMessagePartProps & {
  onToolClick: (selection: ToolSelection) => void;
}) {
  const status = result !== undefined ? "完成" : "处理中";

  return (
    <button
      type="button"
      className="tool-inline-chip"
      aria-label={`工具 ${toolName} ${status}`}
      onClick={() =>
        onToolClick({
          toolCallId,
          toolName,
          args,
          argsText,
          result,
        })
      }
    >
      <span className="tool-inline-chip-icon">
        <Wrench size={12} />
      </span>
      <span className="tool-inline-chip-name">{toolName}</span>
      <span className={`tool-inline-chip-status ${result !== undefined ? "done" : "running"}`}>
        {result !== undefined ? <Check size={12} /> : <Loader2 size={12} className="spin" />}
      </span>
    </button>
  );
}

function AssistantMessageBubble({ onToolClick }: { onToolClick: (selection: ToolSelection) => void }) {
  return (
    <MessagePrimitive.Root className="thread-message thread-message-assistant">
      <div className="message-assistant-body">
        <MessagePrimitive.Unstable_PartsGrouped
          groupingFunction={groupPartsByParentId}
          components={{
            Text: TextPart,
            Reasoning: ({ text }) => <div className="message-status-note">{text}</div>,
            Group: ({ groupKey, children }) =>
              groupKey ? <div className="message-part-group">{children}</div> : <>{children}</>,
            tools: {
              Fallback: (props) => <ToolFallback {...props} onToolClick={onToolClick} />,
            },
          }}
        />
      </div>
    </MessagePrimitive.Root>
  );
}

function EmptyState({
  suggestions,
  onSuggestionClick,
}: {
  suggestions: SuggestionItem[];
  onSuggestionClick: (question: string) => void;
}) {
  const pageSize = 4;
  const totalPages = Math.max(1, Math.ceil(suggestions.length / pageSize));
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [suggestions]);

  const visibleSuggestions = useMemo(() => {
    const start = page * pageSize;
    return suggestions.slice(start, start + pageSize);
  }, [page, suggestions]);

  return (
    <div className="thread-empty-state">
      <div className="thread-empty-copy">
        <h2>提出一个问题，或从快捷卡片开始</h2>
        <p>系统会自动理解图谱、执行查询，并将工具过程收敛到详情面板。</p>
      </div>
      {suggestions.length > 0 ? (
        <div className="thread-suggestions-header">
          <span>快捷问题</span>
          <div className="thread-suggestions-nav">
            <button
              className="thread-suggestions-nav-button"
              onClick={() => setPage((current) => (current - 1 + totalPages) % totalPages)}
              aria-label="上一组快捷问题"
            >
              <ChevronLeft size={16} />
            </button>
            <span>
              {page + 1} / {totalPages}
            </span>
            <button
              className="thread-suggestions-nav-button"
              onClick={() => setPage((current) => (current + 1) % totalPages)}
              aria-label="下一组快捷问题"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      ) : null}
      <div className="thread-empty-suggestions">
        {visibleSuggestions.map((item) => (
          <button key={item.id} className="thread-suggestion-card" onClick={() => onSuggestionClick(item.question)}>
            <span>{item.question}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function AssistantThread({
  messages,
  isRunning,
  statusText,
  suggestions,
  onSubmit,
  onSuggestionClick,
  onToolClick,
}: AssistantThreadProps) {
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning,
    onNew: onSubmit,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="thread-root">
        <ThreadPrimitive.Viewport className="thread-viewport" autoScroll scrollToBottomOnRunStart>
          <ThreadPrimitive.Empty>
            <EmptyState suggestions={suggestions} onSuggestionClick={onSuggestionClick} />
          </ThreadPrimitive.Empty>

          <div className="thread-stream">
            <ThreadPrimitive.Messages>
              {({ message }) => {
                if (message.role === "user") {
                  return <UserMessageBubble />;
                }
                if (message.role === "assistant") {
                  return <AssistantMessageBubble onToolClick={onToolClick} />;
                }
                return null;
              }}
            </ThreadPrimitive.Messages>

            {isRunning ? (
              <div className="thread-live-status">
                <Loader2 size={14} className="spin" />
                <span>{statusText}</span>
              </div>
            ) : null}
          </div>
        </ThreadPrimitive.Viewport>

        <div className="thread-footer">
          <ThreadPrimitive.ScrollToBottom className="thread-scroll-bottom" aria-label="滚动到底部">
            <ArrowDown size={16} />
          </ThreadPrimitive.ScrollToBottom>

          <ComposerPrimitive.Root className="thread-composer">
            <button className="thread-composer-icon" type="button" aria-label="附加操作">
              <Plus size={17} />
            </button>
            <ComposerPrimitive.Input
              className="thread-composer-input"
              placeholder="输入问题，继续追问，或让 Agent 基于当前会话继续分析..."
              submitMode="enter"
              rows={1}
              maxRows={8}
            />
            <ComposerPrimitive.Send className="thread-composer-send" aria-label="发送">
              <SendHorizontal size={18} />
            </ComposerPrimitive.Send>
          </ComposerPrimitive.Root>
        </div>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
