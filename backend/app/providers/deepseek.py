from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import os
from typing import Any, Literal

from openai import OpenAI


class LlmConfigurationError(RuntimeError):
    """The provider cannot be called because local configuration is missing."""


class LlmRequestError(RuntimeError):
    """The provider rejected or failed to complete a request."""


class LlmEmptyStreamError(LlmRequestError):
    """The provider finished a stream without a visible answer."""

    def __init__(self, finish_reason: str | None = None) -> None:
        reason = f" (finish_reason={finish_reason})" if finish_reason else ""
        super().__init__(f"DeepSeek returned an empty streaming response{reason}")
        self.finish_reason = finish_reason


DEFAULT_MAX_TOKENS = 2_000
DEFAULT_REASONING_EFFORT = "high"
ThinkingType = Literal["enabled", "disabled"]
ReasoningEffort = Literal["low", "high", "max"]


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Token usage returned by the DeepSeek API."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    reasoning_tokens: int = 0

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def from_api(cls, value: Any) -> LlmUsage | None:
        if value is None:
            return None

        details = _read_value(value, "completion_tokens_details")
        prompt_tokens = cls._as_int(_read_value(value, "prompt_tokens"))
        cache_hit_tokens = cls._as_int(
            _read_value(value, "prompt_cache_hit_tokens"),
        )
        cache_miss_tokens = cls._as_int(
            _read_value(value, "prompt_cache_miss_tokens"),
        )
        if prompt_tokens and not cache_hit_tokens and not cache_miss_tokens:
            # Older-compatible responses may expose only prompt_tokens. Treat
            # those tokens as a miss instead of silently reporting zero cost.
            cache_miss_tokens = prompt_tokens

        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=cls._as_int(
                _read_value(value, "completion_tokens"),
            ),
            total_tokens=cls._as_int(_read_value(value, "total_tokens")),
            prompt_cache_hit_tokens=cache_hit_tokens,
            prompt_cache_miss_tokens=cache_miss_tokens,
            reasoning_tokens=cls._as_int(
                _read_value(details, "reasoning_tokens"),
            ),
        )

    def __add__(self, other: LlmUsage) -> LlmUsage:
        return LlmUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            prompt_cache_hit_tokens=(
                self.prompt_cache_hit_tokens + other.prompt_cache_hit_tokens
            ),
            prompt_cache_miss_tokens=(
                self.prompt_cache_miss_tokens + other.prompt_cache_miss_tokens
            ),
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


def _read_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(frozen=True, slots=True)
class LlmStreamChunk:
    reasoning: str = ""
    content: str = ""
    finish_reason: str | None = None
    status: str = ""
    usage: LlmUsage | None = None


