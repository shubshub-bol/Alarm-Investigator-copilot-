import type { ReactElement } from "react";
import type { ActionAssessment, ChatResponse, Statement } from "../api/types";
import { splitCitations } from "../lib/citations";
import { seconds } from "../lib/format";
import { CitationChip } from "./CitationChip";
import { PriorityBadge } from "./PriorityBadge";

interface Props {
  response: ChatResponse;
  selectedKey: string | null;
  onCite: (key: string) => void;
  onAsk: (question: string) => void;
  canAsk: boolean; // follow-ups are offered on the latest answer only
}

/** The grounded answer. Every statement ends with its evidence chips; text is rendered as text, never HTML. */
export function Answer({ response, selectedKey, onCite, onAsk, canAsk }: Props) {
  const { answer, evidence } = response;
  const suggestions = canAsk && answer.suggestions.length > 0 && (
    <nav className="suggestions" aria-label="Suggested next questions">
      <h3>Ask next</h3>
      <ul>
        {answer.suggestions.map((q) => (
          <li key={q}>
            <button type="button" onClick={() => onAsk(q)}>
              {q}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );

  if (response.understanding.intent === "conversation") {
    return (
      <article className="answer answer-reply" aria-label="Reply">
        <p className="summary">{answer.summary}</p>
        {suggestions}
      </article>
    );
  }
  const chip = (key: string) => (
    <CitationChip key={key} citeKey={key} selected={selectedKey === key} onSelect={onCite} title={titleFor(key)} />
  );

  function titleFor(key: string): string {
    const fact = answer.facts.find((f) => f.key === key);
    if (fact) return `Tool fact from step ${fact.step}: ${fact.tool}`;
    const cite = answer.citations.find((c) => c.key === key);
    return cite ? `${cite.label}: ${cite.title}` : key;
  }

  const statementList = (items: Statement[], ordered = false) => {
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
  };

  return (
    <article className="answer" aria-label="Answer">
      <AnswerHeader response={response} />
      <p className="summary">
        {splitCitations(answer.summary).map((part, i) =>
          part.kind === "text" ? <span key={i}>{part.text}</span> : <span key={i}>{chip(part.key)}</span>,
        )}
      </p>

      {answer.recommended_actions.length > 0 && (
        <section aria-labelledby={`${response.trace_id}-actions`}>
          <h3 id={`${response.trace_id}-actions`}>Recommended actions</h3>
          {statementList(answer.recommended_actions, true)}
        </section>
      )}
      {answer.action_assessments.length > 0 && (
        <Verdicts assessments={answer.action_assessments} chip={chip} traceId={response.trace_id} />
      )}
      {answer.likely_causes.length > 0 && (
        <section>
          <h3>Likely contributing factors</h3>
          {statementList(answer.likely_causes)}
        </section>
      )}
      {answer.procedure.length > 0 && (
        <section>
          <h3>Applicable procedure</h3>
          {statementList(answer.procedure)}
        </section>
      )}
      {answer.findings.length > 0 && (
        <details className="findings">
          <summary>What the data shows ({answer.findings.length})</summary>
          {statementList(answer.findings)}
        </details>
      )}
      {answer.gaps.length > 0 && (
        <section className="gaps">
          <h3>Gaps and assumptions</h3>
          <ul>
            {answer.gaps.map((gap, i) => (
              <li key={i}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
      {evidence.passages.length === 0 && <p className="note">No document passages supported this answer.</p>}
      {suggestions}
    </article>
  );
}

function AnswerHeader({ response }: { response: ChatResponse }) {
  const { evidence, answer, status, timings, discovery } = response;
  const subject = evidence.focus_alarm
    ? `${evidence.focus_alarm.asset_name}, ${evidence.focus_alarm.alarm_name}`
    : evidence.asset?.name;
  const tag = evidence.focus_alarm?.alarm_id ?? evidence.asset?.asset_id;
  const mode = answer.grounding.mode === "llm" ? (answer.grounding.model ?? "language model") : "template";

  let condition: string | null = null;
  if (discovery && !discovery.connected) condition = "Alarm data unavailable: answered from documents only";
  else if (answer.grounding.degraded) condition = "Language model unavailable: template answer";
  else if (status === "partial") condition = "Some steps failed: see gaps";

  return (
    <header className="answer-head">
      <div className="answer-subject">
        {evidence.priority && (
          <PriorityBadge
            priority={evidence.priority.priority}
            score={evidence.priority.score}
            label={evidence.priority.priority_label}
          />
        )}
        {subject && <span className="subject">{subject}</span>}
        {tag && <code className="tag">{tag}</code>}
      </div>
      <div className="answer-meta">
        {condition && (
          <span className="condition" role="status">
            {condition}
          </span>
        )}
        <span>
          Written by {mode} in {seconds(timings.total_ms)}
        </span>
      </div>
    </header>
  );
}

const VERDICT_TEXT: Record<ActionAssessment["verdict"], string> = {
  conflict: "Conflict",
  consistent: "Matches",
  not_covered: "Not covered",
};

function Verdicts({
  assessments,
  chip,
  traceId,
}: {
  assessments: ActionAssessment[];
  chip: (key: string) => ReactElement;
  traceId: string;
}) {
  return (
    <section aria-labelledby={`${traceId}-verdicts`}>
      <h3 id={`${traceId}-verdicts`}>Alarm API advice checked against the documents</h3>
      <table className="verdicts">
        <thead>
          <tr>
            <th scope="col">API advice</th>
            <th scope="col">Verdict</th>
            <th scope="col">What the documents say</th>
          </tr>
        </thead>
        <tbody>
          {assessments.map((a) => (
            <tr key={a.step} data-verdict={a.verdict}>
              <td>{a.action}</td>
              <td>
                <span className="verdict" data-verdict={a.verdict}>
                  {VERDICT_TEXT[a.verdict]}
                </span>
              </td>
              <td>
                {a.document_guidance ?? <span className="muted">{a.explanation}</span>}{" "}
                <span className="chips">{a.citation_keys.map(chip)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
