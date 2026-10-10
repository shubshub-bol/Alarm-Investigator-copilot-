"""Tool facts: short, citable statements ([T#]) built from MCP results, each tied to its trace step."""

from collections.abc import Callable

from pydantic import BaseModel

from copilot.investigation import InvestigationRun
from copilot.trace import ToolCallRecord


class Fact(BaseModel):
    key: str  # "T1", "T2", ...
    step: int  # the trace step that produced it
    tool: str
    statement: str


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def build_facts(run: InvestigationRun) -> list[Fact]:
    ev = run.evidence
    tool_steps = [s for s in run.steps if isinstance(s, ToolCallRecord) and s.status == "ok"]
    facts: list[Fact] = []

    def find(tool: str, match: Callable[[ToolCallRecord], bool] = lambda s: True) -> ToolCallRecord | None:
        return next((s for s in tool_steps if s.tool == tool and match(s)), None)

    def add(step: ToolCallRecord | None, statement: str) -> None:
        if step is not None:
            facts.append(Fact(key=f"T{len(facts) + 1}", step=step.step, tool=step.tool, statement=statement))

    asset = ev.asset
    if asset:
        related = ", ".join(a["name"] for a in ev.related_assets) or "none recorded"
        add(
            find("get_asset_metadata", lambda s: s.arguments.get("asset_id") == asset["asset_id"])
            or find("get_alarm_details"),
            f"{asset['name']} ({asset['asset_id']}) is a {asset.get('criticality', '?')}-criticality "
            f"{asset['asset_type']} at {asset['site']} {asset['unit']}. Related assets: {related}.",
        )
    if ev.top_alarm and asset:
        t = ev.top_alarm
        totals = (ev.alarm_summary or {}).get("totals", {})
        ack = f"{t['avg_ack_delay']} min" if t.get("avg_ack_delay") is not None else "n/a"
        add(
            find("summarize_alarms", lambda s: s.arguments.get("asset_ids") == [asset["asset_id"]]),
            f"In the last {ev.lookback_days} days {asset['name']} had {totals.get('alarm_count', '?')} alarms in "
            f"scope; the most frequent was {t['alarm_name']} ({t['alarm_code']}) with {t['alarm_count']} "
            f"occurrences, recurring rate {_pct(t.get('recurring_rate'))}, average time to acknowledge {ack}.",
        )
    if ev.trend and ev.top_alarm:
        tr = ev.trend
        change = f"{tr['change_percent']:+.0f}%" if tr.get("change_percent") is not None else "n/a"
        add(
            find("get_alarm_trends"),
            f"{ev.top_alarm['alarm_name']} is {tr['direction']}: {tr['first_half_avg']:.1f} -> "
            f"{tr['second_half_avg']:.1f} alarms per week, first half vs second half of the window ({change}).",
        )
    for factor in ev.contributing_factors:
        add(find("correlate_alarms"), factor["insight"])
    if ev.active_alarms:
        names = "; ".join(f"{a['asset_name']} {a['alarm_name']} ({a['severity']})" for a in ev.active_alarms[:8])
        add(
            find("get_alarms", lambda s: s.arguments.get("status") == "active"),
            f"{len(ev.active_alarms)} active alarm(s): {names}.",
        )
    for rank, scored in enumerate(ev.ranked_alarms, 1):
        add(
            find("score_alarm_priority", lambda s, a=scored["alarm_id"]: s.arguments.get("alarm_id") == a),
            f"Rank {rank}: {scored['asset_name']} {scored['alarm_name']} ({scored['alarm_id']}) scores "
            f"{scored['score']}/100, {scored['priority']} ({scored['priority_label']}).",
        )
    focus = ev.focus_alarm
    if focus and not ev.ranked_alarms:
        value = (
            f", value {focus['value']} {focus['uom'] or ''} vs setpoint {focus['setpoint']}"
            if focus.get("value")
            else ""
        )
        add(
            find("get_alarms", lambda s: s.arguments.get("page_size") == 1) or find("get_alarm_details"),
            f"Latest {focus['alarm_name']}: {focus['alarm_id']} at {focus['start_time']} ({focus['status']}{value}).",
        )
    if ev.priority and not ev.ranked_alarms:
        p = ev.priority
        reasons = "; ".join(f["explanation"] for f in p.get("factors", []))
        add(
            find("score_alarm_priority", lambda s: s.arguments.get("alarm_id") == p.get("alarm_id")),
            f"Priority of {p.get('alarm_id')}: {p['score']}/100, {p['priority']} ({p['priority_label']})"
            + (f" because {reasons}." if reasons else "."),
        )
    recs = ev.recommendations
    if recs:
        rec_step = find("get_operator_recommendations")
        actions = " ".join(f"({a['step']}) {a['action']}." for a in recs["recommended_actions"])
        add(
            rec_step,
            f"The Alarm API ({recs['source']}) lists likely causes: {', '.join(recs['likely_causes'])}; "
            f"and recommends: {actions}",
        )
        for related in recs.get("related_assets") or []:
            active = ", ".join(a["alarm_name"] for a in related["active_alarms"]) or "no active alarms"
            add(
                rec_step,
                f"Related asset {related['name']}: {active}; {related['alarms_prior_7_days']} alarm(s) in the "
                "7 days before the alarm.",
            )
        history = recs.get("historical_pattern")
        if history:
            add(
                rec_step,
                f"History: {history['occurrences_prior_90_days']} occurrences of this alarm in the 90 days before it "
                f"({history['occurrences_prior_30_days']} in the last 30), trend {history['trend_direction']}.",
            )
    return facts
