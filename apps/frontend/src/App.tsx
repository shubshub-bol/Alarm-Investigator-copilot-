import { useCallback, useEffect, useState } from "react";
import { getExamples, getTools } from "./api/client";
import type { ToolsResponse } from "./api/types";
import { Conversation } from "./components/Conversation";
import { type DrawerTab, EvidenceDrawer } from "./components/EvidenceDrawer";
import { TopBar } from "./components/TopBar";
import { useChat } from "./state/useChat";
import { useHealth } from "./state/useHealth";

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

export function App() {
  const { turns, running, ask, retry, reset } = useChat();
  const health = useHealth();
  const [examples, setExamples] = useState<string[]>(FALLBACK_EXAMPLES);
  const [tools, setTools] = useState<ToolsResponse | null>(null);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<DrawerState>(CLOSED);

  const backendUp = health.kind === "ok";
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

  const open = (turnId: string, tab: DrawerTab, key: string | null = null) =>
    setDrawer({ open: true, tab, turnId, key });
  const close = useCallback(() => setDrawer((d) => ({ ...d, open: false, key: null })), []);
  const drawerTurn = turns.find((t) => t.id === drawer.turnId) ?? turns[turns.length - 1];

  return (
    <div className="app">
      <TopBar
        health={health}
        canReset={turns.length > 0}
        onNew={() => {
          reset();
          setDrawer(CLOSED);
        }}
      />
      <Conversation
        turns={turns}
        running={running}
        examples={examples}
        selection={drawer.open && drawer.turnId && drawer.key ? { turnId: drawer.turnId, key: drawer.key } : null}
        onAsk={(q) => void ask(q)}
        onRetry={retry}
        onCite={(turnId, key) => open(turnId, key.startsWith("T") ? "steps" : "sources", key)}
        onShowEvidence={(turnId) => open(turnId, "evidence")}
        onShowSteps={(turnId) => open(turnId, "steps")}
      />
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
