"""Response contracts shared by the routes."""

from datetime import datetime

from pydantic import BaseModel

from alarm_simulator.models import Alarm, AlarmDefinition, Asset


class AssetSummary(BaseModel):
    asset_id: str
    name: str
    asset_type: str
    site: str
    unit: str
    criticality: str

    @classmethod
    def of(cls, asset: Asset) -> "AssetSummary":
        return cls(**asset.model_dump(include=set(cls.model_fields)))


class AssetSearchHit(AssetSummary):
    match_score: float


class AssetSearchResponse(BaseModel):
    query: str
    total: int
    results: list[AssetSearchHit]


class AlarmStatistics(BaseModel):
    total_alarms: int
    active_alarms: int
    by_severity: dict[str, int]
    first_alarm_at: datetime | None
    last_alarm_at: datetime | None


class AssetMetadata(Asset):
    related_assets: list[AssetSummary]  # type: ignore[assignment]  # expanded from ids to summaries
    alarm_codes: list[str]
    alarm_statistics: AlarmStatistics


class Pagination(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool


class AlarmListResponse(BaseModel):
    data: list[Alarm]
    pagination: Pagination
    filters: dict[str, str | list[str]]


class AlarmDetail(Alarm):
    duration_minutes: float
    ack_delay_minutes: float | None
    asset: AssetSummary
    definition: AlarmDefinition
