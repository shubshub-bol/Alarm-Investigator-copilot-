"""Alarm analytics. Pure functions over lists of alarms - no HTTP.

Each function is deliberately simple and explainable; the docstrings state the exact rule used.
"""

import math
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

from alarm_simulator.analysis_schemas import (
    AlarmRef,
    Contributor,
    CorrelationPair,
    FloodWindow,
    InitiatingAlarm,
    KpiValues,
    PriorityFactor,
    RationalizationCandidate,
    SummaryGroup,
    TrendPoint,
    TrendSummary,
)
from alarm_simulator.data import DataStore
from alarm_simulator.models import SEVERITY_RANK, Alarm
from alarm_simulator.queries import ack_delay_minutes, duration_minutes

CHATTER_SECONDS = 120  # cleared faster than this = chattering
STALE_MINUTES = 24 * 60  # standing longer than this = stale
MAX_BUCKETS = 2000
BUCKET_SIZES = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def is_chattering(alarm: Alarm, seconds: int = CHATTER_SECONDS) -> bool:
    return alarm.end_time is not None and (alarm.end_time - alarm.start_time).total_seconds() < seconds


def is_stale(alarm: Alarm, now: datetime, minutes: int = STALE_MINUTES) -> bool:
    return duration_minutes(alarm, now) > minutes


# ------------------------------------------------------------------- KPIs
def compute_kpis(alarms: list[Alarm], kpis: list[str], now: datetime, occurrences: Counter) -> KpiValues:
    """occurrences counts (asset_id, alarm_code) across the whole analysed set, so a group's
    recurring_rate means 'share of these alarms that repeat somewhere in scope'."""
    n = len(alarms)
    out: KpiValues = {}
    for k in kpis:
        if k == "alarm_count":
            out[k] = n
        elif k == "critical_count":
            out[k] = sum(a.severity == "critical" for a in alarms)
        elif k == "recurring_rate":
            out[k] = _ratio(sum(occurrences[(a.asset_id, a.alarm_code)] > 1 for a in alarms), n)
        elif k == "avg_ack_delay":
            delays = [d for a in alarms if (d := ack_delay_minutes(a)) is not None]
            out[k] = round(mean(delays), 1) if delays else None
        elif k == "suppression_candidate_rate":
            out[k] = _ratio(sum(is_chattering(a) or is_stale(a, now) for a in alarms), n)
    return out


def occurrence_counts(alarms: list[Alarm]) -> Counter:
    return Counter((a.asset_id, a.alarm_code) for a in alarms)


def summarize(alarms: list[Alarm], group_by: list[str], kpis: list[str], now: datetime) -> tuple[KpiValues, list]:
    occ = occurrence_counts(alarms)
    totals = compute_kpis(alarms, kpis, now, occ)
    if not group_by:
        return totals, []
    buckets: dict[tuple, list[Alarm]] = defaultdict(list)
    for a in alarms:
        buckets[tuple(str(getattr(a, f)) for f in group_by)].append(a)
    groups = [
        SummaryGroup(key=dict(zip(group_by, key, strict=True)), kpis=compute_kpis(items, kpis, now, occ))
        for key, items in buckets.items()
    ]
    groups.sort(key=lambda g: (-len(buckets[tuple(g.key.values())]), tuple(g.key.values())))
    return totals, groups


# ------------------------------------------------------------------- trends
def bucket_count(start: datetime, end: datetime, bucket: str) -> int:
    return math.ceil((end - start) / BUCKET_SIZES[bucket])


