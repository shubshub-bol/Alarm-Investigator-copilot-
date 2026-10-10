import pytest
from fastapi.testclient import TestClient

from alarm_simulator.config import Settings
from alarm_simulator.main import create_app

TEST_TOKEN = "test-token"


@pytest.fixture
def client() -> TestClient:
    app = create_app(Settings(api_token=TEST_TOKEN))
    return TestClient(app)


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
