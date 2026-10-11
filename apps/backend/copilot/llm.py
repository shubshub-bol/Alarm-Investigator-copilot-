"""Replaceable LLM provider. The copilot depends only on the LlmProvider protocol.

Two providers are implemented: Gemini (Google's SDK) and OpenRouter (one key for many vendors' models, including free
ones). 'offline' means no provider at all (rule-based understanding and template answers). Adding another vendor
means one new class with these two methods.
"""

import asyncio
import logging
import re
import time
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger("copilot.llm")
T = TypeVar("T", bound=BaseModel)

# A busy or failing server is asked again. 429 (quota, spending cap) is not: waiting seconds will not fix it.
RETRY_STATUS_CODES = [500, 502, 503, 504]
RETRY_ATTEMPTS = 3  # including the first call


class LlmError(Exception):
    """Any provider failure: network, quota, timeout, empty or invalid output."""


class LlmProvider(Protocol):
    name: str

    async def generate_json(self, system: str, prompt: str, schema: type[T]) -> T: ...

    async def generate_text(self, system: str, prompt: str, temperature: float | None = None) -> str: ...


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 45,
        temperature: float = 0.1,
        thinking_level: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        retry_delay_seconds: float = 1.0,
    ):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(
                    attempts=RETRY_ATTEMPTS,
                    initial_delay=retry_delay_seconds,  # then about twice as long before the last attempt
                    max_delay=retry_delay_seconds * 4,
                    jitter=retry_delay_seconds / 2,
                    http_status_codes=RETRY_STATUS_CODES,
                ),
                httpx_async_client=http_client,  # tests pass a fake transport; None means the SDK's own client
            ),
        )
        self.model = model
        self.name = f"gemini:{model}"
        self.temperature = temperature
        self.thinking_level = thinking_level  # MINIMAL/LOW/MEDIUM/HIGH; None = model default
        self.last_call: dict = {}

    async def _generate(self, system: str, prompt: str, temperature: float | None = None, **config: object) -> str:
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self._types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=self.temperature if temperature is None else temperature,
                    max_output_tokens=8192,  # thinking models spend output tokens on reasoning first
                    thinking_config=(
                        self._types.ThinkingConfig(thinking_level=self.thinking_level) if self.thinking_level else None
                    ),
                    **config,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises many types; callers only need "it failed"
            raise LlmError(f"{type(exc).__name__}: {exc}"[:300]) from exc
        usage = response.usage_metadata
        self.last_call = {
            "model": getattr(response, "model_version", None) or self.model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "prompt_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "thinking_tokens": getattr(usage, "thoughts_token_count", None),
        }
        log.info("llm call", extra={"fields": self.last_call})
        text = (response.text or "").strip()
        if not text:
            raise LlmError("The model returned an empty response.")
        return text

    async def generate_json(self, system: str, prompt: str, schema: type[T]) -> T:
        text = await self._generate(
            system, prompt, response_mime_type="application/json", response_json_schema=schema.model_json_schema()
        )
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LlmError(f"Model output did not match {schema.__name__}: {exc.error_count()} problem(s)") from exc

    async def generate_text(self, system: str, prompt: str, temperature: float | None = None) -> str:
        """Plain text. `temperature` overrides the provider's default for this call (higher = more varied)."""
        return await self._generate(system, prompt, temperature=temperature)


def build_provider(
    provider: str, model: str, api_key: str | None, thinking_level: str | None = None
) -> LlmProvider | None:
    if provider == "offline":
        return None
    if provider not in ("gemini", "openrouter"):
        raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini', 'openrouter' or 'offline'.")
    if not api_key or api_key == "replace-me":
        log.warning("LLM_PROVIDER=%s but LLM_API_KEY is not set; running offline", provider)
        return None
    if provider == "openrouter":
        return OpenRouterProvider(api_key=api_key, model=model, thinking_level=thinking_level)
    return GeminiProvider(api_key=api_key, model=model, thinking_level=thinking_level)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# A failing upstream provider (502), no available provider (503) or a timeout is asked again; a rate limit (429),
# missing credits (402) or a bad key (401) is not: retrying within seconds will not fix it.
OPENROUTER_RETRY_STATUS = {408, 500, 502, 503, 504}
_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$")


class OpenRouterProvider:
    """OpenRouter's OpenAI-style chat completions API: one key for many vendors' models, including free ones."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 45,
        temperature: float = 0.1,
        thinking_level: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        retry_delay_seconds: float = 1.0,
    ):
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._headers = {"Authorization": f"Bearer {api_key}", "X-Title": "Alarm Investigation Copilot"}
        self.model = model
        self.name = f"openrouter:{model}"
        self.temperature = temperature
        self.reasoning = {"effort": thinking_level.lower()} if thinking_level else None  # e.g. LOW -> low
        self.retry_delay = retry_delay_seconds
        self.last_call: dict = {}

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        """POST with retries for a busy or failing provider, waiting about 1 s and then 2 s."""
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                response = await self._client.post(OPENROUTER_URL, json=body, headers=self._headers)
            except httpx.HTTPError as exc:  # network failure or timeout
                if attempt == RETRY_ATTEMPTS:
                    raise LlmError(f"{type(exc).__name__}: {exc}"[:300]) from exc
            else:
                if response.status_code not in OPENROUTER_RETRY_STATUS or attempt == RETRY_ATTEMPTS:
                    return response
            await asyncio.sleep(self.retry_delay * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the last attempt returns or raises")

    async def _complete(self, system: str, prompt: str, temperature: float | None = None, **extra: Any) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": self.temperature if temperature is None else temperature,
            **extra,
        }
        if self.reasoning:
            body["reasoning"] = self.reasoning
        started = time.perf_counter()
        response = await self._post(body)
        try:
            data = response.json()
        except ValueError:
            data = {}
        error = data.get("error") if isinstance(data, dict) else None
        if response.status_code >= 400 or error:
            code = (error or {}).get("code", response.status_code)
            message = (error or {}).get("message") or response.text[:200]
            raise LlmError(f"OpenRouter {code}: {message}"[:300])
        try:
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("OpenRouter returned no completion.") from exc
        usage = data.get("usage") or {}
        self.last_call = {
            "model": data.get("model") or self.model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "prompt_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        }
        log.info("llm call", extra={"fields": self.last_call})
        if not text:
            raise LlmError("The model returned an empty response.")
        return text

    async def generate_json(self, system: str, prompt: str, schema: type[T]) -> T:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": False, "schema": schema.model_json_schema()},
        }
        # require_parameters: only route to providers that can follow the schema
        text = await self._complete(
            system, prompt, response_format=response_format, provider={"require_parameters": True}
        )
        try:
            return schema.model_validate_json(_FENCE.sub("", text))
        except ValidationError as exc:
            raise LlmError(f"Model output did not match {schema.__name__}: {exc.error_count()} problem(s)") from exc

    async def generate_text(self, system: str, prompt: str, temperature: float | None = None) -> str:
        """Plain text. `temperature` overrides the provider's default for this call (higher = more varied)."""
        return await self._complete(system, prompt, temperature=temperature)
