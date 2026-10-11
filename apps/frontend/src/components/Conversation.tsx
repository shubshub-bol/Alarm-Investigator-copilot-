import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import type { ChatStatus, Turn } from "../state/useChat";
import { Answer } from "./Answer";
import type { DrawerTab } from "./EvidenceDrawer";
import { ArrowUpIcon } from "./icons";
import { Progress } from "./Progress";

interface Props {
  turns: Turn[];
  status: ChatStatus;
  loadError: string | null;
  running: boolean;
  examples: string[];
  selection: { turnId: string; key: string } | null;
  onAsk: (question: string) => void;
  onRetry: (turnId: string) => void;
  onCite: (turnId: string, key: string) => void;
  onOpenEvidence: (turnId: string, tab: DrawerTab) => void;
  onNewChat: () => void;
  onReload: () => void;
}

export function Conversation(props: Props) {
  const { turns, status, loadError, running, examples, selection } = props;
  const { onAsk, onRetry, onCite, onOpenEvidence, onNewChat, onReload } = props;
  const log = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastTurn = turns[turns.length - 1];

  // New question or another chat opened: jump to the latest turn.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  // Progress and the answer: follow along only if the reader is already at the bottom.
  useEffect(() => {
    const el = log.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) end.current?.scrollIntoView({ block: "end" });
  }, [lastTurn?.steps.length, lastTurn?.phase]);

  if (status !== "ready") {
    return (
      <main className="conversation conversation-centred">
        <ChatMessage status={status} error={loadError} onNewChat={onNewChat} onReload={onReload} />
      </main>
    );
  }

  if (turns.length === 0) {
    return (
      <main className="conversation conversation-centred">
        <section className="empty column" aria-labelledby="empty-title">
          <h2 id="empty-title">What would you like to investigate?</h2>
          <Composer onAsk={onAsk} running={running} placeholder="Ask about an alarm, asset or site" />
          <ul className="examples">
            {examples.map((example) => (
              <li key={example}>
                <button type="button" onClick={() => onAsk(example)} disabled={running}>
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </section>
      </main>
    );
  }

  return (
    <main className="conversation">
      <div className="log" ref={log}>
        <div className="column">
          {turns.map((turn) => (
            <section key={turn.id} className="turn" aria-label={`Question: ${turn.question}`}>
              <p className="question">{turn.question}</p>
              {turn.phase === "running" && (
                <Progress turn={turn} onShowSteps={() => onOpenEvidence(turn.id, "steps")} />
              )}
              {turn.phase === "failed" && (
                <div className="failure" role="alert">
                  <p>The investigation did not finish. {turn.error}</p>
                  <button type="button" className="button" onClick={() => onRetry(turn.id)} disabled={running}>
                    Try again
                  </button>
                </div>
              )}
              {turn.phase === "done" && turn.response && (
                <Answer
                  response={turn.response}
                  selectedKey={selection?.turnId === turn.id ? selection.key : null}
                  onCite={(key) => onCite(turn.id, key)}
                  onOpen={(tab) => onOpenEvidence(turn.id, tab)}
                />
              )}
            </section>
          ))}
          <div ref={end} />
        </div>
      </div>
      <Composer onAsk={onAsk} running={running} placeholder="Ask a follow-up" />
    </main>
  );
}

/** Instead of the conversation: opening it, it is gone, or it could not be opened. */
function ChatMessage(props: { status: ChatStatus; error: string | null; onNewChat: () => void; onReload: () => void }) {
  const { status, error, onNewChat, onReload } = props;
  if (status === "loading") {
    return (
      <p className="chat-message muted" role="status">
        Opening chat…
      </p>
    );
  }
  if (status === "not-found") {
    return (
      <div className="chat-message">
        <h2>This chat doesn't exist</h2>
        <p>It may have been deleted.</p>
        <button type="button" className="button button-primary" onClick={onNewChat}>
          New chat
        </button>
      </div>
    );
  }
  return (
    <div className="chat-message" role="alert">
      <h2>Couldn't open this chat</h2>
      <p>{error}</p>
      <button type="button" className="button" onClick={onReload}>
        Try again
      </button>
    </div>
  );
}

const MAX_LENGTH = 2000;

function Composer({ onAsk, running, placeholder }: { onAsk: (question: string) => void; running: boolean; placeholder: string }) {
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
          placeholder={placeholder}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" className="send" aria-label={running ? "Working" : "Ask"} disabled={!question || running}>
          <ArrowUpIcon />
        </button>
      </div>
    </form>
  );
}
