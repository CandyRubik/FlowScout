from __future__ import annotations

import os
from typing import Any

from openai import OpenAI


class LlmConfigurationError(RuntimeError):
    """The provider cannot be called because local configuration is missing."""


class LlmRequestError(RuntimeError):
    """The provider rejected or failed to complete a request."""


DEFAULT_MAX_TOKENS = 2_000
DEFAULT_REASONING_EFFORT = "high"


class DeepSeekProvider:
    def __init__(self, client: OpenAI | None = None) -> None:
        self._client = client

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
        thinking_type: str,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": DEFAULT_MAX_TOKENS,
            "stream": False,
            "extra_body": {"thinking": {"type": thinking_type}},
        }
        if thinking_type == "enabled":
            request["reasoning_effort"] = DEFAULT_REASONING_EFFORT
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

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        request = self._build_request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
            thinking_type="enabled",
        )
        response = self._request_completion(request)
        content, finish_reason = self._extract_content(response)
        if content:
            return content

        # DeepSeek may return an empty content field in JSON/thinking mode.
        # Retry without thinking so the structured response still has a chance
        # to complete. Do not retry content-filtered requests.
        if finish_reason != "content_filter":
            fallback_request = self._build_request(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
                thinking_type="disabled",
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
