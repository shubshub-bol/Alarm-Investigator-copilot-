"""Part 3: summary, trends, correlation, flood analysis, rationalization candidates, priority score.

Most tests check that the analytics surface the patterns planted in the seed data.
"""

MAY_JUL = {"start_time": "2026-05-01T00:00:00Z", "end_time": "2026-07-01T00:00:00Z"}
LAST_90 = {"start_time": "2026-04-02T00:00:00Z", "end_time": "2026-07-01T00:00:00Z"}


# --------------------------------------------------------------------- summary


def test_summary_chain01_groups_bfp101_by_alarm_name(client, auth):
    body = {
        "asset_ids": ["AST-BFP-101"],
        "time_range": MAY_JUL,
        "severity": ["high", "critical"],
        "group_by": ["alarm_name"],
        "kpis": ["alarm_count", "recurring_rate", "avg_ack_delay"],
    }
    r = client.post("/alarms/summary", json=body, headers=auth)
    assert r.status_code == 200
    groups = r.json()["groups"]
    top = groups[0]
    assert top["key"] == {"alarm_name": "High Vibration"}
    assert top["kpis"]["alarm_count"] >= 15
    assert top["kpis"]["recurring_rate"] == 1.0
    assert top["kpis"]["avg_ack_delay"] > 0


def test_summary_multi_field_grouping(client, auth):
    body = {"unit": "Unit 2", "time_range": MAY_JUL, "group_by": ["asset_id", "asset_name", "severity"]}
    groups = client.post("/alarms/summary", json=body, headers=auth).json()["groups"]
    assert groups
    assert set(groups[0]["key"]) == {"asset_id", "asset_name", "severity"}


def test_summary_alarm_type_filter(client, auth):
    body = {"unit": "Unit 5", "time_range": MAY_JUL, "alarm_types": ["safety"], "group_by": ["alarm_type"]}
    groups = client.post("/alarms/summary", json=body, headers=auth).json()["groups"]
    assert [g["key"]["alarm_type"] for g in groups] == ["safety"]


def test_summary_defaults_to_last_90_days(client, auth):
    body = client.post("/alarms/summary", json={"asset_ids": ["AST-BFP-101"]}, headers=auth).json()
    assert body["time_range"]["end_time"] == "2026-07-01T00:00:00Z"
    assert body["time_range"]["start_time"] == "2026-04-02T00:00:00Z"


def test_summary_rejects_unknown_kpi(client, auth):
    r = client.post("/alarms/summary", json={"kpis": ["happiness"]}, headers=auth)
    assert r.status_code == 422


def test_summary_rejects_unknown_group_by(client, auth):
    r = client.post("/alarms/summary", json={"group_by": ["password"]}, headers=auth)
    assert r.status_code == 422


def test_summary_rejects_unexpected_fields(client, auth):
    r = client.post("/alarms/summary", json={"asset_idz": ["AST-BFP-101"]}, headers=auth)
    assert r.status_code == 422


def test_summary_rejects_inverted_time_range(client, auth):
    body = {"time_range": {"start_time": "2026-06-01T00:00:00Z", "end_time": "2026-05-01T00:00:00Z"}}
    assert client.post("/alarms/summary", json=body, headers=auth).status_code == 422


def test_summary_unknown_asset_is_404(client, auth):
    r = client.post("/alarms/summary", json={"asset_ids": ["AST-NOPE"]}, headers=auth)
    assert r.status_code == 404


# ---------------------------------------------------------------------- trends


def test_trends_daily_series_covers_range_and_matches_summary(client, auth):
    body = {"asset_ids": ["AST-BFP-101"], "time_range": MAY_JUL, "bucket": "daily", "metrics": ["alarm_count"]}
    trend = client.post("/alarms/trends", json=body, headers=auth).json()
    assert len(trend["series"]) == 61
    total = sum(p["values"]["alarm_count"] for p in trend["series"])
    summary = client.post("/alarms/summary", json={"asset_ids": ["AST-BFP-101"], "time_range": MAY_JUL}, headers=auth)
    assert total == summary.json()["totals"]["alarm_count"]


def test_trends_detects_bfp101_vibration_getting_worse(client, auth):
    body = {"asset_ids": ["AST-BFP-101"], "alarm_codes": ["PMP-VIB-HI"], "time_range": LAST_90, "bucket": "weekly"}
    trend = client.post("/alarms/trends", json=body, headers=auth).json()["trend"]
    assert trend["direction"] == "increasing"