def trends(alarms: list[Alarm], start: datetime, end: datetime, bucket: str, metrics: list[str], now: datetime):
    """Fixed-size buckets from range start (empty buckets included).

    Direction rule: compare the average alarm_count of the first and second half of the
    buckets; more than +20% = increasing, less than -20% = decreasing, otherwise stable.
    """
    size = BUCKET_SIZES[bucket]
    n = bucket_count(start, end, bucket)
    per_bucket: list[list[Alarm]] = [[] for _ in range(n)]
    for a in alarms:
        per_bucket[int((a.start_time - start) / size)].append(a)

    occ = occurrence_counts(alarms)
    series = [
        TrendPoint(bucket_start=start + i * size, values=compute_kpis(items, metrics, now, occ))
        for i, items in enumerate(per_bucket)
    ]

    counts = [len(b) for b in per_bucket]
    half = max(1, n // 2)
    first, second = mean(counts[:half]), mean(counts[half:] or [0])
    change = round((second - first) / first * 100, 1) if first else None
    if first == 0:
        direction = "increasing" if second > 0 else "stable"
    elif change is not None and change > 20:
        direction = "increasing"
    elif change is not None and change < -20:
        direction = "decreasing"
    else:
        direction = "stable"
    summary = TrendSummary(
        direction=direction, first_half_avg=round(first, 2), second_half_avg=round(second, 2), change_percent=change
    )
    return series, summary


# --------------------------------------------------------------- correlation
def _nearest(times: list[datetime], t: datetime) -> datetime | None:
    i = bisect_left(times, t)
    candidates = [times[j] for j in (i - 1, i) if 0 <= j < len(times)]
    return min(candidates, key=lambda c: abs(c - t)) if candidates else None


def _matches(source: list[datetime], target: list[datetime], lag: timedelta) -> list[float]:
    """For each source event with a target event within +/-lag: minutes from source to nearest target."""
    offsets = []
    for t in source:
        near = _nearest(target, t)
        if near is not None and abs(near - t) <= lag:
            offsets.append((near - t).total_seconds() / 60)
    return offsets


def correlate(store: DataStore, alarms: list[Alarm], lag_minutes: int, min_support: int) -> list[CorrelationPair]:
    """Co-occurrence: for every pair of alarm types (asset + alarm code), count how often one
    fires within +/-lag minutes of the other. The type that usually fires first is 'leading';
    confidence = share of 'following' occurrences that had a 'leading' alarm nearby."""
    by_type: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for a in alarms:
        by_type[(a.asset_id, a.alarm_code)].append(a.start_time)
    keys = sorted(by_type)
    lag = timedelta(minutes=lag_minutes)

    def ref(key: tuple[str, str]) -> AlarmRef:
        asset_id, code = key
        return AlarmRef(
            asset_id=asset_id,
            asset_name=store.assets_by_id[asset_id].name,
            alarm_code=code,
            alarm_name=store.definitions_by_code[code].alarm_name,
            occurrences=len(by_type[key]),
        )

    pairs = []
    for i, x in enumerate(keys):
        for y in keys[i + 1 :]:
            x_to_y = _matches(by_type[x], by_type[y], lag)  # offsets of y relative to x
            y_to_x = _matches(by_type[y], by_type[x], lag)  # offsets of x relative to y
            if min(len(x_to_y), len(y_to_x)) < min_support:
                continue
            # median offset of y relative to x: negative means y usually comes first
            med = median(x_to_y)
            leader, follower, follower_hits = (y, x, len(x_to_y)) if med < 0 else (x, y, len(y_to_x))
            lead_min = round(abs(med), 1)
            relation = "simultaneous" if lead_min < 1 else "precedes"
            confidence = round(follower_hits / len(by_type[follower]), 2)
            lr, fr = ref(leader), ref(follower)
            verb = "fired together with" if relation == "simultaneous" else f"fired a median {lead_min} min before"
            insight = (
                f"{lr.asset_name} '{lr.alarm_name}' {verb} {fr.asset_name} '{fr.alarm_name}' "
                f"in {follower_hits} of {fr.occurrences} occurrences ({round(confidence * 100)}%)."
            )
            pairs.append(
                CorrelationPair(
                    leading=lr,
                    following=fr,
                    relation=relation,
                    cooccurrences=follower_hits,
                    confidence=confidence,
                    median_lead_minutes=lead_min,
                    insight=insight,
                )
            )
    pairs.sort(key=lambda p: (-p.cooccurrences, -p.confidence))
    return pairs[:20]


# --------------------------------------------------------------------- floods
def detect_floods(alarms: list[Alarm], threshold: int, window_minutes: int) -> list[FloodWindow]:
    """Slide a window over alarm start times. Any window holding MORE than `threshold` alarms is a
    flood (ISA-18.2: >10 alarms in 10 minutes). Overlapping flood windows merge into one episode."""
    times = [a.start_time for a in alarms]  # alarms arrive sorted by start_time
    window = timedelta(minutes=window_minutes)
    episodes: list[list[int]] = []  # [first_idx, last_idx, peak]
    j = 0
    for i in range(len(times)):
        j = max(j, i)
        while j + 1 < len(times) and times[j + 1] - times[i] < window:
            j += 1
        count = j - i + 1
        if count <= threshold:
            continue
        if episodes and i <= episodes[-1][1]:
            episodes[-1][1] = max(episodes[-1][1], j)
            episodes[-1][2] = max(episodes[-1][2], count)
        else:
            episodes.append([i, j, count])

    out = []
    for first, last, peak in episodes:
        items = alarms[first : last + 1]
        initiator = items[0]
        top = Counter((a.asset_name, a.alarm_name) for a in items).most_common(5)
        out.append(
            FloodWindow(
                start=items[0].start_time,
                end=items[-1].start_time + timedelta(seconds=1),
                duration_minutes=round((items[-1].start_time - items[0].start_time).total_seconds() / 60, 1),
                alarm_count=len(items),
                peak_count_in_window=peak,
                initiating_alarm=InitiatingAlarm(
                    alarm_id=initiator.alarm_id,
                    asset_id=initiator.asset_id,
                    asset_name=initiator.asset_name,
                    alarm_name=initiator.alarm_name,
                    start_time=initiator.start_time,
                ),
                top_contributors=[Contributor(asset_name=n, alarm_name=an, count=c) for (n, an), c in top],
            )
        )
    return out


# ------------------------------------------------------------ rationalization
SUGGESTED_ACTIONS = {
    "chattering": "Add deadband or on-delay; suppress only if eligible and approved under the alarm philosophy.",
    "stale": "Review the standing alarm: fix the cause, or shelve / take out of service with approval.",
    "recurring": "Investigate the root cause and review the setpoint against the operating envelope.",
    "unacknowledged": "Review relevance - operators routinely leave this alarm unacknowledged.",
}


def rationalization_candidates(
    store: DataStore, alarms: list[Alarm], recurrence: int, stale_minutes: int, chatter_seconds: int
) -> list[RationalizationCandidate]:
    """Group by asset + alarm code and flag groups that are recurring (>= threshold occurrences),
    chattering (>= 3 short re-alarms), stale (stood longer than the stale threshold) or mostly
    unacknowledged. Score 0-100 = recurrence 40 + chattering share 30 + stale 20 + unacked share 10."""
    groups: dict[tuple[str, str], list[Alarm]] = defaultdict(list)
    for a in alarms:
        groups[(a.asset_id, a.alarm_code)].append(a)

    now = store.reference_now
    out = []
    for (asset_id, code), items in groups.items():
        occ = len(items)
        chatter = sum(is_chattering(a, chatter_seconds) for a in items)
        stale = sum(is_stale(a, now, stale_minutes) for a in items)
        unacked = sum(not a.acknowledged for a in items)

        reasons = []
        if chatter >= 3:
            reasons.append("chattering")
        if stale:
            reasons.append("stale")
        if occ >= recurrence:
            reasons.append("recurring")
        if occ >= 2 and unacked / occ >= 0.5:
            reasons.append("unacknowledged")
        if not reasons:
            continue

        score = min(occ / recurrence, 3) / 3 * 40 + chatter / occ * 30 + (20 if stale else 0) + unacked / occ * 10
        definition = store.definitions_by_code[code]
        delays = [d for a in items if (d := ack_delay_minutes(a)) is not None]
        out.append(
            RationalizationCandidate(
                asset_id=asset_id,
                asset_name=items[0].asset_name,
                alarm_code=code,
                alarm_name=definition.alarm_name,
                alarm_type=definition.alarm_type,
                severity=max((a.severity for a in items), key=SEVERITY_RANK.__getitem__),
                occurrences=occ,
                chattering_count=chatter,
                stale_count=stale,
                unacknowledged_count=unacked,
                avg_ack_delay_minutes=round(mean(delays), 1) if delays else None,
                reasons=reasons,  # type: ignore[arg-type]
                score=round(score, 1),
                suppression_eligible=definition.alarm_type != "safety",
                suggested_action=SUGGESTED_ACTIONS[reasons[0]],
            )
        )
    out.sort(key=lambda c: (-c.score, c.asset_id, c.alarm_code))
    return out


# ------------------------------------------------------------------ priority
SEVERITY_POINTS = {"critical": 40, "high": 30, "medium": 18, "low": 8}
CRITICALITY_POINTS = {"high": 25, "medium": 15, "low": 5}
PRIORITY_BANDS = [(75, "P1", "Immediate action"), (55, "P2", "Urgent"), (35, "P3", "Planned"), (0, "P4", "Low")]


def priority_score(store: DataStore, alarm: Alarm) -> tuple[float, str, str, list[PriorityFactor]]:
    """Score 0-100 as a sum of five explainable factors, then map to a P1-P4 band."""
    asset = store.assets_by_id[alarm.asset_id]

    if alarm.status == "active":
        state_pts, state_txt = (8, "active, acknowledged") if alarm.acknowledged else (15, "active, NOT acknowledged")
    else:
        state_pts, state_txt = 0, "cleared"

    window_start = alarm.start_time - timedelta(days=30)
    repeats = sum(
        1
        for a in store.alarms
        if a.asset_id == alarm.asset_id
        and a.alarm_code == alarm.alarm_code
        and window_start <= a.start_time < alarm.start_time
    )

    nearby_ids = {alarm.asset_id, *asset.related_assets}
    concurrent = [
        a
        for a in store.alarms
        if a.alarm_id != alarm.alarm_id
        and a.asset_id in nearby_ids
        and a.start_time <= alarm.start_time
        and (a.end_time is None or a.end_time > alarm.start_time)
    ]

    factors = [
        PriorityFactor(
            name="severity",
            points=SEVERITY_POINTS[alarm.severity],
            max_points=40,
            explanation=f"severity is {alarm.severity}",
        ),
        PriorityFactor(
            name="asset_criticality",
            points=CRITICALITY_POINTS[asset.criticality],
            max_points=25,
            explanation=f"{asset.name} criticality is {asset.criticality}",
        ),
        PriorityFactor(name="state", points=state_pts, max_points=15, explanation=f"alarm is {state_txt}"),
        PriorityFactor(
            name="recurrence",
            points=round(min(repeats, 10) / 10 * 10, 1),
            max_points=10,
            explanation=f"{repeats} previous occurrences of this alarm on this asset in the prior 30 days",
        ),
        PriorityFactor(
            name="concurrent_alarms",
            points=min(len(concurrent), 2) * 5,
            max_points=10,
            explanation=f"{len(concurrent)} other alarm(s) active on this or related assets at the same time",
        ),
    ]
    score = round(sum(f.points for f in factors), 1)
    _, band, label = next(b for b in PRIORITY_BANDS if score >= b[0])
    return score, band, label, factors
