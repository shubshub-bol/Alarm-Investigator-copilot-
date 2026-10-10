"""Replaceable LLM provider. The copilot depends only on the LlmProvider protocol.

Gemini is the implemented provider; 'offline' means no provider at all (rule-based understanding and
template answers). Adding another vendor means one new class with these two methods.
"""

import logging
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("copilot.llm")
T = TypeVar("T", bound=BaseModel)


class LlmError(Exception):
    """Any provider failure: network, quota, timeout, empty or invalid output."""


class LlmProvider(Protocol):
    name: str

    async def generate_json(self, system: str, prompt: str, schema: type[T]) -> T: ...

    async def generate_text(self, system: str, prompt: str) -> str: ...


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 45,
        temperature: float = 0.1,
        thinking_level: str | None = None,
    ):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000))
        )
        self.model = model
        self.name = f"gemini:{model}"
        self.temperature = temperature
        self.thinking_level = thinking_level  # MINIMAL/LOW/MEDIUM/HIGH; None = model default
        self.last_call: dict = {}

    async def _generate(self, system: str, prompt: str, **config: object) -> str:
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self._types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=self.temperature,
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

    async def generate_text(self, system: str, prompt: str) -> str:
        return await self._generate(system, prompt)


def build_provider(
    provider: str, model: str, api_key: str | None, thinking_level: str | None = None
) -> LlmProvider | None:
    if provider == "offline":
        return None
    if provider == "gemini":
        if not api_key or api_key == "replace-me":
            log.warning("LLM_PROVIDER=gemini but LLM_API_KEY is not set; running offline")
            return None
        return GeminiProvider(api_key=api_key, model=model, thinking_level=thinking_level)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini' or 'offline'.")
