import type { ReactElement } from "react";
import type { ChatResponse, Evidence, Statement } from "../api/types";
import { splitCitations } from "../lib/citations";
import { plural, seconds } from "../lib/format";
import { CitationChip } from "./CitationChip";
import type { DrawerTab } from "./EvidenceDrawer";
import { AlarmIcon, SourcesIcon, StepsIcon } from "./icons";
import { PriorityBadge } from "./PriorityBadge";

interface Props {
  response: ChatResponse;
  selectedKey: string | null;
  onCite: (key: string) => void;
  onOpen: (tab: DrawerTab) => void; // opens the evidence panel on that tab
}

/** The answer, kept short: summary, what to do, any conflict to check. Everything else is in the drawer. */
export function Answer({ response, selectedKey, onCite, onOpen }: Props) {
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
          <strong>Check before acting.</strong> The Alarm API suggests “{c.action}”,{" "}
          {c.document_guidance ? `but the documents say: ${c.document_guidance}` : "but the documents disagree."}{" "}
          <span className="chips">{c.citation_keys.map(chip)}</span>
        </div>
      ))}

      {answer.citations.length === 0 && answer.facts.length === 0 && (
        <p className="note">No alarm data or document passages supported this answer.</p>
      )}

      <footer className="answer-foot">
        <EvidenceLinks response={response} onOpen={onOpen} />
      </footer>
    </article>
  );
}

const hasAlarmData = (e: Evidence) =>
  Boolean(e.asset || e.focus_alarm || e.top_alarm || e.ranked_alarms.length || e.active_alarms.length);

/** Where the answer comes from, one click each: the alarm summary, the documents it quotes, the steps it took. */
function EvidenceLinks({ response, onOpen }: { response: ChatResponse; onOpen: (tab: DrawerTab) => void }) {
  const sources = response.answer.citations.length;
  const steps = response.trace.length;
  const failed = response.trace.filter((s) => s.status !== "ok").length;
  return (
    <nav className="answer-links" aria-label="Where this answer comes from">
      {hasAlarmData(response.evidence) && (
        <button type="button" onClick={() => onOpen("evidence")}>
          <AlarmIcon />
          Alarm details
        </button>
      )}
      {sources > 0 && (
        <button type="button" onClick={() => onOpen("sources")}>
          <SourcesIcon />
          {plural(sources, "source")}
        </button>
      )}
      {steps > 0 && (
        <button type="button" onClick={() => onOpen("steps")}>
          <StepsIcon />
          {plural(steps, "step")} in {seconds(response.timings.total_ms)}
          {failed > 0 && <strong className="link-failed">, {failed} failed</strong>}
        </button>
      )}
    </nav>
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
