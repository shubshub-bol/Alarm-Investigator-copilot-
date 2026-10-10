"""Generate deterministic synthetic seed data for the Alarm Management API simulator.

The data is fake but shaped to look like a real plant alarm log. A few patterns are
planted on purpose so the copilot has something real to discover:

  1. Boiler Feed Pump 101 - recurring high vibration, usually preceded by a hot
     bearing on its drive motor (Motor M-101).  <- mandatory acceptance scenario
  2. Boiler Feed Pump 102 - active critical alarms right now.
  3. Unit 2 - one alarm flood (>10 alarms in 10 minutes) after a boiler flame failure.
  4. Compressor C-301 - recurring/chattering high discharge pressure, preceded by a
     hot discharge cooler (E-301).
  5. Motor M-501 (Unit 5) - motor trips preceded by high winding temperature and
     compressor surge on C-502.
  6. A few stale alarms (active for more than 24 hours).

Usage:
    python generate_seed.py                      # writes ../data/seed_data.json
    python generate_seed.py --out x.json --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The simulator treats WINDOW_END as "now", so "last 90 days" always has data.
WINDOW_START = datetime(2026, 4, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 7, 1, tzinfo=timezone.utc)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "seed_data.json"

SITES = [
    {"site": "NorthPlant", "description": "Coal-fired power station", "units": ["Unit 1", "Unit 2"]},
    {"site": "EastRefinery", "description": "Crude oil refinery", "units": ["Unit 3", "Unit 4", "Unit 5"]},
    {"site": "SouthPlant", "description": "Combined-cycle power station", "units": ["Unit 6"]},
]

# --------------------------------------------------------------------------- assets
ASSETS = [
    # NorthPlant / Unit 1 - feedwater system
    {"asset_id": "AST-BFP-101", "name": "Boiler Feed Pump 101", "asset_type": "pump", "site": "NorthPlant", "unit": "Unit 1",
     "criticality": "high", "manufacturer": "Sulzer", "model": "HPT-200", "install_date": "2014-03-12",
     "related_assets": ["AST-MTR-101", "AST-DEA-101", "AST-FV-101", "AST-BFP-102"]},
    {"asset_id": "AST-BFP-102", "name": "Boiler Feed Pump 102", "asset_type": "pump", "site": "NorthPlant", "unit": "Unit 1",
     "criticality": "high", "manufacturer": "Sulzer", "model": "HPT-200", "install_date": "2014-03-12",
     "related_assets": ["AST-MTR-102", "AST-DEA-101", "AST-FV-101", "AST-BFP-101"]},
    {"asset_id": "AST-MTR-101", "name": "Motor M-101 (BFP 101 Drive)", "asset_type": "motor", "site": "NorthPlant", "unit": "Unit 1",
     "criticality": "high", "manufacturer": "ABB", "model": "AMA-500", "install_date": "2014-03-12",
     "related_assets": ["AST-BFP-101"]},
    {"asset_id": "AST-MTR-102", "name": "Motor M-102 (BFP 102 Drive)", "asset_type": "motor", "site": "NorthPlant", "unit": "Unit 1",
     "criticality": "high", "manufacturer": "ABB", "model": "AMA-500", "install_date": "2014-03-12",
     "related_assets": ["AST-BFP-102"]},
    {"asset_id": "AST-DEA-101", "name": "Deaerator D-101", "asset_type": "deaerator", "site": "NorthPlant", "unit": "Unit 1",
     "criticality": "medium", "manufacturer": "Stork", "model": "DA-80", "install_date": "2013-11-02",
     "related_assets": ["AST-BFP-101", "AST-BFP-102"]},
    {"asset_id": "AST-FV-101", "name": "Feedwater Control Valve FV-101", "asset_type": "valve", "site": "NorthPlant", "unit": "Unit 1",
     "criticality": "medium", "manufacturer": "Fisher", "model": "EWT-8", "install_date": "2016-06-20",
     "related_assets": ["AST-BFP-101", "AST-BFP-102"]},
    # NorthPlant / Unit 2 - boiler
    {"asset_id": "AST-BLR-201", "name": "Boiler B-201", "asset_type": "boiler", "site": "NorthPlant", "unit": "Unit 2",
     "criticality": "high", "manufacturer": "Babcock", "model": "SB-600", "install_date": "2012-09-01",
     "related_assets": ["AST-FAN-201", "AST-PMP-202"]},
    {"asset_id": "AST-FAN-201", "name": "Forced Draft Fan F-201", "asset_type": "fan", "site": "NorthPlant", "unit": "Unit 2",
     "criticality": "medium", "manufacturer": "Howden", "model": "FD-90", "install_date": "2012-09-01",
     "related_assets": ["AST-BLR-201"]},
    {"asset_id": "AST-PMP-202", "name": "Condensate Pump 202", "asset_type": "pump", "site": "NorthPlant", "unit": "Unit 2",
     "criticality": "medium", "manufacturer": "KSB", "model": "WKTB-150", "install_date": "2015-02-14",
     "related_assets": ["AST-BLR-201"]},
    # EastRefinery / Unit 3 - gas compression
    {"asset_id": "AST-CMP-301", "name": "Gas Compressor C-301", "asset_type": "compressor", "site": "EastRefinery", "unit": "Unit 3",
     "criticality": "high", "manufacturer": "Atlas Copco", "model": "GT-040", "install_date": "2017-04-08",
     "related_assets": ["AST-HX-301"]},
    {"asset_id": "AST-HX-301", "name": "Discharge Cooler E-301", "asset_type": "heat_exchanger", "site": "EastRefinery", "unit": "Unit 3",
     "criticality": "medium", "manufacturer": "Alfa Laval", "model": "AC-12", "install_date": "2017-04-08",
     "related_assets": ["AST-CMP-301"]},
    # EastRefinery / Unit 4 - crude heating
    {"asset_id": "AST-PMP-401", "name": "Crude Charge Pump 401", "asset_type": "pump", "site": "EastRefinery", "unit": "Unit 4",
     "criticality": "high", "manufacturer": "Flowserve", "model": "DVSH-10", "install_date": "2011-07-19",
     "related_assets": ["AST-HTR-401"]},
    {"asset_id": "AST-HTR-401", "name": "Fired Heater H-401", "asset_type": "fired_heater", "site": "EastRefinery", "unit": "Unit 4",
     "criticality": "high", "manufacturer": "Foster Wheeler", "model": "CH-30", "install_date": "2011-07-19",
     "related_assets": ["AST-PMP-401"]},
    # EastRefinery / Unit 5 - recycle compression
    {"asset_id": "AST-MTR-501", "name": "Motor M-501 (C-502 Drive)", "asset_type": "motor", "site": "EastRefinery", "unit": "Unit 5",
     "criticality": "high", "manufacturer": "Siemens", "model": "H-compact", "install_date": "2018-10-03",
     "related_assets": ["AST-CMP-502", "AST-MTR-502"]},
    {"asset_id": "AST-CMP-502", "name": "Recycle Compressor C-502", "asset_type": "compressor", "site": "EastRefinery", "unit": "Unit 5",
     "criticality": "high", "manufacturer": "Elliott", "model": "EDGE-25", "install_date": "2018-10-03",
     "related_assets": ["AST-MTR-501"]},
    {"asset_id": "AST-MTR-502", "name": "Motor M-502 (Cooling Water Pump)", "asset_type": "motor", "site": "EastRefinery", "unit": "Unit 5",
     "criticality": "medium", "manufacturer": "WEG", "model": "W22", "install_date": "2019-01-22",
     "related_assets": ["AST-MTR-501"]},
    # SouthPlant / Unit 6 - cooling water (background data only; used by the KPI/trend chains)
    {"asset_id": "AST-PMP-601", "name": "Cooling Water Pump 601", "asset_type": "pump", "site": "SouthPlant", "unit": "Unit 6",
     "criticality": "medium", "manufacturer": "Grundfos", "model": "NK-300", "install_date": "2020-05-11",
     "related_assets": ["AST-MTR-601"]},
    {"asset_id": "AST-MTR-601", "name": "Motor M-601 (CW Pump 601 Drive)", "asset_type": "motor", "site": "SouthPlant", "unit": "Unit 6",
     "criticality": "medium", "manufacturer": "WEG", "model": "W22", "install_date": "2020-05-11",
     "related_assets": ["AST-PMP-601"]},
    {"asset_id": "AST-CMP-601", "name": "Instrument Air Compressor C-601", "asset_type": "compressor", "site": "SouthPlant", "unit": "Unit 6",
     "criticality": "medium", "manufacturer": "Kaeser", "model": "DSD-145", "install_date": "2021-02-03",
     "related_assets": []},
]

# ------------------------------------------------------------------ alarm definitions
# alarm_type: "process" (process variable out of range), "safety" (trip / protective action),
#             "device" (equipment health, e.g. vibration, bearings, seals, valve position)
# direction:  "hi" = value above setpoint, "lo" = below setpoint, None = discrete event
ALARM_DEFINITIONS = {
    # pumps
    "PMP-VIB-HI": {"alarm_name": "High Vibration", "asset_type": "pump", "alarm_type": "device", "severity": "critical", "uom": "mm/s", "setpoint": 7.1, "direction": "hi"},
    "PMP-SUCT-LO": {"alarm_name": "Low Suction Pressure", "asset_type": "pump", "alarm_type": "process", "severity": "high", "uom": "bar", "setpoint": 2.5, "direction": "lo"},
    "PMP-BRG-TEMP-HI": {"alarm_name": "High Bearing Temperature", "asset_type": "pump", "alarm_type": "device", "severity": "high", "uom": "degC", "setpoint": 85, "direction": "hi"},
    "PMP-SEAL-LEAK": {"alarm_name": "Seal Leak Detected", "asset_type": "pump", "alarm_type": "device", "severity": "medium", "uom": None, "setpoint": None, "direction": None},
    # motors
    "MTR-TRIP": {"alarm_name": "Motor Trip", "asset_type": "motor", "alarm_type": "safety", "severity": "critical", "uom": None, "setpoint": None, "direction": None},
    "MTR-OVERLOAD": {"alarm_name": "Overload Current", "asset_type": "motor", "alarm_type": "device", "severity": "high", "uom": "A", "setpoint": 410, "direction": "hi"},
    "MTR-WIND-TEMP-HI": {"alarm_name": "High Winding Temperature", "asset_type": "motor", "alarm_type": "device", "severity": "high", "uom": "degC", "setpoint": 130, "direction": "hi"},
    "MTR-BRG-TEMP-HI": {"alarm_name": "High Bearing Temperature", "asset_type": "motor", "alarm_type": "device", "severity": "high", "uom": "degC", "setpoint": 90, "direction": "hi"},
    # compressors
    "CMP-DISCH-PRESS-HI": {"alarm_name": "High Discharge Pressure", "asset_type": "compressor", "alarm_type": "process", "severity": "critical", "uom": "bar", "setpoint": 42, "direction": "hi"},
    "CMP-SURGE": {"alarm_name": "Surge Detected", "asset_type": "compressor", "alarm_type": "safety", "severity": "critical", "uom": None, "setpoint": None, "direction": None},
    "CMP-VIB-HI": {"alarm_name": "High Vibration", "asset_type": "compressor", "alarm_type": "device", "severity": "high", "uom": "mm/s", "setpoint": 9.0, "direction": "hi"},
    # valves
    "VLV-POS-DEV": {"alarm_name": "Position Deviation", "asset_type": "valve", "alarm_type": "device", "severity": "medium", "uom": "%", "setpoint": 5, "direction": "hi"},
    "VLV-STUCK": {"alarm_name": "Valve Stuck", "asset_type": "valve", "alarm_type": "device", "severity": "high", "uom": None, "setpoint": None, "direction": None},
    # deaerator
    "DEA-LVL-LO": {"alarm_name": "Low Level", "asset_type": "deaerator", "alarm_type": "process", "severity": "high", "uom": "%", "setpoint": 35, "direction": "lo"},
    "DEA-PRESS-LO": {"alarm_name": "Low Pressure", "asset_type": "deaerator", "alarm_type": "process", "severity": "medium", "uom": "bar", "setpoint": 1.2, "direction": "lo"},
    # boiler
    "BLR-FLAME-FAIL": {"alarm_name": "Flame Failure", "asset_type": "boiler", "alarm_type": "safety", "severity": "critical", "uom": None, "setpoint": None, "direction": None},
    "BLR-DRUM-LVL-LO": {"alarm_name": "Low Drum Level", "asset_type": "boiler", "alarm_type": "safety", "severity": "critical", "uom": "%", "setpoint": 30, "direction": "lo"},
    "BLR-STEAM-PRESS-HI": {"alarm_name": "High Steam Pressure", "asset_type": "boiler", "alarm_type": "process", "severity": "high", "uom": "bar", "setpoint": 165, "direction": "hi"},
    # fan
    "FAN-TRIP": {"alarm_name": "Fan Trip", "asset_type": "fan", "alarm_type": "safety", "severity": "critical", "uom": None, "setpoint": None, "direction": None},
    "FAN-VIB-HI": {"alarm_name": "High Vibration", "asset_type": "fan", "alarm_type": "device", "severity": "medium", "uom": "mm/s", "setpoint": 6.3, "direction": "hi"},
    # heat exchanger
    "HX-OUT-TEMP-HI": {"alarm_name": "High Outlet Temperature", "asset_type": "heat_exchanger", "alarm_type": "process", "severity": "high", "uom": "degC", "setpoint": 55, "direction": "hi"},
    "HX-DP-HI": {"alarm_name": "High Differential Pressure", "asset_type": "heat_exchanger", "alarm_type": "process", "severity": "medium", "uom": "bar", "setpoint": 0.8, "direction": "hi"},
    # fired heater
    "HTR-TUBE-TEMP-HI": {"alarm_name": "High Tube Skin Temperature", "asset_type": "fired_heater", "alarm_type": "safety", "severity": "high", "uom": "degC", "setpoint": 540, "direction": "hi"},
    "HTR-O2-LO": {"alarm_name": "Low Excess O2", "asset_type": "fired_heater", "alarm_type": "process", "severity": "medium", "uom": "%", "setpoint": 2.0, "direction": "lo"},
}

# ------------------------------------------------------------- API recommendations
# What the (fake) API suggests. One entry deliberately disagrees with the manuals
# (PMP-VIB-HI says "keep running and monitor") so the copilot can flag the conflict.
RECOMMENDATIONS = {
    "PMP-VIB-HI": {
        "likely_causes": ["Bearing wear", "Shaft misalignment", "Cavitation from low suction pressure"],
        "actions": ["Continue operation and monitor vibration trend for 24 hours",
                    "Check drive motor bearing temperature",
                    "Schedule vibration analysis at next maintenance window"]},
    "PMP-SUCT-LO": {
        "likely_causes": ["Low deaerator level", "Clogged suction strainer"],
        "actions": ["Verify deaerator level", "Check suction strainer differential pressure",
                    "Reduce pump flow if suction pressure keeps falling"]},
    "PMP-BRG-TEMP-HI": {
        "likely_causes": ["Insufficient lubrication", "Bearing wear"],
        "actions": ["Check lube oil level and flow", "Compare with vibration readings"]},
    "PMP-SEAL-LEAK": {
        "likely_causes": ["Worn mechanical seal"],
        "actions": ["Inspect seal area", "Plan seal replacement"]},
    "MTR-TRIP": {
        "likely_causes": ["Winding over-temperature", "Overload from driven equipment", "Loss of cooling water"],
        "actions": ["Do not restart until cause is found", "Check winding temperature history",
                    "Inspect driven compressor for surge events", "Verify cooling water supply"]},
    "MTR-OVERLOAD": {
        "likely_causes": ["Mechanical binding", "Process overload"],
        "actions": ["Reduce load", "Check driven equipment"]},
    "MTR-WIND-TEMP-HI": {
        "likely_causes": ["Cooling failure", "Sustained overload"],
        "actions": ["Check cooling fan and cooling water", "Reduce load"]},
    "MTR-BRG-TEMP-HI": {
        "likely_causes": ["Lubrication failure", "Bearing wear", "Coupling misalignment"],
        "actions": ["Check grease/oil", "Check coupling alignment", "Monitor driven pump vibration"]},
    "CMP-DISCH-PRESS-HI": {
        "likely_causes": ["Fouled discharge cooler", "Downstream valve restriction"],
        "actions": ["Check discharge cooler outlet temperature", "Open recycle valve to reduce pressure",
                    "Schedule cooler cleaning"]},
    "CMP-SURGE": {
        "likely_causes": ["Low flow", "Anti-surge valve slow response"],
        "actions": ["Open anti-surge valve", "Increase recycle flow"]},
    "CMP-VIB-HI": {
        "likely_causes": ["Rotor imbalance", "Surge"],
        "actions": ["Check for surge", "Plan vibration analysis"]},
    "VLV-POS-DEV": {
        "likely_causes": ["Positioner calibration drift", "Low instrument air pressure"],
        "actions": ["Check instrument air", "Recalibrate positioner"]},
    "VLV-STUCK": {
        "likely_causes": ["Actuator failure", "Packing too tight"],
        "actions": ["Switch to manual bypass", "Inspect actuator"]},
    "DEA-LVL-LO": {
        "likely_causes": ["Makeup water valve issue", "High feed pump demand"],
        "actions": ["Check makeup water supply", "Watch feed pump suction pressure"]},
    "DEA-PRESS-LO": {
        "likely_causes": ["Low pegging steam"],
        "actions": ["Check pegging steam supply"]},
    "BLR-FLAME-FAIL": {
        "likely_causes": ["Fuel supply interruption", "Flame scanner fault"],
        "actions": ["Follow boiler trip procedure", "Purge furnace before relight"]},
    "BLR-DRUM-LVL-LO": {
        "likely_causes": ["Feedwater supply loss"],
        "actions": ["Verify feed pumps running", "Follow low drum level procedure"]},
    "BLR-STEAM-PRESS-HI": {
        "likely_causes": ["Load rejection"],
        "actions": ["Reduce firing rate"]},
    "FAN-TRIP": {
        "likely_causes": ["Motor protection trip", "Boiler trip interlock"],
        "actions": ["Check interlock status", "Inspect fan motor"]},
    "FAN-VIB-HI": {
        "likely_causes": ["Blade fouling", "Imbalance"],
        "actions": ["Plan blade cleaning"]},
    "HX-OUT-TEMP-HI": {
        "likely_causes": ["Fouled tubes", "Low cooling water flow"],
        "actions": ["Check cooling water flow", "Plan exchanger cleaning"]},
    "HX-DP-HI": {
        "likely_causes": ["Tube fouling"],
        "actions": ["Plan exchanger cleaning"]},
    "HTR-TUBE-TEMP-HI": {
        "likely_causes": ["Low charge flow", "Flame impingement"],
        "actions": ["Verify charge pump flow", "Reduce firing"]},
    "HTR-O2-LO": {
        "likely_causes": ["Air damper position", "O2 analyzer drift"],
        "actions": ["Check damper", "Verify analyzer calibration"]},
}


class AlarmFactory:
    """Builds alarm event dicts with realistic values; collects them in a list."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.assets = {a["asset_id"]: a for a in ASSETS}
        self.events: list[dict] = []

    def add(self, asset_id: str, code: str, start: datetime, *, duration_min: float | None,
            ack_delay_min: float | None, severity: str | None = None, value_factor: float | None = None,
            pattern: str | None = None) -> datetime:
        """Add one alarm. duration_min=None means still active. ack_delay_min=None means unacknowledged."""
        asset = self.assets[asset_id]
        d = ALARM_DEFINITIONS[code]
        assert d["asset_type"] == asset["asset_type"], f"{code} does not apply to {asset_id}"

        value = None
        if d["direction"] == "hi":
            value = round(d["setpoint"] * (value_factor or self.rng.uniform(1.03, 1.25)), 2)
        elif d["direction"] == "lo":
            value = round(d["setpoint"] * (value_factor or self.rng.uniform(0.7, 0.97)), 2)

        end = None if duration_min is None else start + timedelta(minutes=duration_min)
        ack = None if ack_delay_min is None else start + timedelta(minutes=ack_delay_min)
        if end and ack and ack > end:
            ack = end

        self.events.append({
            "asset_id": asset_id,
            "asset_name": asset["name"],
            "site": asset["site"],
            "unit": asset["unit"],
            "alarm_code": code,
            "alarm_name": d["alarm_name"],
            "alarm_type": d["alarm_type"],
            "severity": severity or d["severity"],
            "status": "active" if end is None else "cleared",
            "acknowledged": ack is not None,
            "start_time": start,
            "ack_time": ack,
            "end_time": end,
            "value": value,
            "setpoint": d["setpoint"],
            "uom": d["uom"],
            "message": f"{asset['name']}: {d['alarm_name']}"
                       + (f" ({value} {d['uom']}, setpoint {d['setpoint']})" if value is not None else ""),
            "_pattern": pattern,  # stripped before writing; used only for the summary
        })
        return start

    def rand_time(self, start: datetime = WINDOW_START, end: datetime = WINDOW_END - timedelta(days=1)) -> datetime:
        span = (end - start).total_seconds()
        t = start + timedelta(seconds=self.rng.uniform(0, span))
        return t.replace(second=0, microsecond=0)


