import { useCallback, useEffect, useRef, useState } from "react";
import { getExamples, getTools } from "./api/client";
import type { ToolsResponse } from "./api/types";
import { Conversation } from "./components/Conversation";
import { EvidenceRail, type RailTab } from "./components/EvidenceRail";
import { TopBar } from "./components/TopBar";
import { useChat } from "./state/useChat";
import { useHealth } from "./state/useHealth";

export const FALLBACK_EXAMPLES = [
  "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence.",
  "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.",
  "Which alarm has the highest priority in EastRefinery, and why?",
];

export function App() {
  const { turns, running, ask, retry, reset } = useChat();
  const health = useHealth();
  const [examples, setExamples] = useState<string[]>(FALLBACK_EXAMPLES);
  const [tools, setTools] = useState<ToolsResponse | null>(null);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [tab, setTab] = useState<RailTab>("evidence");
  const [selection, setSelection] = useState<{ turnId: string; key: string } | null>(null);
  const [pinnedTurnId, setPinnedTurnId] = useState<string | null>(null);
  const autoTab = useRef(true); // false once the user picks a tab themselves

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

  const latest = turns[turns.length - 1];
  const railTurn = turns.find((t) => t.id === pinnedTurnId) ?? latest;

  // A new question: follow it in the rail and watch its steps arrive.
  useEffect(() => {
    if (latest?.phase !== "running") return;
    setPinnedTurnId(null);
    setSelection(null);
    autoTab.current = true;
    setTab("trace");
  }, [latest?.id, latest?.phase]);

  // Finished: switch to the evidence summary, unless the user chose a tab meanwhile.
  useEffect(() => {
    if (latest?.phase === "done" && autoTab.current) setTab("evidence");
  }, [latest?.phase]);

  const chooseTab = (next: RailTab) => {
    autoTab.current = false;
    setTab(next);
  };

  const showEvidence = (turnId: string, key: string | null, next: RailTab) => {
    autoTab.current = false;
    setPinnedTurnId(turnId);
    setSelection(key ? { turnId, key } : null);
    setTab(next);
    if (window.matchMedia?.("(max-width: 900px)").matches) {
      document.querySelector(".rail")?.scrollIntoView({ block: "start" });
    }
  };

  return (
    <div className="app">
      <TopBar
        health={health}
        canReset={turns.length > 0}
        onNew={() => {
          reset();
          setSelection(null);
          setPinnedTurnId(null);
          setTab("evidence");
        }}
      />
      <div className="workspace">
        <Conversation
          turns={turns}
          running={running}
          examples={examples}
          selection={selection}
          onAsk={(q) => void ask(q)}
          onRetry={retry}
          onCite={(turnId, key) => showEvidence(turnId, key, key.startsWith("T") ? "trace" : "sources")}
          onShowTrace={(turnId) => showEvidence(turnId, null, "trace")}
        />
        <EvidenceRail
          turn={railTurn}
          tab={tab}
          onTab={chooseTab}
          selectedKey={selection && selection.turnId === railTurn?.id ? selection.key : null}
          onCite={(key) => railTurn && showEvidence(railTurn.id, key, key.startsWith("T") ? "trace" : "sources")}
          tools={tools}
          toolsError={toolsError}
          onReloadTools={loadTools}
        />
      </div>
    </div>
  );
}
