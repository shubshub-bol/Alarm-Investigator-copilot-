import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatResponse, ConversationSummary } from "./api/types";
import { App } from "./App";
import chat from "./test/fixtures/chat-response.json";
import health from "./test/fixtures/health.json";
import streamText from "./test/fixtures/chat-stream.txt?raw";
import tools from "./test/fixtures/tools.json";

const QUESTION = "Investigate Boiler Feed Pump 101";
const response = chat as unknown as ChatResponse;
const SAVED: ConversationSummary = {
  conversation_id: "conv-a14c5ce4373f", // the id in the recorded stream
  title: "Boiler Feed Pump 101: High Vibration",
  created_at: "2026-10-10T09:00:00Z",
  updated_at: "2026-10-10T09:00:00Z",
};
const DETAIL = { ...SAVED, turns: [{ question: response.question, created_at: SAVED.created_at, response }] };

/** A stream that hands over the recorded SSE text in small pieces, like the network does. */
function sseResponse(text: string, chunkSize = 700): Response {
  const bytes = new TextEncoder().encode(text);
  let offset = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= bytes.length) return controller.close();
      controller.enqueue(bytes.slice(offset, offset + chunkSize));
      offset += chunkSize;
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

const openSaved = async (id: string) =>
  id === DETAIL.conversation_id
    ? Response.json(DETAIL)
    : Response.json({ detail: "Conversation not found" }, { status: 404 });

interface Backend {
  chat?: () => Promise<Response>;
  saved?: ConversationSummary[]; // what GET /api/conversations returns (read at call time)
  open?: (id: string) => Promise<Response>;
  reachable?: () => boolean; // false: every request fails as if the backend were not listening yet
  checkHealth?: () => Response;
}

function mockBackend({
  chat = async () => sseResponse(streamText),
  saved = [],
  open = openSaved,
  reachable = () => true,
  checkHealth = () => Response.json(health),
}: Backend = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!reachable()) throw new TypeError("Failed to fetch");
      if (url.endsWith("/api/health")) return checkHealth();
      if (url.endsWith("/api/tools")) return Response.json(tools);
      if (url.endsWith("/api/examples")) return Response.json({ examples: [QUESTION] });
      if (url.endsWith("/api/chat/stream")) return chat();
      if (url.endsWith("/api/conversations")) return Response.json({ conversations: saved });
      const id = /\/api\/conversations\/([^/]+)$/.exec(url)?.[1];
      if (id && init?.method === "DELETE") return new Response(null, { status: 204 });
      if (id) return open(decodeURIComponent(id));
      return new Response("not found", { status: 404 });
    }),
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** Health is checked every 5 s while the backend is unreachable and every 30 s once it answers. */
async function waitForHealthCheck(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function askAndWait(question = QUESTION) {
  await userEvent.type(screen.getByRole("textbox", { name: "Question" }), `${question}{Enter}`);
  return screen.findByRole("article", { name: "Answer" });
}

const sidebar = () => screen.getByRole("navigation", { name: "Chats" });

test("starts simple: a greeting, the input, starter questions, no evidence panel", async () => {
  mockBackend();
  render(<App />);
  expect(await screen.findByText("Ready")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "What would you like to investigate?" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: QUESTION })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("an answer links to its steps: 8 alarm tool calls and 4 document searches", async () => {
  mockBackend();
  render(<App />);
  const answer = await askAndWait();
  expect(within(answer).getByRole("button", { name: /^12 steps in / })).toBeInTheDocument();
  expect(screen.queryByText(/^Checked /)).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a new chat gets its address and appears in the list after its first answer", async () => {
  const saved: ConversationSummary[] = [];
  mockBackend({
    saved,
    chat: async () => {
      saved.push(SAVED); // the backend saves the chat before it sends the answer
      return sseResponse(streamText);
    },
  });
  render(<App />);
  await askAndWait();
  await waitFor(() => expect(window.location.pathname).toBe("/c/conv-a14c5ce4373f"));
  expect(await within(sidebar()).findByRole("button", { name: SAVED.title })).toHaveAttribute("aria-current", "page");
});

test("an answer that could not be saved keeps the address, so a reload does not open a missing chat", async () => {
  mockBackend({ chat: async () => sseResponse(streamText.replace('"saved": true', '"saved": false')) });
  render(<App />);
  await askAndWait();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50)); // give the address time to change, if it were going to
  });
  expect(window.location.pathname).toBe("/");
});

test("a degraded backend (HTTP 503) is shown as degraded, not as unreachable", async () => {
  const degraded = { ...health, status: "degraded", mcp: { ...health.mcp, connected: false, tools: 0 } };
  mockBackend({ checkHealth: () => Response.json(degraded, { status: 503 }) });
  render(<App />);
  expect(await screen.findByText("Alarm data unavailable")).toBeInTheDocument();
  expect(screen.queryByText("Copilot service unreachable")).not.toBeInTheDocument();
});

test("opening a chat's address shows its saved answers", async () => {
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  mockBackend({ saved: [SAVED] });
  render(<App />);
  expect(await screen.findByRole("article", { name: "Answer" })).toBeInTheDocument();
  expect(screen.getByText(response.question)).toBeInTheDocument();
});

