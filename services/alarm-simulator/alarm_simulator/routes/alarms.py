from datetime import datetime
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, Path, Query

from alarm_simulator.data import DataStore
from alarm_simulator.errors import ApiError
from alarm_simulator.models import AlarmStatus, AlarmType, Severity
from alarm_simulator.queries import (
    SortField,
    SortOrder,
    ack_delay_minutes,
    as_utc,
    duration_minutes,
    filter_alarms,
    paginate,
    sort_alarms,
)
from alarm_simulator.routes.deps import check_allowed, get_store, require_asset, split_csv
from alarm_simulator.schemas import AlarmDetail, AlarmListResponse, AssetSummary

router = APIRouter(tags=["alarms"])
Store = Annotated[DataStore, Depends(get_store)]


def check_time_range(start: datetime | None, end: datetime | None) -> None:
    if start and end and as_utc(start) >= as_utc(end):
        raise ApiError(422, "validation_error", "start_time must be before end_time.")


@router.get("/alarms", response_model=AlarmListResponse)
def list_alarms(
    store: Store,
    asset_id: Annotated[list[str] | None, Query(description="One or more asset ids (repeat or comma-separate)")] = None,
    site: str | None = None,
    unit: str | None = None,
    status: AlarmStatus | None = None,
    severity: Annotated[list[str] | None, Query()] = None,
    alarm_type: Annotated[list[str] | None, Query()] = None,
    alarm_code: Annotated[list[str] | None, Query()] = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    sort_by: SortField = "start_time",
    sort_order: SortOrder = "desc",
) -> AlarmListResponse:
    asset_ids = split_csv(asset_id)
    severities = split_csv(severity)
    alarm_types = split_csv(alarm_type)
    check_allowed("severity", severities, set(get_args(Severity)))
    check_allowed("alarm_type", alarm_types, set(get_args(AlarmType)))
    check_time_range(start_time, end_time)
    for a in asset_ids or []:
        require_asset(store, a)

    matches = filter_alarms(
        store,
        asset_ids=asset_ids,
        site=site,
        unit=unit,
        status=status,
        severities=severities,
        alarm_types=alarm_types,
        alarm_codes=split_csv(alarm_code),
        start=start_time,
        end=end_time,
    )
    page_items, pagination = paginate(sort_alarms(matches, sort_by, sort_order), page, page_size)

    applied = {
        "asset_id": asset_ids,
        "site": site,
        "unit": unit,
        "status": status,
        "severity": severities,
        "alarm_type": alarm_types,
        "start_time": as_utc(start_time).isoformat() if start_time else None,
        "end_time": as_utc(end_time).isoformat() if end_time else None,
        "sort": f"{sort_by} {sort_order}",
    }
    return AlarmListResponse(
        data=page_items,
        pagination=pagination,  # type: ignore[arg-type]
        filters={k: v for k, v in applied.items() if v},
    )


@router.get("/alarms/{alarm_id}", response_model=AlarmDetail)
def get_alarm(store: Store, alarm_id: Annotated[str, Path(max_length=32)]) -> AlarmDetail:
    alarm = store.alarms_by_id.get(alarm_id)
    if alarm is None:
        raise ApiError(404, "not_found", f"Alarm '{alarm_id}' not found.")
    return AlarmDetail(
        **alarm.model_dump(),
        duration_minutes=duration_minutes(alarm, store.reference_now),
        ack_delay_minutes=ack_delay_minutes(alarm),
        asset=AssetSummary.of(store.assets_by_id[alarm.asset_id]),
        definition=store.definitions_by_code[alarm.alarm_code],
    )
