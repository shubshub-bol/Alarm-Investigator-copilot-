import type { RetrievalRecord, ToolCallRecord, TraceStep } from "../../api/types";
import { seconds } from "../../lib/format";
import type { Turn } from "../../state/useChat";
import { JsonBlock } from "../JsonBlock";

/** MCP execution trace: every tool call and document search, with raw request and response. */
export function TracePanel({ turn, highlightStep }: { turn: Turn; highlightStep: number | null }) {
  const { discovery, steps } = turn;
  return (
    <div className="trace">
      {turn.traceId && (
        <p className="muted trace-id">
          Trace <code className="tag">{turn.traceId}</code> is passed to the MCP server and the Alarm API with every
          call.
        </p>
      )}
      {discovery && (
        <div className="trace-discovery" data-state={discovery.connected ? "ok" : "error"}>
          {discovery.connected ? (
            <>
              Discovered {discovery.tool_count} tools on <code className="tag">{discovery.server}</code> in{" "}
              {seconds(discovery.duration_ms)}
            </>
          ) : (
            <>MCP server unavailable: {discovery.error}</>
          )}
        </div>
      )}
      <ol className="trace-steps">
        {steps.map((step) => (
          <li key={step.step} id={`${turn.id}-step-${step.step}`} tabIndex={-1}>
            <StepDetails step={step} highlighted={highlightStep === step.step} />
          </li>
        ))}
      </ol>
      {turn.phase === "running" && <p className="muted">More steps arrive as the copilot works.</p>}
    </div>
  );
}

function StepDetails({ step, highlighted }: { step: TraceStep; highlighted: boolean }) {
  const failed = step.status !== "ok";
  return (
    <details className="trace-step" data-failed={failed} data-target={highlighted} open={highlighted || undefined}>
      <summary>
        <span className="step-no">{step.step}</span>
        <span className="trace-name">
          {step.kind === "mcp_tool" ? <code className="tool-name">{step.tool}</code> : "Document search"}
          <span className="muted">{step.purpose}</span>
        </span>
        <span className="trace-side">
          {failed && <span className="error-code">{step.kind === "mcp_tool" ? step.error_code : "error"}</span>}
          {step.kind === "mcp_tool" && step.retries > 0 && (
            <span className="retries">
              {step.retries} {step.retries === 1 ? "retry" : "retries"}
            </span>
          )}
          <span className="muted">{seconds(step.duration_ms)}</span>
        </span>
      </summary>
      <div className="trace-body">
        {step.kind === "mcp_tool" ? <ToolBody step={step} /> : <SearchBody step={step} />}
      </div>
    </details>
  );
}

function ToolBody({ step }: { step: ToolCallRecord }) {
  return (
    <>
      {step.error_message && <p className="error-text">{step.error_message}</p>}
      <h4>Request to {step.server}</h4>
      <JsonBlock value={step.arguments} label={`Arguments sent to ${step.tool}`} />
      {step.api_calls.length > 0 && (
        <>
          <h4>Alarm API calls made by the MCP server</h4>
          <table className="api-calls">
            <thead>
              <tr>
                <th scope="col">Call</th>
                <th scope="col">Status</th>
                <th scope="col">Attempts</th>
                <th scope="col">Time</th>
              </tr>
            </thead>
            <tbody>
              {step.api_calls.map((call, i) => (
                <tr key={i} data-outcome={call.outcome}>
                  <td>
                    <code className="tag">
                      {call.method} {call.path}
                    </code>
                  </td>
                  <td>{call.status_code ?? call.error_code}</td>
                  <td>{call.attempts}</td>
                  <td>{call.duration_ms == null ? "n/a" : seconds(call.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {step.result && (
        <details className="raw">
          <summary>Response from {step.tool}</summary>
          <JsonBlock value={step.result} label={`Response from ${step.tool}`} />
        </details>
      )}
    </>
  );
}

function SearchBody({ step }: { step: RetrievalRecord }) {
  return (
    <>
      {step.error_message && <p className="error-text">{step.error_message}</p>}
      <p>
        <span className="muted">Query</span> “{step.query}”
      </p>
      {Object.keys(step.filters).length > 0 && (
        <>
          <h4>Filters</h4>
          <JsonBlock value={step.filters} label="Search filters" />
        </>
      )}
      <p>
        <span className="muted">Confidence</span> {step.confidence ?? "n/a"}
        {step.top_dense_score != null && <span className="muted"> (best match {step.top_dense_score.toFixed(2)})</span>}
      </p>
      {step.results.length > 0 && (
        <table className="api-calls">
          <thead>
            <tr>
              <th scope="col">Passage</th>
              <th scope="col">Meaning</th>
              <th scope="col">Keywords</th>
            </tr>
          </thead>
          <tbody>
            {step.results.map((r) => (
              <tr key={r.chunk_id}>
                <td>
                  {r.key ? <strong>{r.key}</strong> : <span className="muted">not used</span>} {r.label}
                </td>
                <td>{r.dense_score.toFixed(2)}</td>
                <td>{r.keyword_score.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {step.quarantined_withheld > 0 && (
        <p className="error-text">
          {step.quarantined_withheld} passage(s) withheld: they contain text that tries to instruct the assistant.
        </p>
      )}
    </>
  );
}
