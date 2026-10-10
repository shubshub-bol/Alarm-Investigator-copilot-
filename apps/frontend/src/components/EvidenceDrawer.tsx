import { useEffect, useRef } from "react";
import type { ToolsResponse } from "../api/types";
import type { Turn } from "../state/useChat";
import { EvidencePanel } from "./panels/EvidencePanel";
import { SourcesPanel } from "./panels/SourcesPanel";
import { ToolsPanel } from "./panels/ToolsPanel";
import { TracePanel } from "./panels/TracePanel";

export type DrawerTab = "evidence" | "steps" | "sources" | "tools";

interface Props {
  open: boolean;
  onClose: () => void;
  turn: Turn | undefined;
  tab: DrawerTab;
  onTab: (tab: DrawerTab) => void;
  selectedKey: string | null;
  onCite: (key: string) => void;
  tools: ToolsResponse | null;
  toolsError: string | null;
  onReloadTools: () => void;
}

/** Everything behind an answer, kept out of the way until asked for. */
export function EvidenceDrawer(props: Props) {
  const { open, onClose, turn, tab, onTab, selectedKey, onCite, tools, toolsError, onReloadTools } = props;
  const panel = useRef<HTMLDivElement>(null);
  const response = turn?.response;
  const highlightStep =
    selectedKey && response ? (response.answer.facts.find((f) => f.key === selectedKey)?.step ?? null) : null;
  const highlightSource = selectedKey?.startsWith("S") ? selectedKey : null;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Bring the item a chip points at into view; otherwise start at the top of the tab.
  useEffect(() => {
    if (!open) return;
    const anchor = highlightStep != null ? `step-${highlightStep}` : highlightSource ? `source-${highlightSource}` : null;
    const target = anchor && turn ? document.getElementById(`${turn.id}-${anchor}`) : null;
    if (target) {
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      target.focus({ preventScroll: true });
    } else {
      panel.current?.scrollTo?.({ top: 0 });
      panel.current?.focus({ preventScroll: true });
    }
  }, [open, tab, highlightStep, highlightSource, turn?.id]);

  if (!open) return null;

  const tabs: { id: DrawerTab; label: string; count?: number }[] = [
    { id: "evidence", label: "Evidence" },
    { id: "steps", label: "Steps", count: turn?.steps.length },
    { id: "sources", label: "Sources", count: response?.answer.citations.length },
    { id: "tools", label: "Tools", count: tools?.tools.length },
  ];

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className="drawer-head">
          <h2 id="drawer-title">{turn ? "Evidence for this answer" : "Evidence"}</h2>
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="tabs" role="tablist" aria-label="Evidence views">
          {tabs.map((t) => (
            <button
              key={t.id}
              id={`tab-${t.id}`}
              type="button"
              role="tab"
              className="tab"
              aria-selected={tab === t.id}
              aria-controls="drawer-panel"
              onClick={() => onTab(t.id)}
            >
              {t.label}
              {t.count != null && <span className="tab-count">{t.count}</span>}
            </button>
          ))}
        </div>
        <div
          className="panel"
          id="drawer-panel"
          role="tabpanel"
          aria-labelledby={`tab-${tab}`}
          tabIndex={-1}
          ref={panel}
        >
          {tab === "tools" ? (
            <ToolsPanel tools={tools} error={toolsError} onReload={onReloadTools} />
          ) : !turn ? (
            <p className="muted">Ask a question first; its evidence appears here.</p>
          ) : tab === "evidence" ? (
            <EvidencePanel turn={turn} selectedKey={selectedKey} onCite={onCite} />
          ) : tab === "steps" ? (
            <TracePanel turn={turn} highlightStep={highlightStep} />
          ) : (
            <SourcesPanel turn={turn} highlightKey={highlightSource} onCite={onCite} />
          )}
        </div>
      </aside>
    </>
  );
}
