import { render, screen, within } from "@testing-library/react";
import type { ChatResponse } from "../../api/types";
import type { Turn } from "../../state/useChat";
import chat from "../../test/fixtures/chat-response.json";
import { EvidencePanel } from "./EvidencePanel";

const response = chat as unknown as ChatResponse;
const turn: Turn = {
  id: "turn-x",
  question: response.question,
  phase: "done",
  startedAt: 0,
  steps: response.trace,
  response,
};

test("shows the alarm summary", () => {
  render(<EvidencePanel turn={turn} selectedKey={null} onCite={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "Boiler Feed Pump 101" })).toBeInTheDocument();
  expect(screen.getByText("Occurrences").nextSibling).toHaveTextContent(String(response.evidence.top_alarm!.alarm_count));
  expect(screen.getByRole("figure", { name: /Trend increasing/ })).toBeInTheDocument();
});

test("holds the detail that the short answer leaves out", () => {
  render(<EvidencePanel turn={turn} selectedKey={null} onCite={vi.fn()} />);
  for (const heading of ["Likely causes", "Applicable procedure", "Measured data"]) {
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  }
  // gaps appear only when there are some
  const gaps = screen.queryByRole("heading", { name: "Gaps and assumptions" });
  expect(Boolean(gaps)).toBe(response.answer.gaps.length > 0);
});

test("lists every API recommendation with its verdict", () => {
  render(<EvidencePanel turn={turn} selectedKey={null} onCite={vi.fn()} />);
  const section = screen.getByRole("heading", { name: /Alarm API advice/ }).closest("section")!;
  const items = within(section).getAllByRole("listitem");
  expect(items).toHaveLength(response.answer.action_assessments.length);
  expect(within(section).getByText("Conflict").closest("li")).toHaveTextContent("Continue operation");
});

test("a count answer's steps move into the panel, since the answer leaves them out", () => {
  const counted = structuredClone(response);
  counted.answer.answer_type = "count";
  counted.answer.action_assessments = []; // no conflict, so the answer leaves the steps out
  render(<EvidencePanel turn={{ ...turn, response: counted }} selectedKey={null} onCite={vi.fn()} />);
  const steps = screen.getByRole("heading", { name: "What to do" }).closest("section")!;
  expect(within(steps).getAllByRole("listitem")).toHaveLength(counted.answer.recommended_actions.length);
});

test("a what-to-do answer does not repeat its steps in the panel", () => {
  const steps = structuredClone(response);
  steps.answer.answer_type = "what_to_do";
  steps.answer.action_assessments = []; // no conflict, so only the type puts the steps on the answer
  render(<EvidencePanel turn={{ ...turn, response: steps }} selectedKey={null} onCite={vi.fn()} />);
  expect(screen.queryByRole("heading", { name: "What to do" })).not.toBeInTheDocument();
});
