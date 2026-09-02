from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..prompts import (
    retry_user_prompt,
    role_analysis_system_prompt,
    role_analysis_user_prompt,
)
from ..providers.deepseek import LlmStreamChunk
from ..schemas import RoleAnalysisRequest
from .role_analyzer import (
    InvalidModelResponse,
    LlmProvider,
    _validate_semantics,
    parse_model_response,
)


StreamUpdate = dict[str, Any]


class StreamingLlmProvider(LlmProvider):
    def stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> Iterator[LlmStreamChunk]:
        ...


class ReasoningStreamService:
    def __init__(self, provider: StreamingLlmProvider, max_attempts: int = 2) -> None:
        self._provider = provider
        self._max_attempts = max_attempts

    def stream_raw(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> Iterator[StreamUpdate]:
        content_parts: list[str] = []
        for chunk in self._provider.stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=response_format,
        ):
            if chunk.status:
                yield {"type": "status", "message": chunk.status}
            if chunk.reasoning:
                yield {"type": "reasoning", "text": chunk.reasoning}
            if chunk.content:
                content_parts.append(chunk.content)
                yield {"type": "content", "text": chunk.content}

        yield {"type": "complete", "content": "".join(content_parts)}

    def stream_role_analysis(
        self,
        request: RoleAnalysisRequest,
        *,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
    ) -> Iterator[StreamUpdate]:
        resolved_system_prompt = system_prompt or role_analysis_system_prompt()
        resolved_user_prompt = user_prompt or role_analysis_user_prompt(request)
        last_error: InvalidModelResponse | None = None

        for attempt in range(self._max_attempts):
            raw_response = ""
            for update in self.stream_raw(
                system_prompt=resolved_system_prompt,
                user_prompt=resolved_user_prompt,
                response_format={"type": "json_object"},
            ):
                if update["type"] == "complete":
                    raw_response = str(update["content"])
                else:
                    yield update

            try:
                result = _validate_semantics(
                    parse_model_response(raw_response),
                    request,
                )
                yield {
                    "type": "done",
                    "result": result.model_dump(mode="json"),
                }
                return
            except InvalidModelResponse as error:
                last_error = error
                if attempt + 1 < self._max_attempts:
                    yield {
                        "type": "status",
                        "message": "Ответ не прошёл проверку, повторяем запрос…",
                    }
                    resolved_user_prompt = retry_user_prompt(request, str(error))

        raise last_error or InvalidModelResponse("model response is invalid")
