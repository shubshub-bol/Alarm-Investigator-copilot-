import { useCallback, useEffect, useState } from "react";

const CHAT_PATH = /^\/c\/([^/]+)\/?$/;

/**
 * The open chat lives in the address: "/" is a new chat, "/c/{id}" a saved one, so a reload reopens it and
 * Back/Forward move between chats. Uses the History API directly; the app needs no router library.
 */
export function useRoute() {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((conversationId: string | null, mode: "push" | "replace" = "push") => {
    const next = conversationId ? `/c/${conversationId}` : "/";
    if (next === window.location.pathname) return;
    if (mode === "push") window.history.pushState(null, "", next);
    else window.history.replaceState(null, "", next);
    setPath(next);
  }, []);

  return { conversationId: CHAT_PATH.exec(path)?.[1] ?? null, go };
}
