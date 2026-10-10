"""Part 4: operator recommendations and KPI calculation generate -> execute."""

import pytest

MAY_JUL = {"start_time": "2026-05-01T00:00:00Z", "end_time": "2026-07-01T00:00:00Z"}
CALC_TYPES = ["alarm_flood_index", "critical_alarm_density", "operator_response_efficiency", "nuisance_alarm_score"]


def _latest_alarm(client, auth, **params) -> dict:
    query = {"page_size": 1, "sort_by": "start_time", "sort_order": "desc", **params}
    return client.get("/alarms", params=query, headers=auth).json()["data"][0]


# ------------------------------------------------------------- recommendations


def test_recommendations_for_pump_vibration(client, auth):
    alarm = _latest_alarm(client, auth, asset_id="AST-BFP-101", alarm_code="PMP-VIB-HI")
    r = client.post("/recommendations/operator-actions", json={"alarm_id": alarm["alarm_id"]}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "Bearing wear" in body["likely_causes"]
    assert [s["step"] for s in body["recommended_actions"]] == [1, 2, 3]
    assert body["source"] == "alarm-response-catalog"
    assert body["priority"]["priority"] in {"P1", "P2", "P3", "P4"}
    # optional sections are omitted unless requested
    assert body["related_assets"] is None
    assert body["asset_context"] is None
    assert body["historical_pattern"] is None


def test_recommendations_with_all_context(client, auth):
    alarm = _latest_alarm(client, auth, asset_id="AST-BFP-101", alarm_code="PMP-VIB-HI")
    body = {
        "alarm_id": alarm["alarm_id"],
        "include_related": True,
        "include_asset_context": True,
        "include_historical_pattern": True,
    }
    out = client.post("/recommendations/operator-actions", json=body, headers=auth).json()
    related = {a["asset_id"]: a for a in out["related_assets"]}
    assert "AST-MTR-101" in related
    assert related["AST-MTR-101"]["alarms_prior_7_days"] >= 1
    assert out["asset_context"]["manufacturer"] == "Sulzer"
    history = out["historical_pattern"]
    assert history["occurrences_prior_90_days"] >= 15
    assert history["trend_direction"] in {"increasing", "stable", "decreasing"}


def test_recommendations_chain05_active_bfp102(client, auth):
    """Postman CHAIN-05: search -> active alarms -> recommendations."""
    asset = client.get("/assets/search", params={"query": "Boiler Feed Pump 102"}, headers=auth).json()["results"][0]
    active = client.get("/alarms", params={"asset_id": asset["asset_id"], "status": "active"}, headers=auth).json()
    alarm_id = active["data"][0]["alarm_id"]
    body = {"alarm_id": alarm_id, "include_related": True}
    r = client.post("/recommendations/operator-actions", json=body, headers=auth)
    assert r.status_code == 200
    assert r.json()["alarm"]["status"] == "active"


def test_recommendations_unknown_alarm_is_404(client, auth):
    r = client.post("/recommendations/operator-actions", json={"alarm_id": "ALM-999999"}, headers=auth)
    assert r.status_code == 404


def test_recommendations_reject_unknown_fields(client, auth):
    r = client.post("/recommendations/operator-actions", json={"alarm_id": "ALM-000001", "yolo": True}, headers=auth)
    assert r.status_code == 422


# ------------------------------------------------------------ KPI calculations


def _generate(client, auth, calc_type, **filters):
    body = {"calculation_type": calc_type, "filters": {**MAY_JUL, **filters}}
    r = client.post("/calculation-code/generate", json=body, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _execute(client, auth, calculation_id, **body) -> dict:
    r = client.post("/calculation-code/execute", json={"calculation_id": calculation_id, **body}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("calc_type", CALC_TYPES)
def test_generate_returns_id_and_readable_code(client, auth, calc_type):
    gen = _generate(client, auth, calc_type, unit="Unit 3")
    assert gen["calculation_id"].startswith("calc-")
    assert gen["calculation_type"] == calc_type
    assert "def " in gen["code"]
    assert gen["executable"] is False  # shown for transparency only; never evaluated


@pytest.mark.parametrize("calc_type", CALC_TYPES)
def test_execute_each_calculation_type(client, auth, calc_type):
    gen = _generate(client, auth, calc_type, unit="Unit 4")
    r = client.post("/calculation-code/execute", json={"calculation_id": gen["calculation_id"]}, headers=auth)
    assert r.status_code == 200
    result = r.json()["result"]
    assert isinstance(result["value"], (int, float))
    assert result["unit"]
    assert result["interpretation"]


def test_generate_rejects_unknown_type(client, auth):
    body = {"calculation_type": "vibes_index", "filters": MAY_JUL}
    assert client.post("/calculation-code/generate", json=body, headers=auth).status_code == 422


def test_execute_unknown_id_is_404(client, auth):
    r = client.post("/calculation-code/execute", json={"calculation_id": "calc-doesnotexist"}, headers=auth)
    assert r.status_code == 404


def test_execute_uses_generate_filters_unless_overridden(client, auth):
    gen = _generate(client, auth, "critical_alarm_density", unit="Unit 3")
    same = client.post("/calculation-code/execute", json={"calculation_id": gen["calculation_id"]}, headers=auth).json()
    assert same["filters"]["unit"] == "Unit 3"
    body = {"calculation_id": gen["calculation_id"], "filters": {**MAY_JUL, "unit": "Unit 1"}}
    other = client.post("/calculation-code/execute", json=body, headers=auth).json()
    assert other["filters"]["unit"] == "Unit 1"


def test_critical_density_matches_summary(client, auth):
    gen = _generate(client, auth, "critical_alarm_density", unit="Unit 3")
    value = _execute(client, auth, gen["calculation_id"])["result"]["value"]
    summary = client.post(
        "/alarms/summary", json={"unit": "Unit 3", "time_range": MAY_JUL, "kpis": ["critical_count"]}, headers=auth
    ).json()
    assert value == round(summary["totals"]["critical_count"] / 61, 3)  # May + June = 61 days


def test_flood_index_positive_for_unit2_and_zero_for_unit6(client, auth):
    def run(unit):
        gen = _generate(client, auth, "alarm_flood_index", unit=unit)
        body = {"calculation_id": gen["calculation_id"]}
        return client.post("/calculation-code/execute", json=body, headers=auth).json()["result"]["value"]

    assert run("Unit 2") > 0
    assert run("Unit 6") == 0


def test_response_efficiency_for_southplant_chain07(client, auth):
    gen = _generate(client, auth, "operator_response_efficiency", site="SouthPlant")
    result = _execute(client, auth, gen["calculation_id"])["result"]
    assert 0 <= result["value"] <= 100
    assert result["details"]["acknowledged"] > 0


def test_calculation_filters_reject_inverted_range(client, auth):
    body = {
        "calculation_type": "nuisance_alarm_score",
        "filters": {"start_time": "2026-07-01T00:00:00Z", "end_time": "2026-05-01T00:00:00Z"},
    }
    assert client.post("/calculation-code/generate", json=body, headers=auth).status_code == 422
