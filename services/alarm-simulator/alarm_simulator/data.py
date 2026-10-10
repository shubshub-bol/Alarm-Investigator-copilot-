"""In-memory data store built from seed_data.json. Read-only after load."""

import json
from datetime import datetime
from pathlib import Path

from alarm_simulator.models import Alarm, AlarmDefinition, Asset, Recommendation, Site


class DataStore:
    def __init__(self, raw: dict):
        meta = raw["metadata"]
        self.reference_now = datetime.fromisoformat(meta["reference_now"])
        self.window_start = datetime.fromisoformat(meta["window_start"])
        self.window_end = datetime.fromisoformat(meta["window_end"])

        self.sites = [Site(**s) for s in raw["sites"]]
        self.assets = [Asset(**a) for a in raw["assets"]]
        self.definitions = [AlarmDefinition(**d) for d in raw["alarm_definitions"]]
        self.recommendations = [Recommendation(**r) for r in raw["recommendations"]]
        self.alarms = sorted((Alarm(**a) for a in raw["alarms"]), key=lambda a: a.start_time)

        self.assets_by_id = {a.asset_id: a for a in self.assets}
        self.alarms_by_id = {a.alarm_id: a for a in self.alarms}
        self.definitions_by_code = {d.alarm_code: d for d in self.definitions}
        self.recommendations_by_code = {r.alarm_code: r for r in self.recommendations}

    @classmethod
    def load(cls, path: Path) -> "DataStore":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))