# --------------------------------------------------------------------------- patterns
def pattern_bfp101_vibration(f: AlarmFactory) -> None:
    """Recurring high vibration on BFP-101, getting more frequent and more severe.
    ~80% are preceded 5-15 min by a hot bearing on drive motor M-101."""
    rng, t, n = f.rng, WINDOW_START + timedelta(days=4, hours=9), 0
    while t < WINDOW_END - timedelta(days=2):
        progress = (t - WINDOW_START) / (WINDOW_END - WINDOW_START)
        if rng.random() < 0.8:
            f.add("AST-MTR-101", "MTR-BRG-TEMP-HI", t - timedelta(minutes=rng.randint(5, 15)),
                  duration_min=rng.randint(30, 120), ack_delay_min=rng.randint(2, 15), pattern="bfp101")
        f.add("AST-BFP-101", "PMP-VIB-HI", t, duration_min=rng.randint(20, 90), ack_delay_min=rng.randint(2, 12),
              value_factor=1.05 + 0.5 * progress + rng.uniform(0, 0.1), pattern="bfp101")
        if rng.random() < 0.3:
            f.add("AST-FV-101", "VLV-POS-DEV", t + timedelta(minutes=rng.randint(3, 15)),
                  duration_min=rng.randint(5, 30), ack_delay_min=rng.randint(3, 20), pattern="bfp101")
        n += 1
        gap_days = 6.0 - 4.0 * progress  # 6 days apart in April -> 2 days apart in June
        t += timedelta(days=gap_days * rng.uniform(0.7, 1.3), minutes=rng.randint(-180, 180))
        t = t.replace(second=0, microsecond=0)


