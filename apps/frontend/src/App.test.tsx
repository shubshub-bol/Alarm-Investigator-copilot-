import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import health from "./test/fixtures/health.json";
import streamText from "./test/fixtures/chat-stream.txt?raw";
import tools from "./test/fixtures/tools.json";

const QUESTION = "Investigate Boiler Feed Pump 101";

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

function mockBackend(chat: () => Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/health")) return Response.json(health);
      if (url.endsWith("/api/tools")) return Response.json(tools);
      if (url.endsWith("/api/examples")) return Response.json({ examples: [QUESTION] });
      if (url.endsWith("/api/chat/stream")) return chat();
      return new Response("not found", { status: 404 });
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

async function askAndWait(question = QUESTION) {
  await userEvent.type(screen.getByRole("textbox", { name: "Question" }), `${question}{Enter}`);
  return screen.findByRole("article", { name: "Answer" });
}

test("starts simple: one status, starter questions, no evidence panel", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  expect(await screen.findByText("Ready")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: QUESTION })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("answers with a one-line summary of the work done", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  await askAndWait();
  expect(screen.getByText(/Checked 8 alarm tools and 4 document searches in/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("'Show evidence' opens the alarm summary in a drawer that closes with Escape", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  const answer = await askAndWait();

  await userEvent.click(within(answer).getByRole("button", { name: "Show evidence" }));
  const drawer = screen.getByRole("dialog", { name: "Evidence for this answer" });
  expect(within(drawer).getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
  expect(within(drawer).getByRole("heading", { name: "Boiler Feed Pump 101" })).toBeInTheDocument();

  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a tool chip opens the step it came from", async () => {
  mockBackend(async () => sseResponse(streamText));
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
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  const answer = await askAndWait();

  await userEvent.click(within(answer).getAllByRole("button", { name: /Document source 1: show evidence/ })[0]);

  const drawer = screen.getByRole("dialog");
  expect(within(drawer).getByRole("tab", { name: /Sources/ })).toHaveAttribute("aria-selected", "true");
  expect(drawer.querySelector('.source[data-target="true"]')).toHaveTextContent("S1");
});

test("a failed request explains itself and can be retried", async () => {
  let calls = 0;
  mockBackend(async () => {
    calls += 1;
    return calls === 1 ? new Response("upstream down", { status: 503 }) : sseResponse(streamText);
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
