import type { ReactElement } from "react";
import type { ActionAssessment, Evidence } from "../../api/types";
import { percent, shortTime, signed, stepsOnAnswer } from "../../lib/format";
import type { Turn } from "../../state/useChat";
import { StatementList, chipRenderer } from "../Answer";
import { PriorityBadge } from "../PriorityBadge";

interface Props {
  turn: Turn;
  selectedKey: string | null;
  onCite: (key: string) => void;
}

/** Alarm summary and the full detail behind the answer: data, causes, procedure, steps, API check, gaps. */
export function EvidencePanel({ turn, selectedKey, onCite }: Props) {
  const response = turn.response;
  if (!response) return <p className="muted">Evidence appears here when the investigation finishes.</p>;
  const { evidence, answer } = response;
  if (evidence.intent === "conversation") {
    return <p className="muted">No evidence was needed for this reply: it used no tools and no documents.</p>;
  }
  const chip = chipRenderer(response, selectedKey, onCite);
  const noAlarmData =
    !evidence.asset && !evidence.top_alarm && evidence.ranked_alarms.length === 0 && !evidence.focus_alarm;
  return (
    <div className="evidence">
      {evidence.asset && <AssetBlock evidence={evidence} />}
      {evidence.top_alarm && <AlarmPicture evidence={evidence} />}
      {evidence.contributing_factors.length > 0 && <Factors evidence={evidence} />}
      {evidence.ranked_alarms.length > 0 && <Ranked evidence={evidence} />}
      {!evidence.top_alarm && evidence.focus_alarm && (
        <section className="ev-block">
          <h3>Alarm</h3>
          <p>
            {evidence.focus_alarm.alarm_name} on {evidence.focus_alarm.asset_name}{" "}
            <code className="tag">{evidence.focus_alarm.alarm_id}</code>
          </p>
          <p className="muted">
            {evidence.focus_alarm.status}, started {shortTime(evidence.focus_alarm.start_time)}
          </p>
        </section>
      )}
      <RelatedStatus evidence={evidence} />
      {noAlarmData && <p className="ev-block muted">No live alarm data was used for this answer.</p>}

      {answer.likely_causes.length > 0 && (
        <section className="ev-block">
          <h3>Likely causes</h3>
          <StatementList items={answer.likely_causes} chip={chip} />
        </section>
      )}
      {answer.procedure.length > 0 && (
        <section className="ev-block">
          <h3>Applicable procedure</h3>
          <StatementList items={answer.procedure} chip={chip} />
        </section>
      )}
      {answer.recommended_actions.length > 0 && !stepsOnAnswer(answer) && (
        <section className="ev-block">
          <h3>What to do</h3>
          <StatementList items={answer.recommended_actions} chip={chip} ordered />
        </section>
      )}
      {answer.action_assessments.length > 0 && <Verdicts assessments={answer.action_assessments} chip={chip} />}
      {answer.findings.length > 0 && (
        <section className="ev-block">
          <h3>Measured data</h3>
          <StatementList items={answer.findings} chip={chip} />
        </section>
      )}
      {answer.gaps.length > 0 && (
        <section className="ev-block">
          <h3>Gaps and assumptions</h3>
          <ul>
            {answer.gaps.map((gap, i) => (
              <li key={i}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

const VERDICT_TEXT: Record<ActionAssessment["verdict"], string> = {
  conflict: "Conflict",
  consistent: "Matches",
  not_covered: "Not covered",
};

function Verdicts({ assessments, chip }: { assessments: ActionAssessment[]; chip: (key: string) => ReactElement }) {
  return (
    <section className="ev-block">
      <h3>Alarm API advice checked against the documents</h3>
      <ul className="verdicts">
        {assessments.map((a) => (
          <li key={a.step} data-verdict={a.verdict}>
            <p>
              <span className="verdict" data-verdict={a.verdict}>
                {VERDICT_TEXT[a.verdict]}
              </span>{" "}
              {a.action}
            </p>
            <p className="muted">
              {a.document_guidance ?? a.explanation} <span className="chips">{a.citation_keys.map(chip)}</span>
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function AssetBlock({ evidence }: { evidence: Evidence }) {
  const asset = evidence.asset!;
  return (
    <section className="ev-block ev-asset">
      <h2>{asset.name}</h2>
      <p className="ev-asset-line">
        <code className="tag">{asset.asset_id}</code>
        <span>{asset.asset_type}</span>
        <span>
          {asset.site}, {asset.unit}
        </span>
      </p>
      <dl className="facts-grid">
        <dt>Criticality</dt>
        <dd>{asset.criticality}</dd>
        {asset.manufacturer && (
          <>
            <dt>Make</dt>
            <dd>
              {asset.manufacturer} {asset.model}
            </dd>
          </>
        )}
        {evidence.related_assets.length > 0 && (
          <>
            <dt>Connected to</dt>
            <dd>{evidence.related_assets.map((a) => a.name).join(", ")}</dd>
          </>
        )}
      </dl>
    </section>
  );
}

function AlarmPicture({ evidence }: { evidence: Evidence }) {
  const top = evidence.top_alarm!;
  const trend = evidence.trend;
  const max = trend ? Math.max(trend.first_half_avg, trend.second_half_avg, 0.1) : 1;
  return (
    <section className="ev-block">
      <h3>Most frequent alarm, last {evidence.lookback_days} days</h3>
      <p className="ev-alarm">
        {top.alarm_name} <code className="tag">{top.alarm_code}</code>
      </p>
      <dl className="kpis">
        <div>
          <dt>Occurrences</dt>
          <dd>{top.alarm_count}</dd>
        </div>
        <div>
          <dt>Repeats</dt>
          <dd>{percent(top.recurring_rate)}</dd>
        </div>
        <div>
          <dt>Time to acknowledge</dt>
          <dd>{top.avg_ack_delay == null ? "n/a" : `${top.avg_ack_delay} min`}</dd>
        </div>
      </dl>
      {trend && (
        <figure className="trend" aria-label={`Trend ${trend.direction}, ${signed(trend.change_percent)}`}>
          <div className="trend-bars" aria-hidden="true">
            <span style={{ height: `${(trend.first_half_avg / max) * 100}%` }} />
            <span data-direction={trend.direction} style={{ height: `${(trend.second_half_avg / max) * 100}%` }} />
          </div>
          <figcaption>
            <strong>{trend.direction}</strong> {signed(trend.change_percent)}: {trend.first_half_avg.toFixed(1)} per
            week in the first half of the window, {trend.second_half_avg.toFixed(1)} in the second half
          </figcaption>
        </figure>
      )}
    </section>
  );
}

function Factors({ evidence }: { evidence: Evidence }) {
  return (
    <section className="ev-block">
      <h3>What happens before it</h3>
      <ul className="factors">
        {evidence.contributing_factors.map((f) => (
          <li key={`${f.leading.asset_id}-${f.leading.alarm_code}`}>
            <p>
              {f.leading.asset_name}, {f.leading.alarm_name}
            </p>
            <div className="meter" aria-hidden="true">
              <span style={{ width: `${Math.round(f.confidence * 100)}%` }} />
            </div>
            <p className="muted">
              {f.cooccurrences} of {f.following.occurrences} times, a median {f.median_lead_minutes} min earlier (
              {percent(f.confidence)})
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Ranked({ evidence }: { evidence: Evidence }) {
  return (
    <section className="ev-block">
      <h3>Active alarms by priority</h3>
      <ol className="ranked">
        {evidence.ranked_alarms.map((a) => (
          <li key={a.alarm_id}>
            <PriorityBadge priority={a.priority} score={a.score} label={a.priority_label} />
            <span>
              {a.asset_name}, {a.alarm_name}
            </span>
            <code className="tag">{a.alarm_id}</code>
          </li>
        ))}
      </ol>
    </section>
  );
}

function RelatedStatus({ evidence }: { evidence: Evidence }) {
  const related = evidence.recommendations?.related_assets ?? [];
  if (related.length === 0) return null;
  return (
    <section className="ev-block">
      <h3>Connected assets now</h3>
      <ul className="related">
        {related.map((r) => (
          <li key={r.asset_id} data-active={r.active_alarms.length > 0}>
            <span>{r.name}</span>
            <span className="muted">
              {r.active_alarms.length > 0
                ? `${r.active_alarms.length} active: ${r.active_alarms.map((a) => a.alarm_name).join(", ")}`
                : "no active alarms"}
              {`; ${r.alarms_prior_7_days} in the prior 7 days`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
