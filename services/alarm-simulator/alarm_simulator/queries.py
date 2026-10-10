"""Query logic over the DataStore. No HTTP here, so it is easy to unit test and reuse across routes."""

import math
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from alarm_simulator.data import DataStore
from alarm_simulator.models import SEVERITY_RANK, Alarm, Asset

_WORD = re.compile(r"[a-z0-9]+")

SortField = Literal["start_time", "severity", "asset_id", "alarm_id"]
SortOrder = Literal["asc", "desc"]


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def as_utc(dt: datetime) -> datetime:
    """Treat timezone-less input as UTC so comparisons never mix naive and aware datetimes."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def minutes_between(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 60, 1)


# ------------------------------------------------------------------ asset search
def search_assets(
    store: DataStore,
    query: str,
    *,
    site: str | None = None,
    unit: str | None = None,
    asset_type: str | None = None,
) -> list[tuple[Asset, float]]:
    """Rank assets against a free-text query.

    Score 1.0 = exact name or id match. Otherwise 0.9 x (share of query words found in the
    asset's name / id / type / site / unit). Assets matching under half the words are dropped.
    """
    q = query.strip().lower()
    q_words = words(q)
    hits: list[tuple[Asset, float]] = []
    for asset in store.assets:
        if site and asset.site.lower() != site.lower():
            continue
        if unit and asset.unit.lower() != unit.lower():
            continue
        if asset_type and asset.asset_type.lower() != asset_type.lower():
            continue

        if q in (asset.name.lower(), asset.asset_id.lower()):
            score = 1.0
        else:
            haystack = set(words(f"{asset.name} {asset.asset_id} {asset.asset_type} {asset.site} {asset.unit}"))
            matched = sum(w in haystack for w in q_words)
            if not q_words or matched / len(q_words) < 0.5:
                continue
            score = round(0.9 * matched / len(q_words), 2)
        hits.append((asset, score))

    hits.sort(key=lambda h: (-h[1], h[0].asset_id))
    return hits


# ------------------------------------------------------------------ alarm filters
def filter_alarms(
    store: DataStore,
    *,
    asset_ids: Iterable[str] | None = None,
    site: str | None = None,
    unit: str | None = None,
    status: str | None = None,
    severities: Iterable[str] | None = None,
    alarm_types: Iterable[str] | None = None,
    alarm_codes: Iterable[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Alarm]:
    """Alarms matching every given filter. Time window is on start_time: start <= t < end."""
    asset_set = set(asset_ids) if asset_ids else None
    severity_set = set(severities) if severities else None
    type_set = set(alarm_types) if alarm_types else None
    code_set = set(alarm_codes) if alarm_codes else None
    start = as_utc(start) if start else None
    end = as_utc(end) if end else None

    out = []
    for a in store.alarms:
        if asset_set is not None and a.asset_id not in asset_set:
            continue
        if site and a.site.lower() != site.lower():
            continue
        if unit and a.unit.lower() != unit.lower():
            continue
        if status and a.status != status:
            continue
        if severity_set is not None and a.severity not in severity_set:
            continue
        if type_set is not None and a.alarm_type not in type_set:
            continue
        if code_set is not None and a.alarm_code not in code_set:
            continue
        if start and a.start_time < start:
            continue
        if end and a.start_time >= end:
            continue
        out.append(a)
    return out


def sort_alarms(alarms: list[Alarm], sort_by: SortField, order: SortOrder) -> list[Alarm]:
    if sort_by == "severity":
        key = lambda a: (SEVERITY_RANK[a.severity], a.start_time)  # noqa: E731
    else:
        key = lambda a: getattr(a, sort_by)  # noqa: E731
    return sorted(alarms, key=key, reverse=(order == "desc"))


def paginate(items: list, page: int, page_size: int) -> tuple[list, dict]:
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    return items[start : start + page_size], {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
    }


# ------------------------------------------------------------------ per-alarm metrics
def ack_delay_minutes(alarm: Alarm) -> float | None:
    return minutes_between(alarm.start_time, alarm.ack_time) if alarm.ack_time else None


def duration_minutes(alarm: Alarm, now: datetime) -> float:
    """Cleared alarms: start->end. Active alarms: start->simulator 'now'."""
    return minutes_between(alarm.start_time, alarm.end_time or now)


def alarm_statistics(alarms: list[Alarm]) -> dict:
    return {
        "total_alarms": len(alarms),
        "active_alarms": sum(a.status == "active" for a in alarms),
        "by_severity": dict(Counter(a.severity for a in alarms)),
        "first_alarm_at": alarms[0].start_time if alarms else None,
        "last_alarm_at": alarms[-1].start_time if alarms else None,
    }
