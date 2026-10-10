"""Tool output contracts. Every API response is validated against these before it reaches the model.

These are deliberately the MCP server's own models (not imported from the simulator): the server
depends on the API's documented contract, not on its code.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]


# --------------------------------------------------------------- trace metadata
class ApiCall(BaseModel):
    method: str | None
    path: str | None
    status_code: int | None
    attempts: int = Field(description="1 = first try succeeded; >1 means retries happened")
    duration_ms: float | None
    outcome: Literal["ok", "error"]
    error_code: str | None = None


class ToolMeta(BaseModel):
    trace_id: str
    api_calls: list[ApiCall]


class ToolOutput(BaseModel):
    meta: ToolMeta = Field(description="Trace id and the Alarm API calls made for this tool call")


class TimeRange(BaseModel):
    start_time: datetime
    end_time: datetime


# ----------------------------------------------------------------------- assets
class AssetSummary(BaseModel):
    asset_id: str
    name: str
    asset_type: str
    site: str
    unit: str
    criticality: str


class AssetHit(AssetSummary):
    match_score: float


class SearchAssetsOutput(ToolOutput):
    query: str
    total: int
    results: list[AssetHit]


class AlarmStatistics(BaseModel):
    total_alarms: int
    active_alarms: int
    by_severity: dict[str, int]
    first_alarm_at: datetime | None
    last_alarm_at: datetime | None


class AssetMetadataOutput(ToolOutput, AssetSummary):
    manufacturer: str
    model: str
    install_date: str
    related_assets: list[AssetSummary]
    alarm_codes: list[str]
    alarm_statistics: AlarmStatistics


# ----------------------------------------------------------------------- alarms
class AlarmRecord(BaseModel):
    alarm_id: str
    asset_id: str
    asset_name: str
    site: str
    unit: str
    alarm_code: str
    alarm_name: str
    alarm_type: str
    severity: Severity
    status: Literal["active", "cleared"]
    acknowledged: bool
    start_time: datetime
    ack_time: datetime | None
    end_time: datetime | None
    value: float | None
    setpoint: float | None
    uom: str | None
    message: str


class AlarmListOutput(ToolOutput):
    alarms: list[AlarmRecord]
    total_items: int | None
    page: int | None = Field(description="Page returned; null when fetch_all was used")
    has_next: bool
    pages_fetched: int
    truncated: bool = Field(description="True if more alarms matched than were returned")


class AlarmDetailOutput(ToolOutput, AlarmRecord):
    duration_minutes: float
    ack_delay_minutes: float | None
    asset: AssetSummary


# --------------------------------------------------------------------- analysis
KpiValues = dict[str, float | int | None]


class SummaryGroup(BaseModel):
    key: dict[str, str]
    kpis: KpiValues


class SummaryOutput(ToolOutput):
    time_range: TimeRange
    totals: KpiValues
    groups: list[SummaryGroup]


class TrendPoint(BaseModel):
    bucket_start: datetime
    values: KpiValues


class TrendInfo(BaseModel):
    direction: Literal["increasing", "decreasing", "stable"]
    first_half_avg: float
    second_half_avg: float
    change_percent: float | None


class TrendsOutput(ToolOutput):
    time_range: TimeRange
    bucket: str
    series: list[TrendPoint]
    trend: TrendInfo


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
    cooccurrences: int
    confidence: float
    median_lead_minutes: float
    insight: str


class CorrelationOutput(ToolOutput):
    time_range: TimeRange
    lag_window_minutes: int
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
    end: datetime
    duration_minutes: float
    alarm_count: int
    peak_count_in_window: int
    initiating_alarm: InitiatingAlarm
    top_contributors: list[Contributor]


class FloodOutput(ToolOutput):
    time_range: TimeRange
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
    alarm_type: str
    severity: Severity
    occurrences: int
    chattering_count: int
    stale_count: int
    unacknowledged_count: int
    avg_ack_delay_minutes: float | None
    reasons: list[str]
    score: float
    suppression_eligible: bool
    suggested_action: str


class RationalizationOutput(ToolOutput):
    time_range: TimeRange
    criteria: dict[str, int]
    total_alarms: int
    candidates: list[RationalizationCandidate]


# ------------------------------------------------------- priority & recommendations
class AlarmBrief(BaseModel):
    alarm_id: str
    asset_id: str
    asset_name: str
    alarm_name: str
    severity: Severity
    status: str
    start_time: datetime


class PriorityFactor(BaseModel):
    name: str
    points: float
    max_points: float
    explanation: str


class PriorityOutput(ToolOutput):
    alarm_id: str
    score: float
    priority: Literal["P1", "P2", "P3", "P4"]
    priority_label: str
    factors: list[PriorityFactor]
    alarm: AlarmBrief


class PrioritySnapshot(BaseModel):
    score: float
    priority: Literal["P1", "P2", "P3", "P4"]
    priority_label: str


class ActionStep(BaseModel):
    step: int
    action: str


class RelatedAssetStatus(BaseModel):
    asset_id: str
    name: str
    asset_type: str
    active_alarms: list[AlarmBrief]
    alarms_prior_7_days: int


class AssetContext(BaseModel):
    asset_id: str
    name: str
    asset_type: str
    site: str
    unit: str
    criticality: str
    manufacturer: str
    model: str
    install_date: str


class HistoricalPattern(BaseModel):
    occurrences_prior_30_days: int
    occurrences_prior_90_days: int
    last_previous_occurrence: datetime | None
    avg_duration_minutes: float | None
    avg_ack_delay_minutes: float | None
    trend_direction: Literal["increasing", "decreasing", "stable"]


class RecommendationOutput(ToolOutput):
    alarm_id: str
    alarm: AlarmBrief
    priority: PrioritySnapshot
    likely_causes: list[str]
    recommended_actions: list[ActionStep]
    source: str = Field(description="Origin of the guidance. Must be checked against site procedures.")
    related_assets: list[RelatedAssetStatus] | None
    asset_context: AssetContext | None
    historical_pattern: HistoricalPattern | None


# ------------------------------------------------------------------------ KPIs
class KpiOutput(ToolOutput):
    calculation_id: str
    calculation_type: str
    description: str
    value: float
    unit: str
    interpretation: str
    details: dict[str, float | int | str | None]
    filters: dict[str, str | list[str] | None]


class KpiDefinition(BaseModel):
    name: str
    unit: str
    description: str


class KpiDefinitionsOutput(ToolOutput):
    kpis: list[KpiDefinition]
    calculation_types: list[KpiDefinition]
