import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ConversationSummary } from "../api/types";
import { ChatList } from "./ChatList";

const chats: ConversationSummary[] = [
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

function renderList(overrides: Partial<Parameters<typeof ChatList>[0]> = {}) {
  const props = {
    items: chats,
    activeId: "conv-aaaaaaaaaaaa",
    onOpen: vi.fn(),
    onRename: vi.fn(async () => undefined),
    onDelete: vi.fn(async () => undefined),
    ...overrides,
  };
  render(<ChatList {...props} />);
  return props;
}

async function startRename(title: string) {
  await userEvent.click(screen.getByRole("button", { name: `Options for ${title}` }));
  await userEvent.click(screen.getByRole("button", { name: "Rename" }));
  return screen.getByRole("textbox", { name: "Chat name" });
}

test("marks the open chat and opens another on click", async () => {
  const { onOpen } = renderList();
  expect(screen.getByRole("button", { name: "BFP-102 Critical Alarms" })).toHaveAttribute("aria-current", "page");
  await userEvent.click(screen.getByRole("button", { name: "P1 Priority Score Meaning" }));
  expect(onOpen).toHaveBeenCalledWith("conv-bbbbbbbbbbbb");
});

test("rename saves on Enter", async () => {
  const { onRename } = renderList();
  const field = await startRename("P1 Priority Score Meaning");
  await userEvent.clear(field);
  await userEvent.type(field, "Priority scores{Enter}");
  expect(onRename).toHaveBeenCalledWith("conv-bbbbbbbbbbbb", "Priority scores");
  expect(screen.queryByRole("textbox", { name: "Chat name" })).not.toBeInTheDocument();
});

test("Esc, an empty name or the same name cancel the rename", async () => {
  const { onRename } = renderList();
  await userEvent.type(await startRename("P1 Priority Score Meaning"), " changed{Escape}");
  const emptied = await startRename("P1 Priority Score Meaning");
  await userEvent.clear(emptied);
  await userEvent.type(emptied, "   {Enter}");
  await userEvent.type(await startRename("P1 Priority Score Meaning"), "{Enter}");
  expect(onRename).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "P1 Priority Score Meaning" })).toBeInTheDocument();
});

test("delete asks first and can be cancelled", async () => {
  const { onDelete } = renderList();
  const openDelete = async () => {
    await userEvent.click(screen.getByRole("button", { name: "Options for BFP-102 Critical Alarms" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    return screen.getByRole("dialog", { name: "Delete chat?" });
  };
  const dialog = await openDelete();
  expect(dialog).toHaveTextContent("This deletes “BFP-102 Critical Alarms” and its answers.");
  await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(onDelete).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  await userEvent.click(within(await openDelete()).getByRole("button", { name: "Delete" }));
  expect(onDelete).toHaveBeenCalledWith("conv-aaaaaaaaaaaa");
});

test("a failed rename says so next to the chat", async () => {
  renderList({ onRename: vi.fn(async () => Promise.reject(new Error("The copilot service answered with HTTP 500."))) });
  await userEvent.type(await startRename("P1 Priority Score Meaning"), "x{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't rename this chat.");
});

test("titles are shown as text, never as HTML", () => {
  renderList({ items: [{ ...chats[0], title: '<img src=x onerror="alert(1)">' }] });
  expect(screen.getByRole("button", { name: '<img src=x onerror="alert(1)">' })).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
});
