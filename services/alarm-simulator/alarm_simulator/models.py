"""Typed domain records loaded from the seed data."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Severity = Literal["low", "medium", "high", "critical"]
AlarmType = Literal["process", "safety", "device"]
AlarmStatus = Literal["active", "cleared"]

SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class Site(BaseModel):
    site: str
    description: str
    units: list[str]


class Asset(BaseModel):
    asset_id: str
    name: str
    asset_type: str
    site: str
    unit: str
    criticality: Literal["low", "medium", "high"]
    manufacturer: str
    model: str
    install_date: date
    related_assets: list[str]


class AlarmDefinition(BaseModel):
    alarm_code: str
    alarm_name: str
    asset_type: str
    alarm_type: AlarmType
    severity: Severity
    uom: str | None
    setpoint: float | None
    direction: Literal["hi", "lo"] | None


class Recommendation(BaseModel):
    alarm_code: str
    likely_causes: list[str]
    actions: list[str]


class Alarm(BaseModel):
    alarm_id: str
    asset_id: str
    asset_name: str
    site: str
    unit: str
    alarm_code: str
    alarm_name: str
    alarm_type: AlarmType
    severity: Severity
    status: AlarmStatus
    acknowledged: bool
    start_time: datetime
    ack_time: datetime | None
    end_time: datetime | None
    value: float | None
    setpoint: float | None
    uom: str | None
    message: str