def test_trends_all_bfp101_alarms_are_flat_overall(client, auth):
    """Other BFP-101 alarm types fall while vibration rises, so the total is stable.
    This is why the alarm_codes filter exists."""
    body = {"asset_ids": ["AST-BFP-101"], "time_range": LAST_90, "bucket": "weekly"}
    assert client.post("/alarms/trends", json=body, headers=auth).json()["trend"]["direction"] == "stable"


def test_trends_rejects_unknown_bucket(client, auth):
    assert client.post("/alarms/trends", json={"bucket": "fortnightly"}, headers=auth).status_code == 422


def test_trends_rejects_too_many_buckets(client, auth):
    body = {
        "bucket": "hourly",
        "time_range": {"start_time": "2020-01-01T00:00:00Z", "end_time": "2026-07-01T00:00:00Z"},
    }
    assert client.post("/alarms/trends", json=body, headers=auth).status_code == 422


# ----------------------------------------------------------------- correlation


def test_correlation_finds_motor_bearing_leading_pump_vibration(client, auth):
    body = {
        "asset_ids": ["AST-BFP-101", "AST-MTR-101"],
        "time_range": LAST_90,
        "correlation_method": "cooccurrence",
        "lag_window_minutes": 15,
        "min_support": 3,
    }
    r = client.post("/alarms/correlation", json=body, headers=auth)
    assert r.status_code == 200
    top = r.json()["pairs"][0]
    assert (top["leading"]["asset_id"], top["leading"]["alarm_code"]) == ("AST-MTR-101", "MTR-BRG-TEMP-HI")
    assert (top["following"]["asset_id"], top["following"]["alarm_code"]) == ("AST-BFP-101", "PMP-VIB-HI")
    assert top["relation"] == "precedes"
    assert top["confidence"] >= 0.6
    assert 5 <= top["median_lead_minutes"] <= 15
    assert "Motor M-101" in top["insight"]


def test_correlation_finds_cooler_leading_compressor_pressure(client, auth):
    body = {
        "asset_ids": ["AST-CMP-301", "AST-HX-301"],
        "time_range": LAST_90,
        "lag_window_minutes": 30,
        "min_support": 3,
    }
    top = client.post("/alarms/correlation", json=body, headers=auth).json()["pairs"][0]
    assert top["leading"]["alarm_code"] == "HX-OUT-TEMP-HI"
    assert top["following"]["alarm_code"] == "CMP-DISCH-PRESS-HI"


def test_correlation_severity_threshold_excludes_lower_alarms(client, auth):
    body = {"asset_ids": ["AST-BFP-101", "AST-MTR-101"], "time_range": LAST_90, "severity_threshold": "critical"}
    pairs = client.post("/alarms/correlation", json=body, headers=auth).json()["pairs"]
    codes = {p["leading"]["alarm_code"] for p in pairs} | {p["following"]["alarm_code"] for p in pairs}
    assert "MTR-BRG-TEMP-HI" not in codes  # that alarm is only 'high'


def test_correlation_requires_asset_ids(client, auth):
    assert client.post("/alarms/correlation", json={"asset_ids": []}, headers=auth).status_code == 422


def test_correlation_rejects_unsupported_method(client, auth):
    body = {"asset_ids": ["AST-BFP-101"], "correlation_method": "astrology"}
    assert client.post("/alarms/correlation", json=body, headers=auth).status_code == 422


# ------------------------------------------------------------------------ flood


def test_flood_analysis_finds_single_unit2_flood(client, auth):
    body = {"unit": "Unit 2", "time_range": MAY_JUL, "threshold_count": 10, "rolling_window_minutes": 10}
    r = client.post("/alarms/flood-analysis", json=body, headers=auth)
    assert r.status_code == 200
    windows = r.json()["flood_windows"]
    assert len(windows) == 1
    w = windows[0]
    assert w["alarm_count"] == 18
    assert w["start"].startswith("2026-06-14T03:1")
    assert w["initiating_alarm"]["alarm_name"] == "Flame Failure"


def test_flood_window_can_be_chained_into_alarm_list(client, auth):
    """Postman CHAIN-02: flood window -> GET /alarms for exactly that window."""
    body = {"unit": "Unit 2", "time_range": MAY_JUL}
    w = client.post("/alarms/flood-analysis", json=body, headers=auth).json()["flood_windows"][0]
    params = {"unit": "Unit 2", "start_time": w["start"], "end_time": w["end"], "page_size": 200}
    listed = client.get("/alarms", params=params, headers=auth).json()
    assert listed["pagination"]["total_items"] == w["alarm_count"]


