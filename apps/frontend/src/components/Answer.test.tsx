import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatResponse } from "../api/types";
import chat from "../test/fixtures/chat-response.json";
import hello from "../test/fixtures/hello-response.json";
import { Answer } from "./Answer";

const response = chat as unknown as ChatResponse;
const greeting = hello as unknown as ChatResponse;

function renderAnswer(overrides: { response?: ChatResponse; selectedKey?: string | null } = {}) {
  const handlers = { onCite: vi.fn(), onOpen: vi.fn() };
  render(
    <Answer response={overrides.response ?? response} selectedKey={overrides.selectedKey ?? null} {...handlers} />,
  );
  return handlers;
}

test("shows the priority, the subject and what to do", () => {
  renderAnswer();
  expect(screen.getByTitle(/^P1/)).toBeInTheDocument();
  expect(screen.getByText("Boiler Feed Pump 101, High Vibration")).toBeInTheDocument();
  const todo = screen.getByRole("heading", { name: "What to do" }).closest("section")!;
  expect(within(todo).getAllByRole("listitem")).toHaveLength(response.answer.recommended_actions.length);
});

test("keeps the answer short: details stay in the evidence drawer", () => {
  renderAnswer();
  expect(screen.queryByText("Likely causes")).not.toBeInTheDocument();
  expect(screen.queryByText("Measured data")).not.toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

test("every action carries its evidence chips", () => {
  renderAnswer();
  const todo = screen.getByRole("heading", { name: "What to do" }).closest("section")!;
  for (const item of within(todo).getAllByRole("listitem")) {
    expect(within(item).getAllByRole("button", { name: /show evidence/ }).length).toBeGreaterThan(0);
  }
});

test("warns when the API advice contradicts the procedure", () => {
  renderAnswer();
  const warning = screen.getByRole("note");
  expect(warning).toHaveTextContent("Check before acting");
  expect(warning).toHaveTextContent("Continue operation and monitor vibration trend for 24 hours");
});

test("a conflict without document text does not end the warning with 'say:'", () => {
  const bare = structuredClone(response);
  const conflict = bare.answer.action_assessments.find((a) => a.verdict === "conflict")!;
  conflict.document_guidance = null;
  renderAnswer({ response: bare });
  const warning = screen.getByRole("note");
  expect(warning).not.toHaveTextContent("the documents say:");
  expect(warning).toHaveTextContent("but the documents disagree.");
});

test("clicking a chip asks for that evidence", async () => {
  const { onCite } = renderAnswer();
  await userEvent.click(screen.getAllByRole("button", { name: /Document source 1: show evidence/ })[0]);
  expect(onCite).toHaveBeenCalledWith("S1");
});

test("the selected chip is marked as pressed", () => {
  renderAnswer({ selectedKey: "S1" });
  for (const chip of screen.getAllByRole("button", { name: /Document source 1:/ })) {
    expect(chip).toHaveAttribute("aria-pressed", "true");
  }
});

const evidenceLinks = () => screen.getByRole("navigation", { name: "Where this answer comes from" });

test("each link says what is behind it and opens that view", async () => {
  const { onOpen } = renderAnswer();
  const links = within(evidenceLinks());
  await userEvent.click(links.getByRole("button", { name: "Alarm details" }));
  await userEvent.click(links.getByRole("button", { name: "7 sources" }));
  await userEvent.click(links.getByRole("button", { name: "12 steps in 898 ms" }));
  expect(onOpen.mock.calls).toEqual([["evidence"], ["sources"], ["steps"]]);
  expect(screen.queryByRole("button", { name: "Show evidence" })).not.toBeInTheDocument();
});

test("only links with something behind them are shown", () => {
  const documentsOnly = structuredClone(response);
  Object.assign(documentsOnly.evidence, { asset: null, focus_alarm: null, top_alarm: null, ranked_alarms: [] });
  documentsOnly.evidence.active_alarms = [];
  documentsOnly.answer.citations = [];
  renderAnswer({ response: documentsOnly });
  const links = within(evidenceLinks());
  expect(links.queryByRole("button", { name: "Alarm details" })).not.toBeInTheDocument();
  expect(links.queryByRole("button", { name: /sources?$/ })).not.toBeInTheDocument();
  expect(links.getByRole("button", { name: /^12 steps in/ })).toBeInTheDocument();
});

test("failed steps are called out on the steps link", () => {
  const partial = structuredClone(response);
  partial.trace[1].status = "error";
  renderAnswer({ response: partial });
  expect(within(evidenceLinks()).getByRole("button", { name: "12 steps in 898 ms, 1 failed" })).toBeInTheDocument();
});

test("no 'Ask next' questions under an answer", () => {
  renderAnswer();
  expect(screen.queryByText("Ask next")).not.toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Suggested next questions" })).not.toBeInTheDocument();
});

test("a greeting is just the reply", () => {
  renderAnswer({ response: greeting });
  expect(screen.getByText(/I investigate plant alarms/)).toBeInTheDocument();
  expect(screen.queryAllByRole("button")).toHaveLength(0);
});

test("text from documents is shown as text, never as HTML", () => {
  const hostile = structuredClone(response);
  hostile.answer.summary = '<img src=x onerror="alert(1)"> pump is fine';
  renderAnswer({ response: hostile });
  expect(screen.getByText(/<img src=x onerror="alert\(1\)"> pump is fine/)).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
});

test("shows the model's analysis with its evidence chips", () => {
  const withAnalysis = structuredClone(response);
  withAnalysis.answer.analysis = [
    { text: "The motor bearing heats up first, so misalignment is likely.", citations: ["T4"] },
  ];
  renderAnswer({ response: withAnalysis });
  const analysis = screen.getByRole("heading", { name: "Analysis (AI)" }).closest("section")!;
  const item = within(analysis).getByRole("listitem");
  expect(item).toHaveTextContent("misalignment is likely");
  expect(within(item).getAllByRole("button", { name: /show evidence/ })).toHaveLength(1);
});

test("no analysis section when the answer has none, as in chats saved before it existed", () => {
  renderAnswer();
  expect(screen.queryByRole("heading", { name: "Analysis (AI)" })).not.toBeInTheDocument();
});
