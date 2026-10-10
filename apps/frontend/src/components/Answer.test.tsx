import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatResponse } from "../api/types";
import chat from "../test/fixtures/chat-response.json";
import hello from "../test/fixtures/hello-response.json";
import { Answer } from "./Answer";

const response = chat as unknown as ChatResponse;
const greeting = hello as unknown as ChatResponse;

function renderAnswer(overrides: { response?: ChatResponse; selectedKey?: string | null; canAsk?: boolean } = {}) {
  const handlers = { onCite: vi.fn(), onAsk: vi.fn(), onShowEvidence: vi.fn() };
  render(
    <Answer
      response={overrides.response ?? response}
      selectedKey={overrides.selectedKey ?? null}
      canAsk={overrides.canAsk ?? true}
      {...handlers}
    />,
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

test("'Show evidence' opens the drawer", async () => {
  const { onShowEvidence } = renderAnswer();
  await userEvent.click(screen.getByRole("button", { name: "Show evidence" }));
  expect(onShowEvidence).toHaveBeenCalled();
});

test("offers follow-up questions on the latest answer only", async () => {
  const { onAsk } = renderAnswer();
  await userEvent.click(screen.getByRole("button", { name: "Which operating procedure applies to this alarm?" }));
  expect(onAsk).toHaveBeenCalledWith("Which operating procedure applies to this alarm?");
});

test("older answers do not offer follow-ups", () => {
  renderAnswer({ canAsk: false });
  expect(screen.queryByRole("navigation", { name: "Suggested next questions" })).not.toBeInTheDocument();
});

test("a greeting is a short reply with starter questions", () => {
  renderAnswer({ response: greeting });
  expect(screen.getByText(/I investigate plant alarms/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show evidence" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("button")).toHaveLength(greeting.answer.suggestions.length);
});

test("text from documents is shown as text, never as HTML", () => {
  const hostile = structuredClone(response);
  hostile.answer.summary = '<img src=x onerror="alert(1)"> pump is fine';
  renderAnswer({ response: hostile, canAsk: false });
  expect(screen.getByText(/<img src=x onerror="alert\(1\)"> pump is fine/)).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
});
