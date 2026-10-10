import { act, renderHook } from "@testing-library/react";
import type { ChatResponse, ConversationDetail } from "../api/types";
import chat from "../test/fixtures/chat-response.json";
import { useChat } from "./useChat";

const response = chat as unknown as ChatResponse;
const detail: ConversationDetail = {
  conversation_id: response.conversation_id,
  title: "Boiler Feed Pump 101: High Vibration",
  created_at: "2026-10-10T09:00:00Z",
  updated_at: "2026-10-10T09:00:00Z",
  turns: [{ question: response.question, created_at: "2026-10-10T09:00:00Z", response }],
};

afterEach(() => vi.unstubAllGlobals());

test("opening a saved chat shows its answers as finished turns", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(detail)));
  const { result } = renderHook(() => useChat());
  await act(() => result.current.load(detail.conversation_id));
  expect(result.current.status).toBe("ready");
  expect(result.current.conversationId).toBe(detail.conversation_id);
  const [turn] = result.current.turns;
  expect(turn).toMatchObject({ question: response.question, phase: "done", traceId: response.trace_id });
  expect(turn.steps).toHaveLength(response.trace.length);
});

test.each([
  [404, "not-found"],
  [422, "not-found"],
  [503, "error"],
] as const)("an HTTP %i while opening a chat shows '%s'", async (status, expected) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "x" }, { status })));
  const { result } = renderHook(() => useChat());
  await act(() => result.current.load("conv-000000000000"));
  expect(result.current.status).toBe(expected);
  expect(result.current.conversationId).toBe(expected === "not-found" ? null : "conv-000000000000");
});

test("an answer that arrives after switching chats is ignored", async () => {
  let push: (text: string) => void = () => undefined;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      push = (text) => controller.enqueue(new TextEncoder().encode(text));
    },
  });
  const other: ConversationDetail = { ...detail, conversation_id: "conv-bbbbbbbbbbbb", turns: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.endsWith("/api/chat/stream")
        ? new Response(stream, { headers: { "Content-Type": "text/event-stream" } })
        : Response.json(other),
    ),
  );
  const { result } = renderHook(() => useChat());
  act(() => void result.current.ask("Investigate Boiler Feed Pump 101"));
  await act(() => result.current.load(other.conversation_id));
  await act(async () => {
    push(`event: answer\ndata: ${JSON.stringify(response)}\n\n`);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(result.current.conversationId).toBe(other.conversation_id);
  expect(result.current.turns).toEqual([]);
});

test("reset starts an empty new chat", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(detail)));
  const { result } = renderHook(() => useChat());
  await act(() => result.current.load(detail.conversation_id));
  act(() => result.current.reset());
  expect(result.current).toMatchObject({ conversationId: null, turns: [], status: "ready" });
});
