import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ConversationSummary } from "../api/types";
import { type ChatsView, Sidebar } from "./Sidebar";

const items: ConversationSummary[] = [
  {
    conversation_id: "conv-aaaaaaaaaaaa",
    title: "BFP-102 Critical Alarms",
    created_at: "2026-10-10T09:00:00Z",
    updated_at: "2026-10-10T09:30:00Z",
  },
  {
    conversation_id: "conv-bbbbbbbbbbbb",
    title: "P1 Priority Score Meaning",
    created_at: "2026-10-10T08:00:00Z",
    updated_at: "2026-10-10T08:00:00Z",
  },
];

const view = (overrides: Partial<ChatsView> = {}): ChatsView => ({
  items,
  status: "ready",
  error: null,
  refresh: vi.fn(async () => undefined),
  rename: vi.fn(async () => undefined),
  ...overrides,
});

function renderSidebar(overrides: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const props = {
    chats: view(),
    activeId: null,
    collapsed: false,
    open: false,
    onCollapse: vi.fn(),
    onClose: vi.fn(),
    onNew: vi.fn(),
    onOpen: vi.fn(),
    onDelete: vi.fn(async () => undefined),
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

test("search filters chats by title, ignoring case and spaces", async () => {
  renderSidebar();
  await userEvent.type(screen.getByRole("searchbox", { name: "Search chats" }), "  PRIORITY ");
  expect(screen.getByRole("button", { name: "P1 Priority Score Meaning" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "BFP-102 Critical Alarms" })).not.toBeInTheDocument();
});

test("says when nothing matches", async () => {
  renderSidebar();
  await userEvent.type(screen.getByRole("searchbox", { name: "Search chats" }), "compressor");
  expect(screen.getByText("No chats match.")).toBeInTheDocument();
});

test("a failed list load offers Retry", async () => {
  const refresh = vi.fn(async () => undefined);
  renderSidebar({ chats: view({ items: [], status: "error", error: "HTTP 503", refresh }) });
  await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Retry" }));
  expect(refresh).toHaveBeenCalled();
});

test("New chat starts a new chat", async () => {
  const { onNew } = renderSidebar();
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(onNew).toHaveBeenCalled();
});

test("collapsed, it is a strip of icon buttons", async () => {
  const { onCollapse, onNew } = renderSidebar({ collapsed: true });
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(onNew).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Open sidebar" }));
  expect(onCollapse).toHaveBeenCalledWith(false);
});
