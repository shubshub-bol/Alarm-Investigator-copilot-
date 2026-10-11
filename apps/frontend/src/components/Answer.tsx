import type { ReactElement } from "react";
import type { ChatResponse, Statement } from "../api/types";
import { splitCitations } from "../lib/citations";
import { CitationChip } from "./CitationChip";
import { PriorityBadge } from "./PriorityBadge";

interface Props {
  response: ChatResponse;
  selectedKey: string | null;
  onCite: (key: string) => void;
  onShowEvidence: () => void;
}

/** The answer, kept short: summary, what to do, any conflict to check. Everything else is in the drawer. */
export function Answer({ response, selectedKey, onCite, onShowEvidence }: Props) {
  const { answer, evidence, discovery, status } = response;
  const chip = chipRenderer(response, selectedKey, onCite);
  const conflicts = answer.action_assessments.filter((a) => a.verdict === "conflict");

  if (response.understanding.intent === "conversation") {
    return (
      <article className="answer" aria-label="Reply">
        <p className="summary">{answer.summary}</p>
      </article>
    );
  }

  let condition: string | null = null;
  if (discovery && !discovery.connected) condition = "Alarm data was unavailable, so this answer uses the documents only.";
  else if (answer.grounding.degraded) condition = "The language model was unavailable, so this is a template answer.";
  else if (status === "partial") condition = "Some checks failed; see the evidence for what is missing.";

  return (
    <article className="answer" aria-label="Answer">
      {condition && (
        <p className="condition" role="status">
          {condition}
        </p>
      )}
      {(evidence.priority || evidence.asset) && (
        <header className="answer-head">
          {evidence.priority && (
            <PriorityBadge
              priority={evidence.priority.priority}
              score={evidence.priority.score}
              label={evidence.priority.priority_label}
            />
          )}
          <span className="subject">
            {evidence.focus_alarm
              ? `${evidence.focus_alarm.asset_name}, ${evidence.focus_alarm.alarm_name}`
              : evidence.asset?.name}
          </span>
        </header>
      )}

      <p className="summary">
        {splitCitations(answer.summary).map((part, i) =>
          part.kind === "text" ? <span key={i}>{part.text}</span> : <span key={i}>{chip(part.key)}</span>,
        )}
      </p>

      {answer.recommended_actions.length > 0 && (
        <section aria-labelledby={`${response.trace_id}-todo`}>
          <h3 id={`${response.trace_id}-todo`}>What to do</h3>
          <StatementList items={answer.recommended_actions} chip={chip} ordered />
        </section>
      )}

      {conflicts.map((c) => (
        <div key={c.step} className="warning" role="note">
          <strong>Check before acting.</strong> The Alarm API suggests “{c.action}”, but the documents say:{" "}
          {c.document_guidance} <span className="chips">{c.citation_keys.map(chip)}</span>
        </div>
      ))}

      {answer.citations.length === 0 && answer.facts.length === 0 && (
        <p className="note">No alarm data or document passages supported this answer.</p>
      )}

      <footer className="answer-foot">
        <button type="button" className="button" onClick={onShowEvidence}>
          Show evidence
        </button>
      </footer>
    </article>
  );
}

export function chipRenderer(response: ChatResponse, selectedKey: string | null, onCite: (key: string) => void) {
  const { answer } = response;
  return (key: string): ReactElement => {
    const fact = answer.facts.find((f) => f.key === key);
    const cite = answer.citations.find((c) => c.key === key);
    const title = fact ? `Tool data from step ${fact.step}: ${fact.tool}` : cite ? `${cite.label}: ${cite.title}` : key;
    return <CitationChip key={key} citeKey={key} selected={selectedKey === key} onSelect={onCite} title={title} />;
  };
}

export function StatementList({
  items,
  chip,
  ordered = false,
}: {
  items: Statement[];
  chip: (key: string) => ReactElement;
  ordered?: boolean;
}) {
  const List = ordered ? "ol" : "ul";
  return (
    <List>
      {items.map((s, i) => (
        <li key={i}>
          {s.text} <span className="chips">{s.citations.map(chip)}</span>
        </li>
      ))}
    </List>
  );
}
