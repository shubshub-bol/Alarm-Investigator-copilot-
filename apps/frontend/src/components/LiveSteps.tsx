import { useEffect, useState } from "react";
import type { TraceStep } from "../api/types";
import { INTENT_LABEL, seconds } from "../lib/format";
import type { Turn } from "../state/useChat";

/** The investigation as it happens: each real step appears when the backend finishes it. */
export function LiveSteps({ turn }: { turn: Turn }) {
  const elapsed = useElapsed(turn.startedAt);
  const { understanding, discovery, steps } = turn;
  return (
    <section className="live" aria-label="Investigation in progress" aria-busy="true">
      <ol>
        {understanding ? (
          <li className="live-row" data-state="ok">
            <Mark state="ok" />
            <span className="live-text">
              Understood: {INTENT_LABEL[understanding.intent]}
              {understanding.asset_query && <code className="tag">{understanding.asset_query}</code>}
            </span>
            <span className="live-ms">{understanding.method === "llm" ? "Gemini" : "rules"}</span>
          </li>
        ) : (
          <Pending text="Understanding the question" elapsed={elapsed} />
        )}
        {discovery && (
          <li className="live-row" data-state={discovery.connected ? "ok" : "error"}>
            <Mark state={discovery.connected ? "ok" : "error"} />
            <span className="live-text">
              {discovery.connected
                ? `Found ${discovery.tool_count} tools on ${discovery.server}`
                : `Alarm tools unavailable: continuing with documents only`}
            </span>
            <span className="live-ms">{seconds(discovery.duration_ms)}</span>
          </li>
        )}
        {steps.map((step) => (
          <StepRow key={step.step} step={step} />
        ))}
        {understanding && (
          <Pending
            text={understanding.intent === "conversation" ? "Replying" : steps.length ? "Working" : "Querying the alarm system"}
            elapsed={elapsed}
          />
        )}
      </ol>
      <p className="visually-hidden" aria-live="polite">
        {steps.length ? `Step ${steps.length} done: ${describe(steps[steps.length - 1])}` : ""}
      </p>
    </section>
  );
}

function describe(step: TraceStep): string {
  return step.kind === "mcp_tool" ? step.tool : "document search";
}

function StepRow({ step }: { step: TraceStep }) {
  const state = step.status === "ok" ? "ok" : "error";
  return (
    <li className="live-row" data-state={state}>
      <Mark state={state} />
      <span className="live-text">
        <span className="step-no">{step.step}</span>
        {step.kind === "mcp_tool" ? (
          <code className="tool-name">{step.tool}</code>
        ) : (
          <span className="doc-search">Document search</span>
        )}
        <span className="live-purpose">{step.kind === "mcp_tool" ? step.purpose : `“${step.query}”`}</span>
      </span>
      <span className="live-ms">{seconds(step.duration_ms)}</span>
    </li>
  );
}

function Pending({ text, elapsed }: { text: string; elapsed: number }) {
  return (
    <li className="live-row" data-state="pending">
      <Mark state="pending" />
      <span className="live-text">{text}</span>
      <span className="live-ms">{Math.floor(elapsed / 1000)} s</span>
    </li>
  );
}

function Mark({ state }: { state: "ok" | "error" | "pending" }) {
  if (state === "pending") return <span className="mark mark-pending" aria-hidden="true" />;
  return (
    <svg className={`mark mark-${state}`} viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
      {state === "ok" ? (
        <path d="M2 6.5 5 9.2 10.2 2.8" fill="none" stroke="currentColor" strokeWidth="1.8" />
      ) : (
        <path d="M2.5 2.5 9.5 9.5M9.5 2.5 2.5 9.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
      )}
    </svg>
  );
}

function useElapsed(since: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  return now - since;
}
