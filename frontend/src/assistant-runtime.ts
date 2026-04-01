import type {
  AppendMessage,
  MessageStatus,
  ThreadMessage,
  ToolCallMessagePart,
} from "@assistant-ui/react";

import type { BackendChatMessage, BackendToolCall, ChatStreamEvent } from "./types";

type ToolPartLocation = {
  messageIndex: number;
  partIndex: number;
};

const COMPLETE_STATUS: MessageStatus = { type: "complete", reason: "stop" };

function nowDate(): Date {
  return new Date();
}

function parseJson(value: string | undefined): unknown {
  if (!value) {
    return undefined;
  }
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function normalizeTextContent(value: unknown): string {
  if (typeof value === "string") {
    return value.replace(/\n{3,}/g, "\n\n").trim();
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object" && "text" in item) {
          return String((item as { text?: unknown }).text ?? "");
        }
        return "";
      })
      .join("")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).replace(/\n{3,}/g, "\n\n").trim();
}

function makeUserMessage(raw: BackendChatMessage): ThreadMessage {
  return {
    id: raw.id ?? crypto.randomUUID(),
    role: "user",
    createdAt: raw.created_at ? new Date(raw.created_at * 1000) : nowDate(),
    content: [
      {
        type: "text",
        text: normalizeTextContent(raw.content),
      },
    ],
    attachments: [],
    metadata: {
      custom: { rawId: raw.id ?? "" },
    },
  } as unknown as ThreadMessage;
}

function buildToolCallPart(toolCall: BackendToolCall): ToolCallMessagePart {
  const argsText = toolCall.function?.arguments ?? "";
  const args = parseJson(argsText);
  return {
    type: "tool-call",
    toolCallId: toolCall.id,
    toolName: toolCall.function?.name ?? "tool",
    args: (typeof args === "object" && args !== null ? args : {}) as unknown as ToolCallMessagePart["args"],
    argsText,
  };
}

export function rawMessagesToThreadMessages(rawMessages: BackendChatMessage[]): ThreadMessage[] {
  const threadMessages: ThreadMessage[] = [];
  const toolPartIndex = new Map<string, ToolPartLocation>();

  for (const raw of rawMessages) {
    if (raw.role === "user") {
      threadMessages.push(makeUserMessage(raw));
      continue;
    }

    if (raw.role === "assistant") {
      const text = normalizeTextContent(raw.content);
      const content: Array<{ type: "text"; text: string } | ToolCallMessagePart> = [];

      if (text) {
        content.push({
          type: "text",
          text,
        });
      }

      for (const toolCall of raw.toolCalls ?? []) {
        const toolPart = buildToolCallPart(toolCall);
        toolPartIndex.set(toolCall.id, { messageIndex: threadMessages.length, partIndex: content.length });
        content.push(toolPart);
      }

      if (content.length === 0) {
        continue;
      }

      threadMessages.push({
        id: raw.id ?? crypto.randomUUID(),
        role: "assistant",
        createdAt: raw.created_at ? new Date(raw.created_at * 1000) : nowDate(),
        content,
        status: COMPLETE_STATUS,
        metadata: {
          custom: { rawId: raw.id ?? "" },
        },
      } as unknown as ThreadMessage);
      continue;
    }

    if (raw.role === "tool" && raw.toolCallId) {
      const target = toolPartIndex.get(raw.toolCallId);
      if (!target) {
        continue;
      }
      const message = threadMessages[target.messageIndex];
      if (!message || message.role !== "assistant") {
        continue;
      }

      const content = [...message.content];
      const part = content[target.partIndex];
      if (!part || part.type !== "tool-call") {
        continue;
      }

      content[target.partIndex] = {
        ...part,
        result: parseJson(normalizeTextContent(raw.content)),
      };

      threadMessages[target.messageIndex] = {
        ...message,
        content,
      };
    }
  }

  return threadMessages;
}

export function appendUserRawMessage(messages: BackendChatMessage[], question: string): BackendChatMessage[] {
  return [
    ...messages,
    {
      id: crypto.randomUUID(),
      role: "user",
      content: question,
    },
  ];
}

