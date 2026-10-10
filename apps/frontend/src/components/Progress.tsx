import { useEffect, useState } from "react";
import { plural } from "../lib/format";
import type { Turn } from "../state/useChat";

/** One line of progress while the copilot works; the full step list is in the drawer. */
export function Progress({ turn, onShowSteps }: { turn: Turn; onShowSteps: () => void }) {
  const elapsed = useElapsed(turn.startedAt);
  return (
    <div className="progress" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{activity(turn)}</span>
      <span className="muted">{Math.floor(elapsed / 1000)} s</span>
      {turn.steps.length > 0 && (
        <button type="button" className="link" onClick={onShowSteps}>
          Show steps
        </button>
      )}
    </div>
  );
}

function activity({ understanding, discovery, steps }: Turn): string {
  if (!understanding) return "Reading your question";
  if (understanding.intent === "conversation") return "Replying";
  const tools = steps.filter((s) => s.kind === "mcp_tool").length;
  const searches = steps.length - tools;
  if (discovery && !discovery.connected) return "Alarm data unavailable; reading the documents";
  if (searches > 0) return `Reading documents (${plural(searches, "search", "searches")}) and writing the answer`;
  if (tools > 0) return `Checking alarm data: ${plural(tools, "tool call")} so far`;
  return "Connecting to the alarm system";
}

function useElapsed(since: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  return now - since;
}
