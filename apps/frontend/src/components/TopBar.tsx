import type { HealthState } from "../state/useHealth";

interface Status {
  text: string;
  detail: string;
  abnormal: boolean;
}

function status(state: HealthState): Status {
  if (state.kind === "loading") return { text: "Connecting", detail: "Checking the copilot service", abnormal: false };
  if (state.kind === "unreachable") {
    return { text: "Copilot service unreachable", detail: "Start the backend and this page reconnects", abnormal: true };
  }
  const { mcp, rag, llm } = state.health;
  const model = llm.provider === "offline" ? "template answers (no language model)" : llm.provider.replace(/^gemini:/, "");
  const detail = `${mcp.tools} alarm tools, ${rag.chunks} document passages, ${model}`;
  const problems = [
    !mcp.connected && "alarm data unavailable",
    !rag.index_loaded && "documents not indexed",
  ].filter(Boolean) as string[];
  if (problems.length) {
    const text = problems.join(", ");
    return { text: text[0].toUpperCase() + text.slice(1), detail, abnormal: true };
  }
  return { text: "Ready", detail, abnormal: false };
}

/** One status: grey when everything is normal, amber only when something needs attention. */
export function TopBar({ health, onNew, canReset }: { health: HealthState; onNew: () => void; canReset: boolean }) {
  const s = status(health);
  return (
    <header className="topbar">
      <h1>Alarm Investigation Copilot</h1>
      <p className="status" data-abnormal={s.abnormal} title={s.detail}>
        <span className="status-mark" aria-hidden="true" />
        {s.text}
        <span className="visually-hidden">: {s.detail}</span>
      </p>
      <button type="button" className="button" onClick={onNew} disabled={!canReset}>
        New chat
      </button>
    </header>
  );
}
