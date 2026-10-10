import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, UNREACHABLE, getConversation, streamChat } from "../api/client";
import type { ChatResponse, Discovery, StoredTurn, TraceStep, Understanding } from "../api/types";

export interface Turn {
  id: string;
  question: string;
  phase: "running" | "done" | "failed";
  startedAt: number;
  finishedAt?: number;
  traceId?: string;
  understanding?: Understanding;
  discovery?: Discovery;
  steps: TraceStep[];
  response?: ChatResponse;
  error?: string;
}

/** "ready" shows the turns (none = a new chat); the others replace the conversation with a message. */
export type ChatStatus = "ready" | "loading" | "not-found" | "error";

let counter = 0;
const nextId = () => `turn-${++counter}`;

/** A saved turn, shown exactly like an answer that has just finished. */
function fromStored({ question, created_at, response }: StoredTurn): Turn {
  const finishedAt = Date.parse(created_at);
  return {
    id: nextId(),
    question,
    phase: "done",
    startedAt: finishedAt - response.timings.total_ms,
    finishedAt,
    traceId: response.trace_id,
    understanding: response.understanding,
    discovery: response.discovery ?? undefined,
    steps: response.trace,
    response,
  };
}

/**
 * The open conversation: a new one, or a saved one opened by id. Questions are asked one at a time and their
 * progress streams in. Opening another chat stops the running question, and anything it still delivers is ignored.
 */
export function useChat(onAnswer?: (response: ChatResponse) => void) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const idRef = useRef<string | null>(null); // the id the next question continues
  const controller = useRef<AbortController | null>(null);
  const answered = useRef(onAnswer);

  useEffect(() => {
    answered.current = onAnswer;
  });

  const openId = useCallback((id: string | null) => {
    idRef.current = id;
    setConversationId(id);
  }, []);

  /** Stops whatever request is running and makes a new one the current request. */
  const takeOver = useCallback(() => {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    return next;
  }, []);

  const update = useCallback((id: string, change: (turn: Turn) => Turn) => {
    setTurns((all) => all.map((turn) => (turn.id === id ? change(turn) : turn)));
  }, []);

  const ask = useCallback(
    async (question: string) => {
      const id = nextId();
      setTurns((all) => [...all, { id, question, phase: "running", startedAt: Date.now(), steps: [] }]);
      const abort = takeOver();
      const current = () => controller.current === abort; // false once another chat was opened
      const fail = (message: string) =>
        current() && update(id, (turn) => ({ ...turn, phase: "failed", error: message, finishedAt: Date.now() }));

      try {
        await streamChat(
          question,
          idRef.current,
          {
            started: ({ trace_id }) => current() && update(id, (turn) => ({ ...turn, traceId: trace_id })),
            understanding: (understanding) => current() && update(id, (turn) => ({ ...turn, understanding })),
            discovery: (discovery) => current() && update(id, (turn) => ({ ...turn, discovery })),
            step: (step) => current() && update(id, (turn) => ({ ...turn, steps: [...turn.steps, step] })),
            answer: (response) => {
              if (!current()) return;
              openId(response.conversation_id);
              update(id, (turn) => ({
                ...turn,
                phase: "done",
                response,
                steps: response.trace,
                discovery: response.discovery ?? undefined,
                understanding: response.understanding,
                finishedAt: Date.now(),
              }));
              answered.current?.(response);
            },
            error: (message) => fail(message),
          },
          abort.signal,
        );
      } catch (error) {
        if ((error as Error).name === "AbortError") return;
        fail(error instanceof ApiError ? error.message : UNREACHABLE);
      }
    },
    [update, takeOver, openId],
  );

  const load = useCallback(
    async (id: string) => {
      const abort = takeOver();
      openId(id);
      setTurns([]);
      setLoadError(null);
      setStatus("loading");
      try {
        const detail = await getConversation(id, abort.signal);
        if (controller.current !== abort) return;
        setTurns(detail.turns.map(fromStored));
        setStatus("ready");
      } catch (error) {
        if ((error as Error).name === "AbortError" || controller.current !== abort) return;
        if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
          openId(null); // a question asked from here starts a new chat
          setStatus("not-found");
        } else {
          setLoadError(error instanceof ApiError ? error.message : UNREACHABLE);
          setStatus("error");
        }
      }
    },
    [takeOver, openId],
  );

  const reset = useCallback(() => {
    takeOver();
    openId(null);
    setTurns([]);
    setLoadError(null);
    setStatus("ready");
  }, [takeOver, openId]);

  const retry = useCallback(
    (id: string) => {
      const failed = turns.find((turn) => turn.id === id);
      if (!failed) return;
      setTurns((all) => all.filter((turn) => turn.id !== id));
      void ask(failed.question);
    },
    [turns, ask],
  );

  useEffect(() => () => controller.current?.abort(), []);

  return {
    turns,
    status,
    loadError,
    conversationId,
    running: turns.some((turn) => turn.phase === "running"),
    ask,
    retry,
    load,
    reset,
  };
}