def test_flood_lower_threshold_also_finds_near_miss(client, auth):
    body = {"unit": "Unit 2", "time_range": MAY_JUL, "threshold_count": 5}
    assert len(client.post("/alarms/flood-analysis", json=body, headers=auth).json()["flood_windows"]) == 2


def test_flood_none_in_quiet_unit(client, auth):
    body = {"unit": "Unit 6", "time_range": MAY_JUL}
    assert client.post("/alarms/flood-analysis", json=body, headers=auth).json()["flood_windows"] == []


# -------------------------------------------------------------- rationalization


def test_rationalization_flags_bfp101_vibration_as_recurring(client, auth):
    body = {"asset_ids": ["AST-BFP-101"], "time_range": MAY_JUL, "recurrence_threshold": 5}
    top = client.post("/alarms/rationalization-candidates", json=body, headers=auth).json()["candidates"][0]
    assert top["alarm_code"] == "PMP-VIB-HI"
    assert "recurring" in top["reasons"]


def test_rationalization_flags_compressor_chattering(client, auth):
    body = {"asset_ids": ["AST-CMP-301"], "time_range": LAST_90}
    cands = client.post("/alarms/rationalization-candidates", json=body, headers=auth).json()["candidates"]
    press = next(c for c in cands if c["alarm_code"] == "CMP-DISCH-PRESS-HI")
    assert "chattering" in press["reasons"]


def test_rationalization_flags_stale_valve_alarm(client, auth):
    body = {"site": "NorthPlant", "unit": "Unit 1", "time_range": MAY_JUL, "stale_minutes_threshold": 180}
    cands = client.post("/alarms/rationalization-candidates", json=body, headers=auth).json()["candidates"]
    valve = next(c for c in cands if c["asset_id"] == "AST-FV-101" and c["alarm_code"] == "VLV-POS-DEV")
    assert "stale" in valve["reasons"]


def test_rationalization_never_marks_safety_alarms_suppressible(client, auth):
    body = {"asset_ids": ["AST-MTR-501"], "time_range": LAST_90, "recurrence_threshold": 3}
    cands = client.post("/alarms/rationalization-candidates", json=body, headers=auth).json()["candidates"]
    trip = next(c for c in cands if c["alarm_code"] == "MTR-TRIP")
    assert trip["suppression_eligible"] is False


# ---------------------------------------------------------------------- priority


def _active(client, auth, **params):
    return client.get("/alarms", params={"status": "active", **params}, headers=auth).json()["data"]


def test_priority_active_critical_compressor_is_p1(client, auth):
    alarm = next(a for a in _active(client, auth, asset_id="AST-CMP-301") if a["alarm_code"] == "CMP-DISCH-PRESS-HI")
    r = client.post("/alarms/priority-score", json={"alarm_id": alarm["alarm_id"]}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["priority"] == "P1"
    assert body["score"] >= 75
    assert {f["name"] for f in body["factors"]} >= {"severity", "asset_criticality", "state"}


def test_priority_ranks_compressor_highest_in_eastrefinery(client, auth):
    scores = {
        a["alarm_id"]: client.post("/alarms/priority-score", json={"alarm_id": a["alarm_id"]}, headers=auth).json()[
            "score"
        ]
        for a in _active(client, auth, site="EastRefinery")
    }
    best = max(scores, key=scores.get)
    detail = client.get(f"/alarms/{best}", headers=auth).json()
    assert detail["asset_id"] == "AST-CMP-301"


def test_priority_factors_sum_to_score(client, auth):
    alarm = _active(client, auth, asset_id="AST-BFP-102")[0]
    body = client.post("/alarms/priority-score", json={"alarm_id": alarm["alarm_id"]}, headers=auth).json()
    assert round(sum(f["points"] for f in body["factors"]), 1) == body["score"]


def test_priority_unknown_alarm_is_404(client, auth):
    assert client.post("/alarms/priority-score", json={"alarm_id": "ALM-999999"}, headers=auth).status_code == 404


def test_priority_missing_alarm_id_is_422(client, auth):
    assert client.post("/alarms/priority-score", json={}, headers=auth).status_code == 422
