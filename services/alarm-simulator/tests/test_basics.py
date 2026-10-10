"""Part 1: health, authentication, trace propagation, error envelope."""


def test_health_needs_no_token_and_reports_data_counts(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["assets"] > 0
    assert body["data"]["alarms"] > 0
    assert body["reference_now"] == "2026-07-01T00:00:00Z"


def test_missing_token_is_rejected_with_error_envelope(client):
    r = client.get("/analytics/kpi-definitions")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "unauthorized"
    assert err["message"]
    assert err["trace_id"]


def test_wrong_token_is_rejected(client):
    r = client.get("/analytics/kpi-definitions", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_non_bearer_scheme_is_rejected(client):
    r = client.get("/analytics/kpi-definitions", headers={"Authorization": "Basic dGVzdA=="})
    assert r.status_code == 401


def test_valid_token_is_accepted(client, auth):
    r = client.get("/analytics/kpi-definitions", headers=auth)
    assert r.status_code == 200
    names = {k["name"] for k in r.json()["kpis"]}
    assert {"alarm_count", "recurring_rate", "avg_ack_delay"} <= names


def test_trace_id_header_is_echoed_back(client, auth):
    # Postman sends the header literally as "trace_id".
    r = client.get("/analytics/kpi-definitions", headers={**auth, "trace_id": "trace-abc-123"})
    assert r.headers["x-trace-id"] == "trace-abc-123"


def test_x_trace_id_header_is_also_accepted(client, auth):
    r = client.get("/analytics/kpi-definitions", headers={**auth, "x-trace-id": "trace-xyz"})
    assert r.headers["x-trace-id"] == "trace-xyz"


def test_trace_id_is_generated_when_missing(client):
    r = client.get("/health")
    assert r.headers["x-trace-id"].startswith("trace-")


def test_trace_id_appears_in_error_body(client):
    r = client.get("/analytics/kpi-definitions", headers={"trace_id": "trace-err-1"})
    assert r.json()["error"]["trace_id"] == "trace-err-1"


def test_unknown_route_uses_same_error_envelope(client, auth):
    r = client.get("/does-not-exist", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
