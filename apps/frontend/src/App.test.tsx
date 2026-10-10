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

async function ask(question: string) {
  const box = screen.getByRole("textbox", { name: "Question" });
  await userEvent.type(box, `${question}{Enter}`);
}

test("shows backend status and starter questions", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  expect(await screen.findByText("13 available")).toBeInTheDocument();
  expect(screen.getByText("54 passages")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: QUESTION })).toBeInTheDocument();
});

test("streams the investigation, then shows the answer and its evidence", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  await ask(QUESTION);

  expect(await screen.findByRole("article", { name: "Answer" })).toBeInTheDocument();
  expect(screen.getByText(/12 steps in/)).toBeInTheDocument();
  // the rail switches to the alarm summary when the answer arrives
  expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("heading", { name: "Boiler Feed Pump 101" })).toBeInTheDocument();
});

test("a tool chip opens the trace step it came from", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  await ask(QUESTION);
  const answer = await screen.findByRole("article", { name: "Answer" });

  const chip = within(answer).getAllByRole("button", { name: /Tool fact \d+: show evidence/ })[0];
  await userEvent.click(chip);

  expect(screen.getByRole("tab", { name: /Trace/ })).toHaveAttribute("aria-selected", "true");
  const highlighted = document.querySelector('.trace-step[data-target="true"]');
  expect(highlighted).not.toBeNull();
  expect(highlighted).toHaveAttribute("open");
});

test("a document chip opens the cited passage", async () => {
  mockBackend(async () => sseResponse(streamText));
  render(<App />);
  await ask(QUESTION);
  const answer = await screen.findByRole("article", { name: "Answer" });

  await userEvent.click(within(answer).getAllByRole("button", { name: /Document source 1: show evidence/ })[0]);

  expect(screen.getByRole("tab", { name: /Sources/ })).toHaveAttribute("aria-selected", "true");
  const passage = document.querySelector('.source[data-target="true"]');
  expect(passage).toHaveTextContent("S1");
});

test("a failed request explains itself and can be retried", async () => {
  let calls = 0;
  mockBackend(async () => {
    calls += 1;
    return calls === 1 ? new Response("upstream down", { status: 503 }) : sseResponse(streamText);
  });
  render(<App />);
  await ask(QUESTION);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("HTTP 503");
  await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByRole("article", { name: "Answer" })).toBeInTheDocument());
});

test("an unreachable backend is shown in the status bar", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
  render(<App />);
  expect(await screen.findByText("unreachable")).toBeInTheDocument();
});
