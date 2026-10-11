"""OpenRouter provider against a fake HTTP layer: requests, JSON answers, retries and errors."""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel

from copilot.llm import LlmError, OpenRouterProvider, build_provider

MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


class Verdict(BaseModel):
    intent: str
    confidence: float = 0.5


def completion(content: str | None) -> httpx.Response:
    body = {
        "id": "gen-1",
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }
    return httpx.Response(200, json=body)


def error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": message}})


def provider(
    answer: Callable[[int], httpx.Response], thinking_level: str | None = "LOW"
) -> tuple[OpenRouterProvider, list[httpx.Request]]:
    """A provider whose n-th HTTP request (1-based) gets answer(n)."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(len(seen))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    llm = OpenRouterProvider(
        api_key="or-test-key", model=MODEL, thinking_level=thinking_level, http_client=client, retry_delay_seconds=0.01
    )
    return llm, seen


async def test_text_is_a_chat_completion_with_the_key_the_model_and_low_thinking():
    llm, seen = provider(lambda n: completion("  Hello there.  "))
    assert await llm.generate_text("be brief", "hi", temperature=0.7) == "Hello there."
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer or-test-key"
    assert body["model"] == MODEL
    assert body["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.7
    assert body["reasoning"] == {"effort": "low"}
    assert llm.name == f"openrouter:{MODEL}"


async def test_json_answers_ask_for_the_schema_and_are_validated():
    llm, seen = provider(lambda n: completion('```json\n{"intent": "active_alarms", "confidence": 0.9}\n```'))
    verdict = await llm.generate_json("classify", "Show active alarms", Verdict)
    assert verdict == Verdict(intent="active_alarms", confidence=0.9)
    body = json.loads(seen[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == Verdict.model_json_schema()
    assert body["provider"] == {"require_parameters": True}  # only providers that can follow a schema


async def test_json_that_does_not_fit_the_schema_is_an_error():
    llm, _ = provider(lambda n: completion('{"confidence": "high"}'))
    with pytest.raises(LlmError, match="did not match Verdict"):
        await llm.generate_json("classify", "Show active alarms", Verdict)


@pytest.mark.parametrize("status", [502, 503])
async def test_a_failing_provider_is_asked_again(status):
    llm, seen = provider(lambda n: error(status, "Provider returned error") if n == 1 else completion("OK"))
    assert await llm.generate_text("system", "hi") == "OK"
    assert len(seen) == 2


async def test_retries_stop_after_three_attempts():
    llm, seen = provider(lambda n: error(503, "No available provider"))
    with pytest.raises(LlmError, match="503"):
        await llm.generate_text("system", "hi")
    assert len(seen) == 3


@pytest.mark.parametrize(
    ("status", "message"), [(429, "Rate limit exceeded"), (402, "Insufficient credits"), (401, "Invalid API key")]
)
async def test_limits_credits_and_keys_are_not_retried(status, message):
    llm, seen = provider(lambda n: error(status, message))
    with pytest.raises(LlmError, match=f"{status}.*{message}"):
        await llm.generate_text("system", "hi")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [completion(None), completion("   "), httpx.Response(200, json={"error": {"code": 502, "message": "Upstream"}})],
    ids=["no-content", "blank", "error-in-body"],
)
async def test_an_empty_or_failed_completion_is_an_error(response):
    llm, _ = provider(lambda n: response)
    with pytest.raises(LlmError):
        await llm.generate_text("system", "hi")


async def test_no_thinking_level_sends_no_reasoning_setting():
    llm, seen = provider(lambda n: completion("OK"), thinking_level=None)
    await llm.generate_text("system", "hi")
    assert "reasoning" not in json.loads(seen[0].content)


def test_build_provider_picks_openrouter_and_needs_a_real_key():
    assert isinstance(build_provider("openrouter", MODEL, "or-test-key"), OpenRouterProvider)
    assert build_provider("openrouter", MODEL, "replace-me") is None
    assert build_provider("openrouter", MODEL, None) is None
