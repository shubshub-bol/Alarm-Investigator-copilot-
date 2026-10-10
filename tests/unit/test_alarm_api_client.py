"""Unit tests for the Alarm API connector, using httpx.MockTransport (no real server)."""

import json
import logging

import httpx
import pytest

from alarm_api_client import (
    AlarmApiClient,
    AuthenticationError,
    ClientSettings,
    NotFoundError,
    RequestValidationError,
    UpstreamUnavailableError,
)

SECRET = "s3cret-token-value"


class Recorder:
    """Mock transport that replays scripted responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def err(status: int, code: str, message: str = "boom", **extra) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, **extra}})


@pytest.fixture
def sleeps() -> list[float]:
    return []


def make_client(recorder: Recorder, sleeps: list[float], **overrides) -> AlarmApiClient:
    settings = ClientSettings(
        base_url="http://alarm-api.test", token=SECRET, max_retries=3, backoff_seconds=0.5, **overrides
    )

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return AlarmApiClient(settings, transport=httpx.MockTransport(recorder), sleep=fake_sleep)


# ---------------------------------------------------------------- happy path


async def test_sends_token_and_trace_headers(sleeps):
    rec = Recorder(ok({"results": []}))
    async with make_client(rec, sleeps) as client:
        await client.search_assets("pump", trace_id="trace-abc")
    req = rec.requests[0]
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert req.headers["x-trace-id"] == "trace-abc"
    assert req.headers["trace_id"] == "trace-abc"
    assert req.headers["x-client-id"] == "alarm-mcp-server"
    assert req.url.params["query"] == "pump"


async def test_success_returns_data_and_call_metadata(sleeps):
    rec = Recorder(ok({"results": [{"asset_id": "AST-1"}]}))
    async with make_client(rec, sleeps) as client:
        resp = await client.search_assets("pump", limit=5)
    assert resp.data["results"][0]["asset_id"] == "AST-1"
    assert resp.status_code == 200
    assert resp.attempts == 1
    assert resp.method == "GET"
    assert resp.path == "/assets/search"
    assert resp.trace_id.startswith("trace-")
    assert resp.duration_ms >= 0


async def test_post_sends_json_body(sleeps):
    rec = Recorder(ok({"groups": []}))
    async with make_client(rec, sleeps) as client:
        await client.alarm_summary({"asset_ids": ["AST-1"], "group_by": ["alarm_name"]})
    req = rec.requests[0]
    assert req.method == "POST"
    assert json.loads(req.content) == {"asset_ids": ["AST-1"], "group_by": ["alarm_name"]}


async def test_none_query_params_are_dropped(sleeps):
    rec = Recorder(ok({"data": [], "pagination": {"has_next": False}}))
    async with make_client(rec, sleeps) as client:
        await client.list_alarms(asset_id="AST-1", status=None)
    assert "status" not in rec.requests[0].url.params


# --------------------------------------------------------------------- retries


async def test_retries_503_then_succeeds(sleeps):
    rec = Recorder(err(503, "unavailable"), ok({"status": "ok"}))
    async with make_client(rec, sleeps) as client:
        resp = await client.health()
    assert resp.attempts == 2
    assert sleeps == [0.5]


async def test_retries_timeout_with_exponential_backoff(sleeps):
    timeout = httpx.ReadTimeout("slow")
    rec = Recorder(timeout, timeout, ok({"status": "ok"}))
    async with make_client(rec, sleeps) as client:
        resp = await client.health()
    assert resp.attempts == 3
    assert sleeps == [0.5, 1.0]


async def test_same_trace_id_is_reused_across_retries(sleeps):
    rec = Recorder(err(502, "bad_gateway"), ok({"status": "ok"}))
    async with make_client(rec, sleeps) as client:
        await client.health(trace_id="trace-keep")
    assert [r.headers["x-trace-id"] for r in rec.requests] == ["trace-keep", "trace-keep"]


async def test_gives_up_after_max_retries(sleeps):
    rec = Recorder(*[err(503, "unavailable")] * 4)
    async with make_client(rec, sleeps) as client:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await client.health()
    assert exc.value.attempts == 4  # 1 try + 3 retries
    assert exc.value.retryable is True
    assert sleeps == [0.5, 1.0, 2.0]


async def test_connection_refused_maps_to_unavailable(sleeps):
    rec = Recorder(*[httpx.ConnectError("refused")] * 4)
    async with make_client(rec, sleeps) as client:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await client.health()
    assert "could not reach" in exc.value.message.lower()


async def test_429_respects_retry_after_header(sleeps):
    limited = httpx.Response(
        429, headers={"Retry-After": "2"}, json={"error": {"code": "rate_limited", "message": "x"}}
    )
    rec = Recorder(limited, ok({"status": "ok"}))
    async with make_client(rec, sleeps) as client:
        await client.health()
    assert sleeps == [2.0]


# --------------------------------------------------------------- error mapping


async def test_404_is_not_retried_and_keeps_api_message(sleeps):
    rec = Recorder(err(404, "not_found", "Asset 'AST-X' not found.", trace_id="trace-srv"))
    async with make_client(rec, sleeps) as client:
        with pytest.raises(NotFoundError) as exc:
            await client.get_asset_metadata("AST-X")
    assert exc.value.attempts == 1
    assert exc.value.code == "not_found"
    assert "AST-X" in exc.value.message
    assert exc.value.retryable is False
    assert sleeps == []


async def test_401_maps_to_authentication_error(sleeps):
    rec = Recorder(err(401, "unauthorized"))
    async with make_client(rec, sleeps) as client:
        with pytest.raises(AuthenticationError):
            await client.health()


async def test_422_maps_to_validation_error_with_details(sleeps):
    problems = {"problems": [{"field": "body.kpis.0", "message": "bad"}]}
    rec = Recorder(err(422, "validation_error", "Request validation failed.", details=problems))
    async with make_client(rec, sleeps) as client:
        with pytest.raises(RequestValidationError) as exc:
            await client.alarm_summary({"kpis": ["nope"]})
    assert exc.value.details == problems


async def test_non_json_error_body_is_handled(sleeps):
    html = httpx.Response(502, text="<html>Bad Gateway</html>")
    rec = Recorder(*[html] * 4)
    async with make_client(rec, sleeps) as client:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await client.health()
    assert exc.value.status_code == 502


# -------------------------------------------------------------------- safety


async def test_path_ids_are_escaped(sleeps):
    rec = Recorder(err(404, "not_found"))
    async with make_client(rec, sleeps) as client:
        with pytest.raises(NotFoundError):
            await client.get_asset_metadata("../admin")
    assert rec.requests[0].url.raw_path == b"/assets/..%2Fadmin/metadata"


async def test_blank_ids_are_rejected_before_any_request(sleeps):
    rec = Recorder()
    async with make_client(rec, sleeps) as client:
        with pytest.raises(ValueError):
            await client.get_alarm("  ")
    assert rec.requests == []


def test_token_is_hidden_in_settings_repr():
    settings = ClientSettings(base_url="http://x", token=SECRET)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.model_dump())


async def test_token_never_logged(sleeps, caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(err(503, "unavailable"), ok({"status": "ok"}))
    async with make_client(rec, sleeps) as client:
        await client.health()
    assert caplog.records  # something was logged
    assert SECRET not in caplog.text


# ----------------------------------------------------------------- pagination


async def test_list_all_alarms_follows_pages(sleeps):
    def page(n, ids, has_next):
        return ok({"data": [{"alarm_id": i} for i in ids], "pagination": {"page": n, "has_next": has_next}})

    rec = Recorder(page(1, ["A", "B"], True), page(2, ["C", "D"], True), page(3, ["E"], False))
    async with make_client(rec, sleeps) as client:
        result = await client.list_all_alarms(asset_id="AST-1", page_size=2)
    assert [a["alarm_id"] for a in result.items] == ["A", "B", "C", "D", "E"]
    assert result.pages_fetched == 3
    assert result.truncated is False
    assert [r.url.params["page"] for r in rec.requests] == ["1", "2", "3"]


async def test_list_all_alarms_stops_at_max_items(sleeps):
    def page(n, has_next=True):
        return ok(
            {"data": [{"alarm_id": f"{n}-{i}"} for i in range(2)], "pagination": {"page": n, "has_next": has_next}}
        )

    rec = Recorder(page(1), page(2), page(3))
    async with make_client(rec, sleeps) as client:
        result = await client.list_all_alarms(page_size=2, max_items=3)
    assert len(result.items) == 3
    assert result.truncated is True
    assert result.pages_fetched == 2
