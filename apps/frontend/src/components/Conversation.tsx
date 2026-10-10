import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import type { ChatResponse } from "../api/types";
import { plural, seconds } from "../lib/format";
import type { Turn } from "../state/useChat";
import { Answer } from "./Answer";
import { Progress } from "./Progress";

interface Props {
  turns: Turn[];
  running: boolean;
  examples: string[];
  selection: { turnId: string; key: string } | null;
  onAsk: (question: string) => void;
  onRetry: (turnId: string) => void;
  onCite: (turnId: string, key: string) => void;
  onShowEvidence: (turnId: string) => void;
  onShowSteps: (turnId: string) => void;
}

export function Conversation(props: Props) {
  const { turns, running, examples, selection, onAsk, onRetry, onCite, onShowEvidence, onShowSteps } = props;
  const log = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastTurn = turns[turns.length - 1];

  // New question: jump to it.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  // Progress and the answer: follow along only if the reader is already at the bottom.
  useEffect(() => {
    const el = log.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) end.current?.scrollIntoView({ block: "end" });
  }, [lastTurn?.steps.length, lastTurn?.phase]);

  return (
    <main className="conversation">
      <div className="log" ref={log}>
        <div className="column">
          {turns.length === 0 ? (
            <EmptyState examples={examples} onAsk={onAsk} disabled={running} />
          ) : (
            turns.map((turn) => (
              <section key={turn.id} className="turn" aria-label={`Question: ${turn.question}`}>
                <p className="question">{turn.question}</p>
                {turn.phase === "running" && <Progress turn={turn} onShowSteps={() => onShowSteps(turn.id)} />}
                {turn.phase === "failed" && (
                  <div className="failure" role="alert">
                    <p>The investigation did not finish. {turn.error}</p>
                    <button type="button" className="button" onClick={() => onRetry(turn.id)} disabled={running}>
                      Try again
                    </button>
                  </div>
                )}
                {turn.phase === "done" && turn.response && (
                  <>
                    <RunLine response={turn.response} onShowSteps={() => onShowSteps(turn.id)} />
                    <Answer
                      response={turn.response}
                      selectedKey={selection?.turnId === turn.id ? selection.key : null}
                      onCite={(key) => onCite(turn.id, key)}
                      onAsk={onAsk}
                      onShowEvidence={() => onShowEvidence(turn.id)}
                      canAsk={!running && turn === lastTurn}
                    />
                  </>
                )}
              </section>
            ))
          )}
          <div ref={end} />
        </div>
      </div>
      <Composer onAsk={onAsk} running={running} />
    </main>
  );
}

/** What the copilot did, in one line: "Checked 8 alarm tools and 4 document searches in 12.3 s". */
function RunLine({ response, onShowSteps }: { response: ChatResponse; onShowSteps: () => void }) {
  if (response.trace.length === 0) return null;
  const tools = response.trace.filter((s) => s.kind === "mcp_tool").length;
  const searches = response.trace.length - tools;
  const failed = response.trace.filter((s) => s.status !== "ok").length;
  return (
    <p className="run-line">
      <span>
        Checked {plural(tools, "alarm tool")} and {plural(searches, "document search", "document searches")} in{" "}
        {seconds(response.timings.total_ms)}
        {failed > 0 && <strong className="run-failed">, {plural(failed, "step")} failed</strong>}
      </span>
      <button type="button" className="link" onClick={onShowSteps}>
        Show steps
      </button>
    </p>
  );
}

function EmptyState({ examples, onAsk, disabled }: { examples: string[]; onAsk: (q: string) => void; disabled: boolean }) {
  return (
    <section className="empty" aria-labelledby="empty-title">
      <h2 id="empty-title">What would you like to investigate?</h2>
      <p>Ask about an asset, an alarm or a site. Every answer shows the alarm data and documents behind it.</p>
      <ul className="examples">
        {examples.map((example) => (
          <li key={example}>
            <button type="button" onClick={() => onAsk(example)} disabled={disabled}>
              {example}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

const MAX_LENGTH = 2000;

function Composer({ onAsk, running }: { onAsk: (question: string) => void; running: boolean }) {
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const question = text.trim();

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!question || running) return;
    onAsk(question);
    setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  useEffect(() => {
    if (!running) input.current?.focus();
  }, [running]);

  return (
    <form className="composer" onSubmit={submit}>
      <div className="column composer-row">
        <label htmlFor="question" className="visually-hidden">
          Question
        </label>
        <textarea
          id="question"
          ref={input}
          rows={1}
          maxLength={MAX_LENGTH}
          value={text}
          placeholder="Ask about an alarm, asset or site"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" className="button button-primary" disabled={!question || running}>
          {running ? "Working" : "Ask"}
        </button>
      </div>
    </form>
  );
}
