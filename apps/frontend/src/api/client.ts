import { createSseParser } from "./sse";
import type {
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  Discovery,
  Health,
  ToolsResponse,
  TraceStep,
  Understanding,
} from "./types";

// Empty = same origin (the Vite dev proxy or nginx forwards /api to the backend).
const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export const UNREACHABLE =
  "Can't reach the copilot service. Check that the backend is running (python -m copilot), then try again.";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

async function describeFailure(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (Array.isArray(detail) && detail[0]?.msg) return `The question was rejected: ${detail[0].msg}.`;
    if (typeof detail === "string") return detail;
  } catch {
    /* not JSON */
  }
  return `The copilot service answered with HTTP ${res.status}.`;
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(UNREACHABLE);
  }
  if (!res.ok) throw new ApiError(await describeFailure(res), res.status);
  return res;
}

async function getJson<T>(path: string): Promise<T> {
  return (await request(path)).json() as Promise<T>;
}

/** The backend answers 503 while degraded; its body is the same report, so the GUI can say what is wrong. */
export async function getHealth(): Promise<Health> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/health`);
  } catch {
    throw new ApiError(UNREACHABLE);
  }
  if (!res.ok && res.status !== 503) throw new ApiError(await describeFailure(res), res.status);
  return res.json() as Promise<Health>; // a 503 without the report (another proxy) fails here: unreachable
}
export const getTools = () => getJson<ToolsResponse>("/api/tools");
export const getExamples = () => getJson<{ examples: string[] }>("/api/examples");

const conversationPath = (id: string) => `/api/conversations/${encodeURIComponent(id)}`;

export const listConversations = () => getJson<{ conversations: ConversationSummary[] }>("/api/conversations");

export async function getConversation(id: string, signal?: AbortSignal): Promise<ConversationDetail> {
  return (await request(conversationPath(id), { signal })).json() as Promise<ConversationDetail>;
}

export async function renameConversation(id: string, title: string): Promise<ConversationSummary> {
  const res = await request(conversationPath(id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return res.json() as Promise<ConversationSummary>;
}

export async function deleteConversation(id: string): Promise<void> {
  await request(conversationPath(id), { method: "DELETE" });
}

export interface StreamHandlers {
  started?: (data: { trace_id: string; conversation_id: string }) => void;
  understanding?: (data: Understanding) => void;
  discovery?: (data: Discovery) => void;
  step?: (data: TraceStep) => void;
  answer: (data: ChatResponse) => void;
  error: (message: string, traceId?: string | null) => void;
}

/** POST a question and receive progress events until the answer (or an error) arrives. */
export async function streamChat(
  message: string,
  conversationId: string | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await request("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!res.body) throw new ApiError("The copilot service sent an empty response.");

  let finished = false;
  const feed = createSseParser(({ event, data }) => {
    const payload = JSON.parse(data);
    switch (event) {
      case "started":
        handlers.started?.(payload);
        break;
      case "understanding":
        handlers.understanding?.(payload);
        break;
      case "discovery":
        handlers.discovery?.(payload);
        break;
      case "step":
        handlers.step?.(payload);
        break;
      case "answer":
        finished = true;
        handlers.answer(payload);
        break;
      case "error":
        finished = true;
        handlers.error(payload.message, payload.trace_id);
        break;
    }
  });

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      feed(value);
    }
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError("The connection to the copilot service dropped. Try again.");
  }
  if (!finished) handlers.error("The connection closed before the answer arrived. Try again.");
}