class DeepSeekProvider:
    def __init__(
        self,
        client: OpenAI | None = None,
        *,
        model: str | None = None,
        thinking_type: ThinkingType | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        user_id: str | None = None,
        retry_without_thinking: bool = True,
    ) -> None:
        self._client = client
        self._model = model
        self._thinking_type = thinking_type
        self._reasoning_effort = reasoning_effort
        self._user_id = user_id
        self._retry_without_thinking = retry_without_thinking

    def _get_client(self) -> OpenAI:
        if self._client is not None:
            return self._client

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise LlmConfigurationError("DEEPSEEK_API_KEY is not configured")

        return OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

    def _build_request(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
        thinking_type: ThinkingType,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        stream: bool = False,
    ) -> dict[str, Any]:
        extra_body: dict[str, Any] = {
            "thinking": {"type": thinking_type},
        }
        if self._user_id:
            extra_body["user_id"] = self._user_id

        request: dict[str, Any] = {
            "model": self._model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "stream": stream,
            "extra_body": extra_body,
        }
        if thinking_type == "enabled":
            request["reasoning_effort"] = self._reasoning_effort or os.getenv(
                "DEEPSEEK_REASONING_EFFORT",
                DEFAULT_REASONING_EFFORT,
            )
        if stream:
            request["stream_options"] = {"include_usage": True}
        if response_format is not None:
            request["response_format"] = response_format
        return request

    def _request_completion(self, request: dict[str, Any]) -> Any:
        try:
            return self._get_client().chat.completions.create(**request)
        except LlmConfigurationError:
            raise
        except Exception as error:
            raise LlmRequestError("DeepSeek request failed") from error

    @staticmethod
    def _extract_content(response: Any) -> tuple[str, str | None]:
        if not response.choices:
            raise LlmRequestError("DeepSeek returned no choices")

        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        return content, getattr(choice, "finish_reason", None)

    @classmethod
    def _extract_stream_chunk(cls, chunk: Any) -> LlmStreamChunk:
        usage = LlmUsage.from_api(_read_value(chunk, "usage"))
        choices = _read_value(chunk, "choices", []) or []
        if not choices:
            return LlmStreamChunk(usage=usage)

        choice = choices[0]
        delta = _read_value(choice, "delta")
        if delta is None:
            return LlmStreamChunk(
                finish_reason=_read_value(choice, "finish_reason"),
                usage=usage,
            )

        reasoning = _read_value(delta, "reasoning_content", "")
        if not reasoning:
            reasoning = _read_value(delta, "reasoning", "")
        content = _read_value(delta, "content", "")
        return LlmStreamChunk(
            reasoning=reasoning if isinstance(reasoning, str) else str(reasoning or ""),
            content=content if isinstance(content, str) else str(content or ""),
            finish_reason=_read_value(choice, "finish_reason"),
            usage=usage,
        )

    def _stream_once(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
        thinking_type: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[LlmStreamChunk]:
        request = self._build_request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
            thinking_type=thinking_type,
            max_tokens=max_tokens,
            stream=True,
        )

        try:
            response = self._get_client().chat.completions.create(**request)
            saw_content = False
            finish_reason: str | None = None
            for chunk in response:
                parsed = self._extract_stream_chunk(chunk)
                saw_content = saw_content or bool(parsed.content)
                finish_reason = parsed.finish_reason or finish_reason
                if (
                    parsed.reasoning
                    or parsed.content
                    or parsed.finish_reason
                    or parsed.usage is not None
                ):
                    yield parsed
        except LlmConfigurationError:
            raise
        except LlmEmptyStreamError:
            raise
        except Exception as error:
            raise LlmRequestError("DeepSeek streaming request failed") from error

        if not saw_content:
            raise LlmEmptyStreamError(finish_reason)

    def stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[LlmStreamChunk]:
        thinking_type = self._thinking_type or "enabled"
        if thinking_type == "disabled":
            yield from self._stream_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
                thinking_type="disabled",
                max_tokens=max_tokens,
            )
            return

        try:
            yield from self._stream_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
                thinking_type="enabled",
                max_tokens=max_tokens,
            )
        except LlmEmptyStreamError as error:
            if (
                not self._retry_without_thinking
                or error.finish_reason == "content_filter"
            ):
                raise

            yield LlmStreamChunk(
                status=(
                    "Финальный ответ не пришёл в thinking-режиме; "
                    "повторяем без thinking…"
                ),
            )
            yield from self._stream_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
                thinking_type="disabled",
                max_tokens=max_tokens,
            )

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        thinking_type = self._thinking_type or "enabled"
        request = self._build_request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
            thinking_type=thinking_type,
            max_tokens=max_tokens,
        )
        response = self._request_completion(request)
        content, finish_reason = self._extract_content(response)
        if content:
            return content

        # DeepSeek may return an empty content field in JSON/thinking mode.
        # Retry without thinking so the structured response still has a chance
        # to complete. Do not retry content-filtered requests.
        if (
            thinking_type == "enabled"
            and self._retry_without_thinking
            and finish_reason != "content_filter"
        ):
            fallback_request = self._build_request(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
                thinking_type="disabled",
                max_tokens=max_tokens,
            )
            fallback_response = self._request_completion(fallback_request)
            fallback_content, fallback_finish_reason = self._extract_content(
                fallback_response,
            )
            if fallback_content:
                return fallback_content
            finish_reason = fallback_finish_reason or finish_reason

        reason = f" (finish_reason={finish_reason})" if finish_reason else ""
        raise LlmRequestError(f"DeepSeek returned an empty response{reason}")
