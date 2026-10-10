"""Operator recommendations: catalogue guidance plus optional context around the alarm."""

from datetime import timedelta
from statistics import mean

from alarm_simulator import analysis
from alarm_simulator.analysis_schemas import (
    ActionStep,
    AssetContext,
    HistoricalPattern,
    PriorityAlarm,
    PrioritySnapshot,
    RecommendationRequest,
    RecommendationResponse,
    RelatedAssetStatus,
)
from alarm_simulator.data import DataStore
from alarm_simulator.models import Alarm
from alarm_simulator.queries import ack_delay_minutes, duration_minutes

SOURCE = "alarm-response-catalog"


def _as_priority_alarm(a: Alarm) -> PriorityAlarm:
    return PriorityAlarm(**a.model_dump(include=set(PriorityAlarm.model_fields)))


def _related_assets(store: DataStore, alarm: Alarm) -> list[RelatedAssetStatus]:
    asset = store.assets_by_id[alarm.asset_id]
    since = alarm.start_time - timedelta(days=7)
    out = []
    for rid in asset.related_assets:
        related = store.assets_by_id.get(rid)
        if related is None:
            continue
        own = [a for a in store.alarms if a.asset_id == rid]
        out.append(
            RelatedAssetStatus(
                asset_id=rid,
                name=related.name,
                asset_type=related.asset_type,
                active_alarms=[_as_priority_alarm(a) for a in own if a.status == "active"],
                alarms_prior_7_days=sum(since <= a.start_time <= alarm.start_time for a in own),
            )
        )
    return out


def _asset_context(store: DataStore, alarm: Alarm) -> AssetContext:
    a = store.assets_by_id[alarm.asset_id]
    return AssetContext(**a.model_dump(exclude={"related_assets", "install_date"}), install_date=str(a.install_date))


def _history(store: DataStore, alarm: Alarm) -> HistoricalPattern:
    """Same asset + alarm code in the 90 days before this alarm started."""
    start = alarm.start_time - timedelta(days=90)
    prior = [
        a
        for a in store.alarms
        if a.asset_id == alarm.asset_id
        and a.alarm_code == alarm.alarm_code
        and start <= a.start_time < alarm.start_time
    ]
    durations = [duration_minutes(a, store.reference_now) for a in prior]
    delays = [d for a in prior if (d := ack_delay_minutes(a)) is not None]
    _, trend = analysis.trends(prior, start, alarm.start_time, "weekly", ["alarm_count"], store.reference_now)
    return HistoricalPattern(
        occurrences_prior_30_days=sum(a.start_time >= alarm.start_time - timedelta(days=30) for a in prior),
        occurrences_prior_90_days=len(prior),
        last_previous_occurrence=prior[-1].start_time if prior else None,
        avg_duration_minutes=round(mean(durations), 1) if durations else None,
        avg_ack_delay_minutes=round(mean(delays), 1) if delays else None,
        trend_direction=trend.direction,
    )


def recommend(store: DataStore, alarm: Alarm, req: RecommendationRequest) -> RecommendationResponse:
    rec = store.recommendations_by_code.get(alarm.alarm_code)
    score, band, label, _ = analysis.priority_score(store, alarm)
    return RecommendationResponse(
        alarm_id=alarm.alarm_id,
        alarm=_as_priority_alarm(alarm),
        priority=PrioritySnapshot(score=score, priority=band, priority_label=label),  # type: ignore[arg-type]
        likely_causes=rec.likely_causes if rec else [],
        recommended_actions=[ActionStep(step=i, action=t) for i, t in enumerate(rec.actions if rec else [], 1)],
        source=SOURCE,
        related_assets=_related_assets(store, alarm) if req.include_related else None,
        asset_context=_asset_context(store, alarm) if req.include_asset_context else None,
        historical_pattern=_history(store, alarm) if req.include_historical_pattern else None,
    )