def pattern_bfp101_suction(f: AlarmFactory) -> None:
    """Secondary factor: low deaerator level -> low suction pressure on BFP-101."""
    rng = f.rng
    for _ in range(8):
        t = f.rand_time()
        f.add("AST-DEA-101", "DEA-LVL-LO", t, duration_min=rng.randint(15, 60), ack_delay_min=rng.randint(2, 10), pattern="bfp101")
        f.add("AST-BFP-101", "PMP-SUCT-LO", t + timedelta(minutes=rng.randint(2, 10)),
              duration_min=rng.randint(10, 40), ack_delay_min=rng.randint(2, 10), pattern="bfp101")


def pattern_bfp102_active(f: AlarmFactory) -> None:
    """BFP-102: a quiet history, then active critical alarms at the end of the window."""
    rng = f.rng
    for code in ["PMP-SUCT-LO", "PMP-BRG-TEMP-HI", "PMP-SEAL-LEAK", "PMP-VIB-HI"]:
        f.add("AST-BFP-102", code, f.rand_time(), duration_min=rng.randint(10, 60), ack_delay_min=rng.randint(2, 10), pattern="bfp102")
    base = datetime(2026, 6, 30, 21, 50, tzinfo=timezone.utc)
    f.add("AST-BFP-102", "PMP-SUCT-LO", base, duration_min=None, ack_delay_min=4, severity="critical", value_factor=0.62, pattern="bfp102")
    f.add("AST-BFP-102", "PMP-VIB-HI", base + timedelta(minutes=15), duration_min=None, ack_delay_min=None, value_factor=1.45, pattern="bfp102")
    f.add("AST-BFP-102", "PMP-BRG-TEMP-HI", base + timedelta(minutes=30), duration_min=None, ack_delay_min=None, pattern="bfp102")
    f.add("AST-MTR-102", "MTR-OVERLOAD", base + timedelta(minutes=33), duration_min=None, ack_delay_min=None, pattern="bfp102")


