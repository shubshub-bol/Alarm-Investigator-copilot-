"""Repo-wide pytest setup: run every `async def` test with anyio (the MCP SDK's test runner)."""

import inspect

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.anyio)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
