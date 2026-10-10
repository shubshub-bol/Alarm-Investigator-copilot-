import { useEffect } from "react";
import type { ToolsResponse } from "../api/types";
import type { Turn } from "../state/useChat";
import { EvidencePanel } from "./panels/EvidencePanel";
import { SourcesPanel } from "./panels/SourcesPanel";
import { ToolsPanel } from "./panels/ToolsPanel";
import { TracePanel } from "./panels/TracePanel";

export type RailTab = "evidence" | "trace" | "sources" | "tools";

interface Props {
  turn: Turn | undefined;
  tab: RailTab;
  onTab: (tab: RailTab) => void;
  selectedKey: string | null;
  onCite: (key: string) => void;
  tools: ToolsResponse | null;
  toolsError: string | null;
  onReloadTools: () => void;
}

/** Evidence behind the current answer. Citation chips in the answer select an item here. */
export function EvidenceRail({ turn, tab, onTab, selectedKey, onCite, tools, toolsError, onReloadTools }: Props) {
  const response = turn?.response;
  const highlightStep =
    selectedKey && response ? response.answer.facts.find((f) => f.key === selectedKey)?.step ?? null : null;
  const highlightSource = selectedKey?.startsWith("S") ? selectedKey : null;

  useEffect(() => {
    if (!selectedKey || !turn) return;
    const anchor = highlightStep != null ? `step-${highlightStep}` : `source-${highlightSource}`;
    const target = document.getElementById(`${turn.id}-${anchor}`);
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    target?.scrollIntoView?.({ block: "center", behavior: reduce ? "auto" : "smooth" });
    target?.focus({ preventScroll: true });
  }, [selectedKey, highlightStep, highlightSource, turn, tab]);

  const tabs: { id: RailTab; label: string; count?: number }[] = [
    { id: "evidence", label: "Evidence" },
    { id: "trace", label: "Trace", count: turn ? turn.steps.length : undefined },
    { id: "sources", label: "Sources", count: response?.answer.citations.length },
    { id: "tools", label: "Tools", count: tools?.tools.length },
  ];

  return (
    <aside className="rail" aria-label="Evidence">
      <div className="tabs" role="tablist" aria-label="Evidence views">
        {tabs.map((t) => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
            {t.count != null && <span className="tab-count">{t.count}</span>}
          </button>
        ))}
      </div>
      <div className="panel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "tools" ? (
          <ToolsPanel tools={tools} error={toolsError} onReload={onReloadTools} />
        ) : !turn ? (
          <p className="muted rail-empty">
            The evidence for an investigation appears here: the asset and its alarm history, every tool call the
            copilot made, and the document passages it cited.
          </p>
        ) : tab === "evidence" ? (
          <EvidencePanel turn={turn} />
        ) : tab === "trace" ? (
          <TracePanel turn={turn} highlightStep={highlightStep} />
        ) : (
          <SourcesPanel turn={turn} highlightKey={highlightSource} onCite={onCite} />
        )}
      </div>
    </aside>
  );
}
