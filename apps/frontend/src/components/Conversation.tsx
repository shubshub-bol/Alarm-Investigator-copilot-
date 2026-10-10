import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { plural, seconds } from "../lib/format";
import type { Turn } from "../state/useChat";
import { Answer } from "./Answer";
import { LiveSteps } from "./LiveSteps";

interface Props {
  turns: Turn[];
  running: boolean;
  examples: string[];
  selection: { turnId: string; key: string } | null;
  onAsk: (question: string) => void;
  onRetry: (turnId: string) => void;
  onCite: (turnId: string, key: string) => void;
  onShowTrace: (turnId: string) => void;
}

export function Conversation({ turns, running, examples, selection, onAsk, onRetry, onCite, onShowTrace }: Props) {
  const log = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastTurn = turns[turns.length - 1];

  // New question: jump to it.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  // Live steps and the answer: follow along only if the reader is already at the bottom.
  useEffect(() => {
    const el = log.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) end.current?.scrollIntoView({ block: "end" });
  }, [lastTurn?.steps.length, lastTurn?.phase]);

  return (
    <main className="conversation">
      <div className="log" ref={log}>
        {turns.length === 0 ? (
          <EmptyState examples={examples} onAsk={onAsk} disabled={running} />
        ) : (
          turns.map((turn) => (
            <section key={turn.id} className="turn" aria-label={`Question: ${turn.question}`}>
              <p className="question">{turn.question}</p>
              {turn.phase === "running" && <LiveSteps turn={turn} />}
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
                  {turn.response.trace.length > 0 && (
                    <p className="run-summary">
                      {plural(turn.response.trace.length, "step")} in {seconds(turn.response.timings.total_ms)}
                      <button type="button" className="link" onClick={() => onShowTrace(turn.id)}>
                        View trace
                      </button>
                    </p>
                  )}
                  <Answer
                    response={turn.response}
                    selectedKey={selection?.turnId === turn.id ? selection.key : null}
                    onCite={(key) => onCite(turn.id, key)}
                    onAsk={onAsk}
                    canAsk={!running && turn === turns[turns.length - 1]}
                  />
                </>
              )}
            </section>
          ))
        )}
        <div ref={end} />
      </div>
      <Composer onAsk={onAsk} running={running} />
    </main>
  );
}

function EmptyState({ examples, onAsk, disabled }: { examples: string[]; onAsk: (q: string) => void; disabled: boolean }) {
  return (
    <section className="empty" aria-labelledby="empty-title">
      <h2 id="empty-title">Start an investigation</h2>
      <p>
        Ask about an asset, an alarm or a site. The copilot queries the alarm system, reads the plant procedures and
        shows the evidence behind every statement.
      </p>
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
      <label htmlFor="question" className="visually-hidden">
        Question
      </label>
      <textarea
        id="question"
        ref={input}
        rows={2}
        maxLength={MAX_LENGTH}
        value={text}
        placeholder="Ask about an asset, alarm or site. For example: why does Boiler Feed Pump 101 keep alarming?"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer-actions">
        {text.length > MAX_LENGTH - 200 && (
          <span className="muted">
            {text.length}/{MAX_LENGTH}
          </span>
        )}
        <button type="submit" className="button button-primary" disabled={!question || running}>
          {running ? "Investigating" : "Investigate"}
        </button>
      </div>
    </form>
  );
}
