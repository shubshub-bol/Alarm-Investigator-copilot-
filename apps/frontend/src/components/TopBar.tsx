import type { HealthState } from "../state/useHealth";
import { MenuIcon } from "./icons";

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
  const { mcp, rag, llm, storage } = state.health;
  const model = llm.provider === "offline" ? "template answers (no language model)" : llm.provider.replace(/^gemini:/, "");
  const detail = `${mcp.tools} alarm tools, ${rag.chunks} document passages, ${model}`;
  const problems = [
    !mcp.connected && "alarm data unavailable",
    !rag.index_loaded && "documents not indexed",
    storage?.ok === false && "chats not saved",
  ].filter(Boolean) as string[];
  if (problems.length) {
    const text = problems.join(", ");
    return { text: text[0].toUpperCase() + text.slice(1), detail, abnormal: true };
  }
  return { text: "Ready", detail, abnormal: false };
}

/** The app name, like ChatGPT's model name, and one status: grey when normal, amber when something needs attention. */
export function TopBar({ health, onMenu }: { health: HealthState; onMenu: () => void }) {
  const s = status(health);
  return (
    <header className="topbar">
      <button type="button" className="icon-button topbar-menu" aria-label="Open chats" onClick={onMenu}>
        <MenuIcon />
      </button>
      <h1>Alarm Investigation Copilot</h1>
      <p className="status" data-abnormal={s.abnormal} title={s.detail}>
        <span className="status-mark" aria-hidden="true" />
        {s.text}
        <span className="visually-hidden">: {s.detail}</span>
      </p>
    </header>
  );
}