def pattern_unit2_flood(f: AlarmFactory) -> None:
    """Unit 2 alarm flood: boiler flame failure cascades into 18 alarms in ~9 minutes."""
    rng = f.rng
    t0 = datetime(2026, 6, 14, 3, 12, tzinfo=timezone.utc)
    cascade = [("AST-BLR-201", "BLR-FLAME-FAIL"), ("AST-FAN-201", "FAN-TRIP"), ("AST-BLR-201", "BLR-DRUM-LVL-LO"),
               ("AST-BLR-201", "BLR-STEAM-PRESS-HI"), ("AST-PMP-202", "PMP-SUCT-LO"), ("AST-FAN-201", "FAN-VIB-HI")]
    for i in range(18):
        asset_id, code = cascade[i % len(cascade)]
        f.add(asset_id, code, t0 + timedelta(seconds=i * 30 + rng.randint(0, 20)),
              duration_min=rng.randint(5, 45), ack_delay_min=rng.randint(4, 25), pattern="unit2_flood")
    # A near-miss in May: busy, but under the 10-in-10-minutes flood threshold.
    t1 = datetime(2026, 5, 9, 16, 40, tzinfo=timezone.utc)
    for i in range(7):
        asset_id, code = cascade[(i + 2) % len(cascade)]
        f.add(asset_id, code, t1 + timedelta(minutes=i * 1.5), duration_min=rng.randint(5, 20),
              ack_delay_min=rng.randint(2, 10), pattern="unit2_near_flood")


