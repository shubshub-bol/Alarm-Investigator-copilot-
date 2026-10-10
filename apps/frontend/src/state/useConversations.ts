import { useCallback, useEffect, useState } from "react";
import { deleteConversation, listConversations, renameConversation } from "../api/client";
import type { ConversationSummary } from "../api/types";

export type ListStatus = "loading" | "ready" | "error";

/** The saved chats in the sidebar. Loads when the backend becomes reachable; rename and remove throw on failure. */
export function useConversations(enabled: boolean) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [status, setStatus] = useState<ListStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { conversations } = await listConversations();
      setItems(conversations);
      setStatus("ready");
      setError(null);
    } catch (e) {
      setStatus("error");
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (enabled) void refresh();
  }, [enabled, refresh]);

  const rename = useCallback(async (id: string, title: string) => {
    const updated = await renameConversation(id, title);
    setItems((all) => all.map((c) => (c.conversation_id === id ? updated : c)));
  }, []);

  const remove = useCallback(async (id: string) => {
    await deleteConversation(id);
    setItems((all) => all.filter((c) => c.conversation_id !== id));
  }, []);

  return { items, status, error, refresh, rename, remove };
}
