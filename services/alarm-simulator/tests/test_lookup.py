"""Part 2: asset search, asset metadata, alarm list (filters + pagination), alarm detail."""

# ---------------------------------------------------------------- asset search


def test_search_exact_name_ranks_that_asset_first(client, auth):
    r = client.get("/assets/search", params={"query": "Boiler Feed Pump 101", "limit": 5}, headers=auth)
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["asset_id"] == "AST-BFP-101"
    assert results[0]["match_score"] == 1.0


def test_search_short_name_still_finds_asset(client, auth):
    r = client.get("/assets/search", params={"query": "pump 101"}, headers=auth)
    assert r.json()["results"][0]["asset_id"] == "AST-BFP-101"


def test_search_by_type_word_returns_all_compressors(client, auth):
    r = client.get("/assets/search", params={"query": "compressor", "limit": 5}, headers=auth)
    results = r.json()["results"]
    assert len(results) >= 2
    assert all(a["asset_type"] == "compressor" for a in results)


def test_search_with_unit_filter(client, auth):
    r = client.get("/assets/search", params={"query": "motor", "unit": "Unit 5"}, headers=auth)
    results = r.json()["results"]
    assert {a["asset_id"] for a in results} == {"AST-MTR-501", "AST-MTR-502"}


def test_search_respects_limit(client, auth):
    r = client.get("/assets/search", params={"query": "pump", "limit": 2}, headers=auth)
    body = r.json()
    assert len(body["results"]) == 2
    assert body["total"] > 2


def test_search_no_match_returns_empty_list(client, auth):
    r = client.get("/assets/search", params={"query": "flux capacitor"}, headers=auth)
    assert r.status_code == 200
    assert r.json() == {"query": "flux capacitor", "total": 0, "results": []}


def test_search_without_query_is_a_validation_error(client, auth):
    r = client.get("/assets/search", headers=auth)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_search_limit_out_of_range_is_rejected(client, auth):
    r = client.get("/assets/search", params={"query": "pump", "limit": 0}, headers=auth)
    assert r.status_code == 422


def test_search_requires_token(client):
    assert client.get("/assets/search", params={"query": "pump"}).status_code == 401


# -------------------------------------------------------------- asset metadata


def test_metadata_includes_related_assets_and_alarm_stats(client, auth):
    r = client.get("/assets/AST-BFP-101/metadata", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Boiler Feed Pump 101"
    related = {a["asset_id"]: a for a in body["related_assets"]}
    assert related["AST-MTR-101"]["name"].startswith("Motor M-101")
    assert "PMP-VIB-HI" in body["alarm_codes"]
    assert body["alarm_statistics"]["total_alarms"] > 20


def test_metadata_unknown_asset_is_404(client, auth):
    r = client.get("/assets/AST-NOPE-999/metadata", headers=auth)
    assert r.status_code == 404
    assert "AST-NOPE-999" in r.json()["error"]["message"]


# ------------------------------------------------------------------ alarm list


def test_alarm_list_is_paginated(client, auth):
    params = {"asset_id": "AST-BFP-101", "page_size": 5}
    p1 = client.get("/alarms", params={**params, "page": 1}, headers=auth).json()
    p2 = client.get("/alarms", params={**params, "page": 2}, headers=auth).json()
    assert len(p1["data"]) == 5
    assert p1["pagination"]["total_items"] > 5
    assert p1["pagination"]["has_next"] is True
    assert {a["alarm_id"] for a in p1["data"]}.isdisjoint({a["alarm_id"] for a in p2["data"]})


def test_alarm_list_page_past_the_end_is_empty(client, auth):
    body = client.get("/alarms", params={"asset_id": "AST-BFP-101", "page": 999}, headers=auth).json()
    assert body["data"] == []
    assert body["pagination"]["has_next"] is False


def test_alarm_list_sort_desc_by_start_time(client, auth):
    params = {"asset_id": "AST-BFP-101", "sort_by": "start_time", "sort_order": "desc", "page_size": 50}
    times = [a["start_time"] for a in client.get("/alarms", params=params, headers=auth).json()["data"]]
    assert times == sorted(times, reverse=True)


def test_alarm_list_active_for_bfp102(client, auth):
    body = client.get("/alarms", params={"asset_id": "AST-BFP-102", "status": "active"}, headers=auth).json()
    assert len(body["data"]) == 3
    assert all(a["status"] == "active" for a in body["data"])


def test_alarm_list_site_and_status_filter(client, auth):
    params = {"site": "EastRefinery", "status": "active"}
    data = client.get("/alarms", params=params, headers=auth).json()["data"]
    assert data
    assert all(a["site"] == "EastRefinery" and a["status"] == "active" for a in data)


def test_alarm_list_severity_accepts_comma_list(client, auth):
    data = client.get("/alarms", params={"severity": "high,critical", "page_size": 200}, headers=auth).json()["data"]
    assert data
    assert {a["severity"] for a in data} <= {"high", "critical"}


def test_alarm_list_time_window_finds_unit2_flood(client, auth):
    params = {"unit": "Unit 2", "start_time": "2026-06-14T03:10:00Z", "end_time": "2026-06-14T03:25:00Z"}
    body = client.get("/alarms", params=params, headers=auth).json()
    assert body["pagination"]["total_items"] == 18


def test_alarm_list_accepts_time_without_timezone_as_utc(client, auth):
    params = {"unit": "Unit 2", "start_time": "2026-06-14T03:10:00", "end_time": "2026-06-14T03:25:00"}
    r = client.get("/alarms", params=params, headers=auth)
    assert r.status_code == 200
    assert r.json()["pagination"]["total_items"] == 18


def test_alarm_list_rejects_start_after_end(client, auth):
    params = {"start_time": "2026-06-01T00:00:00Z", "end_time": "2026-05-01T00:00:00Z"}
    r = client.get("/alarms", params=params, headers=auth)
    assert r.status_code == 422


def test_alarm_list_rejects_unknown_sort_field(client, auth):
    r = client.get("/alarms", params={"sort_by": "password"}, headers=auth)
    assert r.status_code == 422


def test_alarm_list_rejects_unknown_severity(client, auth):
    r = client.get("/alarms", params={"severity": "apocalyptic"}, headers=auth)
    assert r.status_code == 422


def test_alarm_list_unknown_asset_is_404(client, auth):
    r = client.get("/alarms", params={"asset_id": "AST-NOPE-999"}, headers=auth)
    assert r.status_code == 404


# ---------------------------------------------------------------- alarm detail


def test_alarm_detail_round_trips_from_list(client, auth):
    first = client.get("/alarms", params={"asset_id": "AST-BFP-101", "page_size": 1}, headers=auth).json()["data"][0]
    r = client.get(f"/alarms/{first['alarm_id']}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["alarm_id"] == first["alarm_id"]
    assert body["asset"]["asset_id"] == "AST-BFP-101"
    assert body["ack_delay_minutes"] is None or body["ack_delay_minutes"] >= 0


def test_alarm_detail_active_alarm_has_running_duration(client, auth):
    active = client.get("/alarms", params={"asset_id": "AST-BFP-102", "status": "active"}, headers=auth).json()["data"]
    body = client.get(f"/alarms/{active[0]['alarm_id']}", headers=auth).json()
    assert body["end_time"] is None
    assert body["duration_minutes"] > 0  # measured up to the simulator's reference "now"


def test_alarm_detail_unknown_is_404(client, auth):
    r = client.get("/alarms/ALM-999999", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