def pattern_cmp301_pressure(f: AlarmFactory) -> None:
    """C-301 recurring high discharge pressure (some chattering), preceded by hot cooler E-301."""
    rng = f.rng
    for _ in range(14):
        t = f.rand_time()
        f.add("AST-HX-301", "HX-OUT-TEMP-HI", t - timedelta(minutes=rng.randint(10, 30)),
              duration_min=rng.randint(30, 90), ack_delay_min=rng.randint(3, 20), pattern="cmp301")
        f.add("AST-CMP-301", "CMP-DISCH-PRESS-HI", t, duration_min=rng.randint(5, 40), ack_delay_min=rng.randint(2, 10), pattern="cmp301")
        if rng.random() < 0.5:  # chattering: re-alarms within minutes, very short duration
            for k in range(rng.randint(2, 4)):
                f.add("AST-CMP-301", "CMP-DISCH-PRESS-HI", t + timedelta(minutes=45 + k * rng.randint(2, 5)),
                      duration_min=rng.uniform(0.3, 1.5), ack_delay_min=None, severity="high", pattern="cmp301_chatter")
    # Still active now
    f.add("AST-HX-301", "HX-OUT-TEMP-HI", datetime(2026, 6, 30, 18, 5, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=6, pattern="cmp301")
    f.add("AST-CMP-301", "CMP-DISCH-PRESS-HI", datetime(2026, 6, 30, 18, 30, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=None, value_factor=1.19, pattern="cmp301")


def pattern_m501_trips(f: AlarmFactory) -> None:
    """M-501 motor trips: winding temp high + C-502 surge (+ sometimes M-502 overload) beforehand."""
    rng = f.rng
    for _ in range(5):
        t = f.rand_time()
        f.add("AST-MTR-501", "MTR-WIND-TEMP-HI", t - timedelta(minutes=rng.randint(8, 20)),
              duration_min=rng.randint(10, 30), ack_delay_min=rng.randint(2, 8), pattern="m501")
        f.add("AST-CMP-502", "CMP-SURGE", t - timedelta(minutes=rng.randint(1, 6)),
              duration_min=rng.randint(2, 10), ack_delay_min=rng.randint(1, 5), pattern="m501")
        if rng.random() < 0.6:
            f.add("AST-MTR-502", "MTR-OVERLOAD", t - timedelta(minutes=rng.randint(15, 40)),
                  duration_min=rng.randint(10, 30), ack_delay_min=rng.randint(3, 10), pattern="m501")
        f.add("AST-MTR-501", "MTR-TRIP", t, duration_min=rng.randint(30, 180), ack_delay_min=rng.randint(1, 4), pattern="m501")
    f.add("AST-MTR-501", "MTR-WIND-TEMP-HI", datetime(2026, 6, 30, 20, 10, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=9, pattern="m501")


def pattern_stale(f: AlarmFactory) -> None:
    """Alarms left active for days (stale) - rationalization / housekeeping candidates."""
    f.add("AST-FV-101", "VLV-POS-DEV", datetime(2026, 6, 20, 7, 15, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=35, pattern="stale")
    f.add("AST-HX-301", "HX-DP-HI", datetime(2026, 6, 10, 11, 0, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=60, pattern="stale")
    f.add("AST-HTR-401", "HTR-O2-LO", datetime(2026, 6, 25, 2, 40, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=20, pattern="stale")
    f.add("AST-PMP-401", "PMP-SEAL-LEAK", datetime(2026, 6, 30, 9, 25, tzinfo=timezone.utc),
          duration_min=None, ack_delay_min=12, pattern="stale")


def background_noise(f: AlarmFactory, count: int) -> None:
    """Random cleared alarms on every asset so patterns are not trivially obvious."""
    rng = f.rng
    codes_by_type: dict[str, list[str]] = {}
    for code, d in ALARM_DEFINITIONS.items():
        codes_by_type.setdefault(d["asset_type"], []).append(code)
    for _ in range(count):
        asset = rng.choice(ASSETS)
        code = rng.choice(codes_by_type[asset["asset_type"]])
        sev = ALARM_DEFINITIONS[code]["severity"]
        if sev == "critical" and rng.random() < 0.7:
            sev = "high"  # keep random noise from looking too dramatic
        f.add(asset["asset_id"], code, f.rand_time(), duration_min=rng.uniform(1, 120),
              ack_delay_min=rng.uniform(0.5, 30) if rng.random() < 0.9 else None, severity=sev, pattern="noise")


# ------------------------------------------------------------------------- assemble
def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def build(seed: int) -> dict:
    f = AlarmFactory(random.Random(seed))
    pattern_bfp101_vibration(f)
    pattern_bfp101_suction(f)
    pattern_bfp102_active(f)
    pattern_unit2_flood(f)
    pattern_cmp301_pressure(f)
    pattern_m501_trips(f)
    pattern_stale(f)
    background_noise(f, count=260)

    f.events.sort(key=lambda e: (e["start_time"], e["asset_id"], e["alarm_code"]))
    alarms = []
    for i, e in enumerate(f.events, start=1):
        e = {"alarm_id": f"ALM-{i:06d}", **e}
        for k in ("start_time", "ack_time", "end_time"):
            e[k] = iso(e[k])
        alarms.append(e)

    return {
        "metadata": {
            "description": "Synthetic seed data for the Alarm Management API simulator. Not real plant data.",
            "seed": seed,
            "window_start": iso(WINDOW_START),
            "window_end": iso(WINDOW_END),
            "reference_now": iso(WINDOW_END),
            "counts": {"sites": len(SITES), "assets": len(ASSETS), "alarm_definitions": len(ALARM_DEFINITIONS),
                       "alarms": len(alarms)},
        },
        "sites": SITES,
        "assets": ASSETS,
        "alarm_definitions": [{"alarm_code": c, **d} for c, d in ALARM_DEFINITIONS.items()],
        "recommendations": [{"alarm_code": c, **r} for c, r in RECOMMENDATIONS.items()],
        "alarms": alarms,
    }


def summarize(data: dict) -> str:
    alarms = data["alarms"]
    lines = [f"Total alarms: {len(alarms)}  (assets: {len(data['assets'])}, alarm types: {len(data['alarm_definitions'])})"]
    lines.append("By pattern: " + ", ".join(f"{k}={v}" for k, v in Counter(a["_pattern"] for a in alarms).most_common()))
    lines.append("By severity: " + ", ".join(f"{k}={v}" for k, v in Counter(a["severity"] for a in alarms).most_common()))
    active = [a for a in alarms if a["status"] == "active"]
    lines.append(f"Active now: {len(active)}")
    for a in active:
        lines.append(f"   {a['alarm_id']}  {a['site']:<12} {a['asset_name']:<32} {a['alarm_name']:<26} {a['severity']}")
    bfp = [a for a in alarms if a["asset_id"] == "AST-BFP-101" and a["severity"] in ("high", "critical")]
    lines.append(f"BFP-101 high/critical alarms in window: {len(bfp)}  "
                 f"(by type: {dict(Counter(a['alarm_name'] for a in bfp))})")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data = build(args.seed)
    print(summarize(data))
    for a in data["alarms"]:
        a.pop("_pattern")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
