import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatResponse } from "../api/types";
import chat from "../test/fixtures/chat-response.json";
import hello from "../test/fixtures/hello-response.json";
import { Answer } from "./Answer";

const response = chat as unknown as ChatResponse;
const greeting = hello as unknown as ChatResponse;

function renderAnswer(props: Partial<Parameters<typeof Answer>[0]> = {}) {
  const onCite = vi.fn();
  const onAsk = vi.fn();
  render(<Answer response={response} selectedKey={null} onCite={onCite} onAsk={onAsk} canAsk {...props} />);
  return { onCite, onAsk };
}

test("shows the priority, the asset and the recommended actions", () => {
  renderAnswer();
  expect(screen.getByTitle(/^P1/)).toBeInTheDocument();
  expect(screen.getByText(/Boiler Feed Pump 101, High Vibration/)).toBeInTheDocument();
  const actions = screen.getByRole("heading", { name: "Recommended actions" }).closest("section")!;
  expect(within(actions).getAllByRole("listitem").length).toBe(response.answer.recommended_actions.length);
});

test("every action carries its evidence chips", () => {
  renderAnswer();
  const actions = screen.getByRole("heading", { name: "Recommended actions" }).closest("section")!;
  for (const item of within(actions).getAllByRole("listitem")) {
    expect(within(item).getAllByRole("button", { name: /show evidence/ }).length).toBeGreaterThan(0);
  }
});

test("flags the API advice that conflicts with the procedure", () => {
  renderAnswer();
  const table = screen.getByRole("table");
  const conflictRow = within(table).getByText("Conflict").closest("tr")!;
  expect(conflictRow).toHaveTextContent("Continue operation");
});

test("clicking a chip asks for that evidence", async () => {
  const { onCite } = renderAnswer();
  const [first] = screen.getAllByRole("button", { name: /Document source 1: show evidence/ });
  await userEvent.click(first);
  expect(onCite).toHaveBeenCalledWith("S1");
});

test("the selected chip is marked as pressed", () => {
  renderAnswer({ selectedKey: "S1" });
  for (const chip of screen.getAllByRole("button", { name: /Document source 1:/ })) {
    expect(chip).toHaveAttribute("aria-pressed", "true");
  }
});

test("offers follow-up questions", async () => {
  const { onAsk } = renderAnswer();
  await userEvent.click(screen.getByRole("button", { name: "Which operating procedure applies to this alarm?" }));
  expect(onAsk).toHaveBeenCalledWith("Which operating procedure applies to this alarm?");
});

test("older answers do not offer follow-ups", () => {
  renderAnswer({ canAsk: false });
  expect(screen.queryByRole("navigation", { name: "Suggested next questions" })).not.toBeInTheDocument();
});

test("a greeting is a short reply with starter questions and no evidence sections", () => {
  render(<Answer response={greeting} selectedKey={null} onCite={vi.fn()} onAsk={vi.fn()} canAsk />);
  expect(screen.getByText(/I investigate plant alarms/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getAllByRole("button").length).toBe(greeting.answer.suggestions.length);
});

test("text from documents is shown as text, never as HTML", () => {
  const hostile = structuredClone(response);
  hostile.answer.summary = '<img src=x onerror="alert(1)"> pump is fine';
  render(<Answer response={hostile} selectedKey={null} onCite={vi.fn()} onAsk={vi.fn()} canAsk={false} />);
  expect(screen.getByText(/<img src=x onerror="alert\(1\)"> pump is fine/)).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
});