test("an address for a deleted chat says so and offers a new chat", async () => {
  window.history.replaceState(null, "", "/c/conv-000000000000");
  mockBackend();
  render(<App />);
  expect(await screen.findByRole("heading", { name: "This chat doesn't exist" })).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("main")).getByRole("button", { name: "New chat" }));
  expect(window.location.pathname).toBe("/");
  expect(screen.getByRole("heading", { name: "What would you like to investigate?" })).toBeInTheDocument();
});

test("a chat that can't be opened yet can be tried again", async () => {
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  let down = true;
  mockBackend({
    saved: [SAVED],
    open: async (id) => (down ? new Response("starting", { status: 503 }) : openSaved(id)),
  });
  render(<App />);
  await screen.findByText("Ready"); // the first health check has answered, so its automatic retry has run
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Couldn't open this chat");
  down = false;
  await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("article", { name: "Answer" })).toBeInTheDocument();
});

test("a chat reloaded while the backend was down opens by itself once the backend is back", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  let up = false;
  mockBackend({ saved: [SAVED], reachable: () => up });
  render(<App />);
  expect(await screen.findByText("Copilot service unreachable")).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't open this chat");
  up = true;
  await waitForHealthCheck(5_000);
  expect(await screen.findByRole("article", { name: "Answer" })).toBeInTheDocument();
});

test("a chat that failed to open while the backend was up opens by itself at the next health check", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  let busy = true;
  mockBackend({
    saved: [SAVED],
    open: async (id) => (busy ? new Response("storage busy", { status: 503 }) : openSaved(id)),
  });
  render(<App />);
  await screen.findByText("Ready");
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't open this chat");
  busy = false;
  await waitForHealthCheck(30_000);
  expect(await screen.findByRole("article", { name: "Answer" })).toBeInTheDocument();
});

test("New chat in the sidebar goes back to an empty chat", async () => {
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  mockBackend({ saved: [SAVED] });
  render(<App />);
  await screen.findByRole("article", { name: "Answer" });
  await userEvent.click(within(sidebar()).getByRole("button", { name: "New chat" }));
  expect(window.location.pathname).toBe("/");
  expect(screen.queryByRole("article", { name: "Answer" })).not.toBeInTheDocument();
});

test("deleting the open chat goes back to a new chat", async () => {
  window.history.replaceState(null, "", "/c/conv-a14c5ce4373f");
  mockBackend({ saved: [SAVED] });
  render(<App />);
  await screen.findByRole("article", { name: "Answer" });
  await userEvent.click(await within(sidebar()).findByRole("button", { name: `Options for ${SAVED.title}` }));
  await userEvent.click(within(sidebar()).getByRole("button", { name: "Delete" }));
  await userEvent.click(within(screen.getByRole("dialog", { name: "Delete chat?" })).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(window.location.pathname).toBe("/"));
  expect(within(sidebar()).queryByRole("button", { name: SAVED.title })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "What would you like to investigate?" })).toBeInTheDocument();
});

test("'Alarm details' opens the alarm summary in a drawer that closes with Escape", async () => {
  mockBackend();
  render(<App />);
  const answer = await askAndWait();

  await userEvent.click(within(answer).getByRole("button", { name: "Alarm details" }));
  const drawer = screen.getByRole("dialog", { name: "Evidence for this answer" });
  expect(within(drawer).getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
  expect(within(drawer).getByRole("heading", { name: "Boiler Feed Pump 101" })).toBeInTheDocument();

  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a tool chip opens the step it came from", async () => {
  mockBackend();
  render(<App />);
  const answer = await askAndWait();

  await userEvent.click(within(answer).getAllByRole("button", { name: /Tool fact \d+: show evidence/ })[0]);

  const drawer = screen.getByRole("dialog");
  expect(within(drawer).getByRole("tab", { name: /Steps/ })).toHaveAttribute("aria-selected", "true");
  const highlighted = drawer.querySelector('.trace-step[data-target="true"]');
  expect(highlighted).not.toBeNull();
  expect(highlighted).toHaveAttribute("open");
});

test("a document chip opens the cited passage", async () => {
  mockBackend();
  render(<App />);
  const answer = await askAndWait();

  await userEvent.click(within(answer).getAllByRole("button", { name: /Document source 1: show evidence/ })[0]);

  const drawer = screen.getByRole("dialog");
  expect(within(drawer).getByRole("tab", { name: /Sources/ })).toHaveAttribute("aria-selected", "true");
  expect(drawer.querySelector('.source[data-target="true"]')).toHaveTextContent("S1");
});

test("a failed request explains itself and can be retried", async () => {
  let calls = 0;
  mockBackend({
    chat: async () => {
      calls += 1;
      return calls === 1 ? new Response("upstream down", { status: 503 }) : sseResponse(streamText);
    },
  });
  render(<App />);
  await userEvent.type(screen.getByRole("textbox", { name: "Question" }), `${QUESTION}{Enter}`);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("HTTP 503");
  await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByRole("article", { name: "Answer" })).toBeInTheDocument());
});

test("an unreachable backend is the one thing the status shows", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
  render(<App />);
  expect(await screen.findByText("Copilot service unreachable")).toBeInTheDocument();
});
