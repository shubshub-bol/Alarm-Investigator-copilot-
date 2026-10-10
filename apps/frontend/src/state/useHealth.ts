import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import type { Health } from "../api/types";

export type HealthState = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "unreachable" };

/** Backend readiness: every 30 s while reachable, every 5 s while starting up or down. */
export function useHealth(okIntervalMs = 30_000, retryIntervalMs = 5_000): HealthState {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  const reachable = state.kind === "ok";
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getHealth()
        .then((health) => !cancelled && setState({ kind: "ok", health }))
        .catch(() => !cancelled && setState({ kind: "unreachable" }));
    if (state.kind === "loading") void load();
    const timer = window.setInterval(load, reachable ? okIntervalMs : retryIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [reachable, okIntervalMs, retryIntervalMs]); // re-arm only when reachability changes
  return state;
}
