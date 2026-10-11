import { render, screen } from "@testing-library/react";
import type { Health } from "../api/types";
import health from "../test/fixtures/health.json";
import { TopBar } from "./TopBar";

const ok = health as Health;

test("shows the app name and Ready when everything works", () => {
  render(<TopBar health={{ kind: "ok", health: ok }} onMenu={vi.fn()} />);
  expect(screen.getByRole("heading", { level: 1, name: "Alarm Investigation Copilot" })).toBeInTheDocument();
  expect(screen.getByText("Ready")).toBeInTheDocument();
});

test("a backend that does not report storage yet still shows Ready", () => {
  const { storage: _storage, ...older } = ok; // an older backend's health has no storage field
  render(<TopBar health={{ kind: "ok", health: older }} onMenu={vi.fn()} />);
  expect(screen.getByText("Ready")).toBeInTheDocument();
});

test("names the model without its provider prefix", () => {
  const viaOpenRouter: Health = { ...ok, llm: { provider: "openrouter:nvidia/nemotron-3-super-120b-a12b:free" } };
  render(<TopBar health={{ kind: "ok", health: viaOpenRouter }} onMenu={vi.fn()} />);
  expect(screen.getByText("Ready").closest("p")).toHaveAttribute(
    "title",
    "13 alarm tools, 54 document passages, nvidia/nemotron-3-super-120b-a12b:free",
  );
});

test("says when chats are not being saved", () => {
  const broken: Health = { ...ok, status: "degraded", storage: { ok: false, error: "disk full" } };
  render(<TopBar health={{ kind: "ok", health: broken }} onMenu={vi.fn()} />);
  expect(screen.getByText("Chats not saved")).toBeInTheDocument();
});
