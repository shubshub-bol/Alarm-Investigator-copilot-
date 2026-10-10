"""Request and response contracts for the analysis endpoints (POST /alarms/...)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alarm_simulator.models import AlarmType, Severity
from alarm_simulator.queries import as_utc

KpiName = Literal["alarm_count", "critical_count", "recurring_rate", "avg_ack_delay", "suppression_candidate_rate"]
GroupField = Literal["alarm_name", "alarm_code", "alarm_type", "asset_id", "asset_name", "severity", "site", "unit"]
KpiValues = dict[str, float | int | None]


class StrictModel(BaseModel):
    """Reject unknown fields so typos (e.g. 'asset_idz') fail loudly instead of being ignored."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ requests
class TimeRange(StrictModel):
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def _normalise(self) -> "TimeRange":
        self.start_time, self.end_time = as_utc(self.start_time), as_utc(self.end_time)
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class ScopedRequest(StrictModel):
    """Which alarms to analyse. Omitted time_range = the last 90 days."""

    asset_ids: list[str] | None = Field(None, min_length=1, max_length=50)
    site: str | None = Field(None, max_length=64)
    unit: str | None = Field(None, max_length=64)
    time_range: TimeRange | None = None


class SummaryRequest(ScopedRequest):
    severity: list[Severity] | None = None
    alarm_types: list[AlarmType] | None = None
    group_by: list[GroupField] = Field(default_factory=list, max_length=4)
    kpis: list[KpiName] = Field(default_factory=lambda: ["alarm_count"], min_length=1)


class TrendsRequest(ScopedRequest):
    severity: list[Severity] | None = None
    alarm_codes: list[str] | None = Field(None, max_length=20, description="e.g. only PMP-VIB-HI")
    bucket: Literal["hourly", "daily", "weekly"] = "daily"
    metrics: list[KpiName] = Field(default_factory=lambda: ["alarm_count"], min_length=1)


class CorrelationRequest(ScopedRequest):
    asset_ids: list[str] = Field(min_length=1, max_length=20)  # type: ignore[assignment]  # required here
    correlation_method: Literal["cooccurrence"] = "cooccurrence"
    lag_window_minutes: int = Field(15, ge=1, le=240)
    severity_threshold: Severity = "low"
    min_support: int = Field(1, ge=1, le=100)


class FloodRequest(ScopedRequest):
    threshold_count: int = Field(10, ge=2, le=1000, description="Flood = MORE than this many alarms in the window")
    rolling_window_minutes: int = Field(10, ge=1, le=1440)


class RationalizationRequest(ScopedRequest):
    recurrence_threshold: int = Field(5, ge=2, le=1000)
    stale_minutes_threshold: int = Field(1440, ge=1)
    chatter_seconds_threshold: int = Field(120, ge=1, le=3600)


class PriorityRequest(StrictModel):
    alarm_id: str = Field(min_length=1, max_length=32)


# ----------------------------------------------------------------- responses
class ResolvedTimeRange(BaseModel):
    start_time: datetime
    end_time: datetime


class SummaryGroup(BaseModel):
    key: dict[str, str]
    kpis: KpiValues


class SummaryResponse(BaseModel):
    time_range: ResolvedTimeRange
    totals: KpiValues
    groups: list[SummaryGroup]


class TrendPoint(BaseModel):
    bucket_start: datetime
    values: KpiValues


class TrendSummary(BaseModel):
    direction: Literal["increasing", "decreasing", "stable"]
    first_half_avg: float
    second_half_avg: float
    change_percent: float | None


class TrendsResponse(BaseModel):
    time_range: ResolvedTimeRange
    bucket: str
    series: list[TrendPoint]
    trend: TrendSummary


class AlarmRef(BaseModel):
    asset_id: str
    asset_name: str
    alarm_code: str
    alarm_name: str
    occurrences: int


class CorrelationPair(BaseModel):
    leading: AlarmRef
    following: AlarmRef
    relation: Literal["precedes", "simultaneous"]
    cooccurrences: int = Field(description="Occurrences of 'following' with 'leading' inside the lag window")
    confidence: float = Field(description="cooccurrences / occurrences of 'following'")
    median_lead_minutes: float
    insight: str


class CorrelationResponse(BaseModel):
    time_range: ResolvedTimeRange
    method: str
    lag_window_minutes: int
    assets_analyzed: list[str]
    events_analyzed: int
    pairs: list[CorrelationPair]


class InitiatingAlarm(BaseModel):
    alarm_id: str
    asset_id: str
    asset_name: str
    alarm_name: str
    start_time: datetime


class Contributor(BaseModel):
    asset_name: str
    alarm_name: str
    count: int


class FloodWindow(BaseModel):
    start: datetime
    end: datetime = Field(description="Exclusive: use directly as end_time on GET /alarms")
    duration_minutes: float
    alarm_count: int
    peak_count_in_window: int
    initiating_alarm: InitiatingAlarm
    top_contributors: list[Contributor]


class FloodResponse(BaseModel):
    time_range: ResolvedTimeRange
    threshold_count: int
    rolling_window_minutes: int
    total_alarms: int
    alarms_in_floods: int
    time_in_flood_percent: float
    flood_windows: list[FloodWindow]


class RationalizationCandidate(BaseModel):
    asset_id: str
    asset_name: str
    alarm_code: str
    alarm_name: str
    alarm_type: AlarmType
    severity: Severity
    occurrences: int
    chattering_count: int
    stale_count: int
    unacknowledged_count: int
    avg_ack_delay_minutes: float | None
    reasons: list[Literal["recurring", "chattering", "stale", "unacknowledged"]]
    score: float
    suppression_eligible: bool = Field(description="Safety alarms are never eligible for suppression")
    suggested_action: str


class RationalizationResponse(BaseModel):
    time_range: ResolvedTimeRange
    criteria: dict[str, int]
    total_alarms: int
    candidates: list[RationalizationCandidate]


class PriorityFactor(BaseModel):
    name: str
    points: float
    max_points: float
    explanation: str


class PriorityAlarm(BaseModel):
    alarm_id: str
    asset_id: str
    asset_name: str
    alarm_name: str
    severity: Severity
    status: str
    start_time: datetime


class PriorityResponse(BaseModel):
    alarm_id: str
    score: float
    priority: Literal["P1", "P2", "P3", "P4"]
    priority_label: str
    factors: list[PriorityFactor]
    alarm: PriorityAlarm
