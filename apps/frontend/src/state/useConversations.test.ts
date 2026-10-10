import { act, renderHook, waitFor } from "@testing-library/react";
import type { ConversationSummary } from "../api/types";
import { useConversations } from "./useConversations";

const A: ConversationSummary = {
  conversation_id: "conv-aaaaaaaaaaaa",
  title: "Boiler Feed Pump 101: High Vibration",
  created_at: "2026-10-10T09:00:00Z",
  updated_at: "2026-10-10T09:30:00Z",
};
const B: ConversationSummary = { ...A, conversation_id: "conv-bbbbbbbbbbbb", title: "New chat" };

afterEach(() => vi.unstubAllGlobals());

test("loads the list once the backend is reachable", async () => {
  const fetchMock = vi.fn(async () => Response.json({ conversations: [A, B] }));
  vi.stubGlobal("fetch", fetchMock);
  const { result, rerender } = renderHook(({ up }) => useConversations(up), { initialProps: { up: false } });
  expect(fetchMock).not.toHaveBeenCalled();
  rerender({ up: true });
  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.items.map((c) => c.title)).toEqual([A.title, "New chat"]);
});

test("rename and remove update the list", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") return Response.json({ ...B, title: "Shift handover" });
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return Response.json({ conversations: [A, B] });
    }),
  );
  const { result } = renderHook(() => useConversations(true));
  await waitFor(() => expect(result.current.status).toBe("ready"));
  await act(() => result.current.rename(B.conversation_id, "Shift handover"));
  expect(result.current.items[1].title).toBe("Shift handover");
  await act(() => result.current.remove(A.conversation_id));
  expect(result.current.items.map((c) => c.conversation_id)).toEqual([B.conversation_id]);
});

test("a failed load is reported, and a failed rename throws", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("down", { status: 503 })));
  const { result } = renderHook(() => useConversations(true));
  await waitFor(() => expect(result.current.status).toBe("error"));
  expect(result.current.error).toContain("503");
  await expect(result.current.rename(A.conversation_id, "x")).rejects.toThrow("503");
});
