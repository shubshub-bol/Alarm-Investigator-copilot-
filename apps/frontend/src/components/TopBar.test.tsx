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

test("says when chats are not being saved", () => {
  const broken: Health = { ...ok, status: "degraded", storage: { ok: false, error: "disk full" } };
  render(<TopBar health={{ kind: "ok", health: broken }} onMenu={vi.fn()} />);
  expect(screen.getByText("Chats not saved")).toBeInTheDocument();
});
