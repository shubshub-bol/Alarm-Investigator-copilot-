import type { CopilotAnswer, Intent } from "../api/types";

export const INTENT_LABEL: Record<Intent, string> = {
  investigate_asset: "Investigate an asset",
  active_alarms: "Active alarms and immediate actions",
  highest_priority: "Highest-priority alarm",
  related_assets: "Related assets to inspect",
  procedure_lookup: "Applicable procedure",
  recommendation_check: "Check API advice against the documents",
  knowledge_question: "Question about the documents",
  conversation: "Conversation",
  agent: "Answered with the alarm tools",
};

/**
 * Steps stay on the answer when it is about what to do or why, or when a warning points at them.
 * Otherwise they move to the evidence panel, so each step appears once.
 */
export function stepsOnAnswer(answer: CopilotAnswer): boolean {
  const type = answer.answer_type;
  const conflict = answer.action_assessments.some((a) => a.verdict === "conflict");
  return !type || type === "what_to_do" || type === "why" || conflict;
}

export function seconds(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function percent(ratio: number | null | undefined): string {
  return ratio == null ? "n/a" : `${Math.round(ratio * 100)}%`;
}

export function signed(value: number | null | undefined): string {
  if (value == null) return "n/a";
  return `${value > 0 ? "+" : ""}${Math.round(value)}%`;
}

export function shortTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}