export function extractTextFromAppendMessage(message: AppendMessage): string {
  if (typeof message.content === "string") {
    return normalizeTextContent(message.content);
  }

  return message.content
    .map((part) => {
      if (part.type === "text") {
        return part.text;
      }
      return "";
    })
    .join("")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function applyStreamEventToRawMessages(
  rawMessages: BackendChatMessage[],
  event: ChatStreamEvent,
): BackendChatMessage[] {
  const next: BackendChatMessage[] = rawMessages.map((message): BackendChatMessage => ({
    ...message,
    toolCalls: message.toolCalls
      ? message.toolCalls.map((toolCall) => ({
          ...toolCall,
          function: toolCall.function ? { ...toolCall.function } : undefined,
        }))
      : undefined,
  }));

  switch (event.type) {
    case "TOOL_CALL_START": {
      const parentMessageId = String(event.parentMessageId ?? crypto.randomUUID());
      const toolCallId = String(event.toolCallId ?? crypto.randomUUID());
      const toolName = String(event.toolCallName ?? "tool");
      const existing = next.find((message) => message.id === parentMessageId);
      if (existing) {
        existing.toolCalls = existing.toolCalls ?? [];
        if (!existing.toolCalls.some((toolCall) => toolCall.id === toolCallId)) {
          existing.toolCalls.push({
            id: toolCallId,
            type: "function",
            function: {
              name: toolName,
              arguments: "",
            },
          });
        }
      } else {
        next.push({
          id: parentMessageId,
          role: "assistant",
          content: "",
          toolCalls: [
            {
              id: toolCallId,
              type: "function",
              function: {
                name: toolName,
                arguments: "",
              },
            },
          ],
        });
      }
      return next;
    }

    case "TOOL_CALL_ARGS": {
      const toolCallId = String(event.toolCallId ?? "");
      const delta = String(event.delta ?? "");
      for (const message of next) {
        for (const toolCall of message.toolCalls ?? []) {
          if (toolCall.id === toolCallId) {
            toolCall.function = {
              name: toolCall.function?.name,
              arguments: delta,
            };
            return next;
          }
        }
      }
      return next;
    }

    case "TOOL_CALL_RESULT": {
      next.push({
        id: String(event.messageId ?? crypto.randomUUID()),
        role: "tool",
        toolCallId: String(event.toolCallId ?? ""),
        content: String(event.content ?? ""),
      });
      return next;
    }

    case "TEXT_MESSAGE_START": {
      const messageId = String(event.messageId ?? crypto.randomUUID());
      if (!next.some((message) => message.id === messageId)) {
        next.push({
          id: messageId,
          role: "assistant",
          content: "",
        });
      }
      return next;
    }

    case "TEXT_MESSAGE_CONTENT": {
      const messageId = String(event.messageId ?? "");
      const delta = String(event.delta ?? "");
      const target = next.find((message) => message.id === messageId);
      if (target) {
        const existingContent = typeof target.content === "string" ? target.content : normalizeTextContent(target.content);
        target.content = `${existingContent}${delta}`.replace(/\n{3,}/g, "\n\n");
      }
      return next;
    }

    default:
      return next;
  }
}

export function humanizeEvent(event: ChatStreamEvent): string {
  switch (event.type) {
    case "RUN_STARTED":
      return "开始执行";
    case "STEP_STARTED":
      return `步骤：${String(event.stepName ?? "")}`;
    case "TOOL_CALL_START":
      return `调用工具：${String(event.toolCallName ?? "")}`;
    case "TOOL_CALL_RESULT":
      return "工具返回结果";
    case "TEXT_MESSAGE_START":
      return "生成回答";
    case "RUN_FINISHED":
      return "执行完成";
    case "RUN_ERROR":
      return `执行失败：${String(event.message ?? "")}`;
    default:
      return String(event.type ?? "事件");
  }
}

export function statusFromEvent(event: ChatStreamEvent): string | null {
  switch (event.type) {
    case "RUN_STARTED":
      return "正在思考";
    case "STEP_STARTED":
      return "正在思考";
    case "DECISION_ISSUE":
      return "正在思考";
    case "TOOL_CALL_START":
      return "正在执行";
    case "TEXT_MESSAGE_START":
      return "正在生成回答";
    case "RUN_FINISHED":
      return "已完成";
    case "RUN_ERROR":
      return "执行失败";
    default:
      return null;
  }
}
