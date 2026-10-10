import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, UNREACHABLE, streamChat } from "../api/client";
import type { ChatResponse, Discovery, TraceStep, Understanding } from "../api/types";

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

let counter = 0;
const nextId = () => `turn-${++counter}`;

/** One conversation: questions are asked one at a time and their progress streams in. */
export function useChat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const conversationId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const update = useCallback((id: string, change: (turn: Turn) => Turn) => {
    setTurns((all) => all.map((turn) => (turn.id === id ? change(turn) : turn)));
  }, []);

  const ask = useCallback(
    async (question: string) => {
      const id = nextId();
      setTurns((all) => [...all, { id, question, phase: "running", startedAt: Date.now(), steps: [] }]);
      const abort = new AbortController();
      controller.current = abort;
      const fail = (message: string) =>
        update(id, (turn) => ({ ...turn, phase: "failed", error: message, finishedAt: Date.now() }));

      try {
        await streamChat(
          question,
          conversationId.current,
          {
            started: ({ trace_id, conversation_id }) => {
              conversationId.current = conversation_id;
              update(id, (turn) => ({ ...turn, traceId: trace_id }));
            },
            understanding: (understanding) => update(id, (turn) => ({ ...turn, understanding })),
            discovery: (discovery) => update(id, (turn) => ({ ...turn, discovery })),
            step: (step) => update(id, (turn) => ({ ...turn, steps: [...turn.steps, step] })),
            answer: (response) => {
              conversationId.current = response.conversation_id;
              update(id, (turn) => ({
                ...turn,
                phase: "done",
                response,
                steps: response.trace,
                discovery: response.discovery ?? undefined,
                understanding: response.understanding,
                finishedAt: Date.now(),
              }));
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
    [update],
  );

  const retry = useCallback(
    (id: string) => {
      const failed = turns.find((turn) => turn.id === id);
      if (!failed) return;
      setTurns((all) => all.filter((turn) => turn.id !== id));
      void ask(failed.question);
    },
    [turns, ask],
  );

  const reset = useCallback(() => {
    controller.current?.abort();
    conversationId.current = null;
    setTurns([]);
  }, []);

  useEffect(() => () => controller.current?.abort(), []);

  return { turns, running: turns.some((turn) => turn.phase === "running"), ask, retry, reset };
}
