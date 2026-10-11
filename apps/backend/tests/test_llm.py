"""Gemini provider against a fake HTTP layer: busy-server retries and the per-call creativity setting."""

import json
from collections.abc import Callable

import httpx
import pytest

from copilot.llm import GeminiProvider, LlmError


def reply(text: str) -> httpx.Response:
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]}
    return httpx.Response(200, json=body)


def error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": reason, "status": reason}})


def provider(answer: Callable[[int], httpx.Response]) -> tuple[GeminiProvider, list[httpx.Request]]:
    """A provider whose n-th HTTP request (1-based) gets answer(n)."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(len(seen))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    llm = GeminiProvider(api_key="test-key", model="gemini-test", http_client=client, retry_delay_seconds=0.01)
    return llm, seen


async def test_a_busy_model_is_asked_again():
    llm, seen = provider(lambda n: error(503, "UNAVAILABLE") if n == 1 else reply("Hello!"))
    assert await llm.generate_text("system", "hi") == "Hello!"
    assert len(seen) == 2


async def test_retries_stop_after_three_attempts():
    llm, seen = provider(lambda n: error(503, "UNAVAILABLE"))
    with pytest.raises(LlmError, match="503"):
        await llm.generate_text("system", "hi")
    assert len(seen) == 3


async def test_a_spending_cap_is_not_retried():
    llm, seen = provider(lambda n: error(429, "RESOURCE_EXHAUSTED"))
    with pytest.raises(LlmError, match="429"):
        await llm.generate_text("system", "hi")
    assert len(seen) == 1


async def test_a_call_can_ask_for_more_variety():
    llm, seen = provider(lambda n: reply("Hi"))
    await llm.generate_text("system", "hi", temperature=0.7)
    await llm.generate_text("system", "hi")
    sent = [json.loads(r.content)["generationConfig"]["temperature"] for r in seen]
    assert sent == [0.7, 0.1]
