import { useCallback, useEffect, useState } from "react";
import { getExamples, getTools } from "./api/client";
import type { ToolsResponse } from "./api/types";
import { Conversation } from "./components/Conversation";
import { type DrawerTab, EvidenceDrawer } from "./components/EvidenceDrawer";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { useChat } from "./state/useChat";
import { useConversations } from "./state/useConversations";
import { useHealth } from "./state/useHealth";
import { useRoute } from "./state/useRoute";

export const FALLBACK_EXAMPLES = [
  "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence.",
  "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.",
  "Which alarm has the highest priority in EastRefinery, and why?",
];

interface DrawerState {
  open: boolean;
  tab: DrawerTab;
  turnId: string | null;
  key: string | null; // citation the user clicked, highlighted in the drawer
}

const CLOSED: DrawerState = { open: false, tab: "evidence", turnId: null, key: null };
const COLLAPSED_KEY = "copilot.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "true";
  } catch {
    return false; // storage blocked (private window): start expanded
  }
}

export function App() {
  const route = useRoute();
  const health = useHealth();
  const backendUp = health.kind === "ok";
  const chats = useConversations(backendUp);
  const chat = useChat((response) => {
    route.go(response.conversation_id, "replace"); // a new chat gets its address once it is saved
    void chats.refresh(); // shows a new chat and its title
  });
  const [examples, setExamples] = useState<string[]>(FALLBACK_EXAMPLES);
  const [tools, setTools] = useState<ToolsResponse | null>(null);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<DrawerState>(CLOSED);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const loadTools = useCallback(() => {
    setToolsError(null);
    getTools()
      .then(setTools)
      .catch((e: Error) => setToolsError(e.message));
  }, []);

  // (Re)load reference data whenever the backend becomes reachable, e.g. after it finishes starting.
  useEffect(() => {
    if (!backendUp) return;
    loadTools();
    getExamples()
      .then((r) => setExamples(r.examples))
      .catch(() => undefined); // keep the built-in examples
  }, [backendUp, loadTools]);

  // The address decides which chat is open, also after Back and Forward. When a new chat has just been given
  // its address, the ids already match and nothing reloads.
  useEffect(() => {
    if (route.conversationId === chat.conversationId) return;
    setDrawer(CLOSED);
    setSidebarOpen(false);
    if (route.conversationId) void chat.load(route.conversationId);
    else chat.reset();
  }, [route.conversationId]); // only a change of address opens another chat

  // A chat that failed to open (backend still starting, storage busy) is retried at each successful health check,
  // so it opens by itself; Try again does the same at once.
  useEffect(() => {
    if (backendUp && chat.status === "error" && route.conversationId) void chat.load(route.conversationId);
  }, [health]); // every check is a new object: at most one retry per check, never a loop

  const newChat = (mode: "push" | "replace" = "push") => {
    setDrawer(CLOSED);
    setSidebarOpen(false);
    chat.reset();
    route.go(null, mode);
  };

  const openChat = (id: string) => {
    setSidebarOpen(false);
    route.go(id);
  };

  const deleteChat = async (id: string) => {
    await chats.remove(id);
    if (id === chat.conversationId) newChat("replace");
  };

  const collapse = (next: boolean) => {
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSED_KEY, String(next));
    } catch {
      // not remembered; the sidebar still works
    }
  };

  const open = (turnId: string, tab: DrawerTab, key: string | null = null) =>
    setDrawer({ open: true, tab, turnId, key });
  const close = useCallback(() => setDrawer((d) => ({ ...d, open: false, key: null })), []);
  const drawerTurn = chat.turns.find((t) => t.id === drawer.turnId) ?? chat.turns[chat.turns.length - 1];

  return (
    <div className="app">
      <Sidebar
        chats={chats}
        activeId={chat.conversationId}
        collapsed={collapsed}
        open={sidebarOpen}
        onCollapse={collapse}
        onClose={() => setSidebarOpen(false)}
        onNew={() => newChat()}
        onOpen={openChat}
        onDelete={deleteChat}
      />
      <div className="main">
        <TopBar health={health} onMenu={() => setSidebarOpen(true)} />
        <Conversation
          turns={chat.turns}
          status={chat.status}
          loadError={chat.loadError}
          running={chat.running}
          examples={examples}
          selection={drawer.open && drawer.turnId && drawer.key ? { turnId: drawer.turnId, key: drawer.key } : null}
          onAsk={(q) => void chat.ask(q)}
          onRetry={chat.retry}
          onCite={(turnId, key) => open(turnId, key.startsWith("T") ? "steps" : "sources", key)}
          onOpenEvidence={(turnId, tab) => open(turnId, tab)}
          onNewChat={() => newChat()}
          onReload={() => {
            if (route.conversationId) void chat.load(route.conversationId);
          }}
        />
      </div>
      <EvidenceDrawer
        open={drawer.open}
        onClose={close}
        turn={drawerTurn}
        tab={drawer.tab}
        onTab={(tab) => setDrawer((d) => ({ ...d, tab, key: null }))}
        selectedKey={drawer.key}
        onCite={(key) => setDrawer((d) => ({ ...d, tab: key.startsWith("T") ? "steps" : "sources", key }))}
        tools={tools}
        toolsError={toolsError}
        onReloadTools={loadTools}
      />
    </div>
  );
}
