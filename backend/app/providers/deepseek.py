from __future__ import annotations

import os
from typing import Any

from openai import OpenAI


class LlmConfigurationError(RuntimeError):
    """The provider cannot be called because local configuration is missing."""


class LlmRequestError(RuntimeError):
    """The provider rejected or failed to complete a request."""


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

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            request["response_format"] = response_format

        try:
            response = self._get_client().chat.completions.create(**request)
        except LlmConfigurationError:
            raise
        except Exception as error:
            raise LlmRequestError("DeepSeek request failed") from error

        if not response.choices:
            raise LlmRequestError("DeepSeek returned no choices")

        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise LlmRequestError("DeepSeek returned an empty response")
        return content
