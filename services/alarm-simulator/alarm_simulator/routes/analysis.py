from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from alarm_simulator import analysis
from alarm_simulator.analysis_schemas import (
    CorrelationRequest,
    CorrelationResponse,
    FloodRequest,
    FloodResponse,
    PriorityAlarm,
    PriorityRequest,
    PriorityResponse,
    RationalizationRequest,
    RationalizationResponse,
    ResolvedTimeRange,
    ScopedRequest,
    SummaryRequest,
    SummaryResponse,
    TrendsRequest,
    TrendsResponse,
)
from alarm_simulator.data import DataStore
from alarm_simulator.errors import ApiError
from alarm_simulator.models import SEVERITY_RANK, Alarm
from alarm_simulator.queries import filter_alarms
from alarm_simulator.routes.deps import get_store, require_asset

router = APIRouter(tags=["analysis"])
Store = Annotated[DataStore, Depends(get_store)]
DEFAULT_LOOKBACK = timedelta(days=90)


def scoped_alarms(store: DataStore, req: ScopedRequest, **extra) -> tuple[list[Alarm], datetime, datetime]:
    """Validate asset ids, resolve the time range (default: last 90 days) and filter alarms."""
    for asset_id in req.asset_ids or []:
        require_asset(store, asset_id)
    if req.time_range:
        start, end = req.time_range.start_time, req.time_range.end_time
    else:
        start, end = store.reference_now - DEFAULT_LOOKBACK, store.reference_now
    alarms = filter_alarms(store, asset_ids=req.asset_ids, site=req.site, unit=req.unit, start=start, end=end, **extra)
    return alarms, start, end


@router.post("/alarms/summary", response_model=SummaryResponse)
def summary(store: Store, req: SummaryRequest) -> SummaryResponse:
    alarms, start, end = scoped_alarms(store, req, severities=req.severity, alarm_types=req.alarm_types)
    totals, groups = analysis.summarize(alarms, list(req.group_by), list(req.kpis), store.reference_now)
    return SummaryResponse(time_range=ResolvedTimeRange(start_time=start, end_time=end), totals=totals, groups=groups)


@router.post("/alarms/trends", response_model=TrendsResponse)
def trends(store: Store, req: TrendsRequest) -> TrendsResponse:
    alarms, start, end = scoped_alarms(store, req, severities=req.severity, alarm_codes=req.alarm_codes)
    if analysis.bucket_count(start, end, req.bucket) > analysis.MAX_BUCKETS:
        raise ApiError(
            422, "validation_error", f"Too many '{req.bucket}' buckets; use a larger bucket or a shorter time range."
        )
    series, trend = analysis.trends(alarms, start, end, req.bucket, list(req.metrics), store.reference_now)
    return TrendsResponse(
        time_range=ResolvedTimeRange(start_time=start, end_time=end), bucket=req.bucket, series=series, trend=trend
    )


@router.post("/alarms/correlation", response_model=CorrelationResponse)
def correlation(store: Store, req: CorrelationRequest) -> CorrelationResponse:
    alarms, start, end = scoped_alarms(store, req)
    floor = SEVERITY_RANK[req.severity_threshold]
    alarms = [a for a in alarms if SEVERITY_RANK[a.severity] >= floor]
    return CorrelationResponse(
        time_range=ResolvedTimeRange(start_time=start, end_time=end),
        method=req.correlation_method,
        lag_window_minutes=req.lag_window_minutes,
        assets_analyzed=req.asset_ids,
        events_analyzed=len(alarms),
        pairs=analysis.correlate(store, alarms, req.lag_window_minutes, req.min_support),
    )


@router.post("/alarms/flood-analysis", response_model=FloodResponse)
def flood_analysis(store: Store, req: FloodRequest) -> FloodResponse:
    alarms, start, end = scoped_alarms(store, req)
    windows = analysis.detect_floods(store, alarms, req.threshold_count, req.rolling_window_minutes)
    flood_minutes = sum(w.duration_minutes for w in windows)
    return FloodResponse(
        time_range=ResolvedTimeRange(start_time=start, end_time=end),
        threshold_count=req.threshold_count,
        rolling_window_minutes=req.rolling_window_minutes,
        total_alarms=len(alarms),
        alarms_in_floods=sum(w.alarm_count for w in windows),
        time_in_flood_percent=round(flood_minutes / ((end - start).total_seconds() / 60) * 100, 3),
        flood_windows=windows,
    )


@router.post("/alarms/rationalization-candidates", response_model=RationalizationResponse)
def rationalization_candidates(store: Store, req: RationalizationRequest) -> RationalizationResponse:
    alarms, start, end = scoped_alarms(store, req)
    return RationalizationResponse(
        time_range=ResolvedTimeRange(start_time=start, end_time=end),
        criteria={
            "recurrence_threshold": req.recurrence_threshold,
            "stale_minutes_threshold": req.stale_minutes_threshold,
            "chatter_seconds_threshold": req.chatter_seconds_threshold,
        },
        total_alarms=len(alarms),
        candidates=analysis.rationalization_candidates(
            store, alarms, req.recurrence_threshold, req.stale_minutes_threshold, req.chatter_seconds_threshold
        ),
    )


@router.post("/alarms/priority-score", response_model=PriorityResponse)
def priority_score(store: Store, req: PriorityRequest) -> PriorityResponse:
    alarm = store.alarms_by_id.get(req.alarm_id)
    if alarm is None:
        raise ApiError(404, "not_found", f"Alarm '{req.alarm_id}' not found.")
    score, band, label, factors = analysis.priority_score(store, alarm)
    return PriorityResponse(
        alarm_id=alarm.alarm_id,
        score=score,
        priority=band,  # type: ignore[arg-type]
        priority_label=label,
        factors=factors,
        alarm=PriorityAlarm(**alarm.model_dump(include=set(PriorityAlarm.model_fields))),
    )
