"""Repo-wide pytest setup. `anyio_mode = "auto"` (pyproject.toml) runs every `async def` test with anyio, the MCP
SDK's test runner; this fixture pins it to the asyncio backend."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
