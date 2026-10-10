"""The real connector talking to the real simulator app (in-process via ASGI, no network)."""

import httpx
import pytest

from alarm_api_client import AlarmApiClient, AuthenticationError, ClientSettings, NotFoundError
from alarm_simulator.config import Settings
from alarm_simulator.main import create_app

TOKEN = "integration-token"


@pytest.fixture
async def client():
    app = create_app(Settings(api_token=TOKEN))
    settings = ClientSettings(base_url="http://simulator", token=TOKEN)
    async with AlarmApiClient(settings, transport=httpx.ASGITransport(app=app)) as c:
        yield c


async def test_chain_search_metadata_alarms(client):
    search = await client.search_assets("Boiler Feed Pump 101", limit=3, trace_id="trace-int-1")
    asset_id = search.data["results"][0]["asset_id"]
    meta = await client.get_asset_metadata(asset_id, trace_id="trace-int-1")
    alarms = await client.list_alarms(asset_id=asset_id, severity="high,critical", page_size=5)
    assert asset_id == "AST-BFP-101"
    assert any(r["asset_id"] == "AST-MTR-101" for r in meta.data["related_assets"])
    assert len(alarms.data["data"]) == 5
    assert search.trace_id == "trace-int-1"


async def test_list_all_alarms_against_real_pagination(client):
    result = await client.list_all_alarms(asset_id="AST-BFP-101", page_size=10)
    meta = await client.get_asset_metadata("AST-BFP-101")
    assert len(result.items) == meta.data["alarm_statistics"]["total_alarms"]
    assert result.pages_fetched > 1


async def test_real_404_is_mapped(client):
    with pytest.raises(NotFoundError) as exc:
        await client.get_alarm("ALM-999999")
    assert exc.value.trace_id  # server's trace id is carried into the error


async def test_wrong_token_against_real_server():
    app = create_app(Settings(api_token=TOKEN))
    settings = ClientSettings(base_url="http://simulator", token="wrong")
    async with AlarmApiClient(settings, transport=httpx.ASGITransport(app=app)) as c:
        with pytest.raises(AuthenticationError):
            await c.search_assets("pump")
