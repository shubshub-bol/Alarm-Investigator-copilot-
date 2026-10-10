import type { HealthState } from "../state/useHealth";

interface Item {
  label: string;
  value: string;
  abnormal: boolean;
}

function items(state: HealthState): Item[] {
  if (state.kind === "loading") return [{ label: "Copilot service", value: "checking", abnormal: false }];
  if (state.kind === "unreachable") return [{ label: "Copilot service", value: "unreachable", abnormal: true }];
  const { mcp, rag, llm } = state.health;
  return [
    {
      label: "Alarm tools",
      value: mcp.connected ? `${mcp.tools} available` : "unavailable",
      abnormal: !mcp.connected,
    },
    {
      label: "Documents",
      value: rag.index_loaded ? `${rag.chunks} passages` : "not indexed",
      abnormal: !rag.index_loaded,
    },
    {
      label: "Language model",
      value: llm.provider === "offline" ? "off (template answers)" : llm.provider.replace(/^gemini:/, ""),
      abnormal: false,
    },
  ];
}

/** Status follows the control-room rule: normal stays grey, only problems get colour. */
export function TopBar({ health, onNew, canReset }: { health: HealthState; onNew: () => void; canReset: boolean }) {
  return (
    <header className="topbar">
      <h1>Alarm Investigation Copilot</h1>
      <ul className="status-list" aria-label="System status">
        {items(health).map((item) => (
          <li key={item.label} className="status-item" data-abnormal={item.abnormal}>
            <span className="status-mark" aria-hidden="true" />
            <span className="status-label">{item.label}</span>
            <span className="status-value">{item.value}</span>
          </li>
        ))}
      </ul>
      <button type="button" className="button" onClick={onNew} disabled={!canReset}>
        New investigation
      </button>
    </header>
  );
}
