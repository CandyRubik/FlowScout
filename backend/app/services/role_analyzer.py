from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from ..prompts import (
    retry_user_prompt,
    role_analysis_system_prompt,
    role_analysis_user_prompt,
)
from ..schemas import (
    NeedsClarification,
    ReadyAnalysis,
    RoleAnalysisRequest,
    RoleAnalysisResponse,
    ROLE_ANALYSIS_RESPONSE_ADAPTER,
)


class LlmProvider(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        ...


class InvalidModelResponse(RuntimeError):
    """The LLM returned content that violates the application contract."""


def _normalize_json(content: str) -> str:
    normalized = content.strip()
    if not normalized.startswith("```"):
        return normalized

    lines = normalized.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return normalized

    language = lines[0].strip()[3:].strip().lower()
    if language not in {"", "json"}:
        return normalized
    return "\n".join(lines[1:-1]).strip()


def _validation_summary(error: ValidationError) -> str:
    details = []
    for item in error.errors(include_context=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "response"
        details.append(f"{location}: {item.get('type', 'invalid')}")
    return "; ".join(details) or "response does not match the schema"


def parse_model_response(content: str) -> RoleAnalysisResponse:
    normalized = _normalize_json(content)
    try:
        json.loads(normalized)
    except json.JSONDecodeError as error:
        raise InvalidModelResponse("model response is not valid JSON") from error

    try:
        return ROLE_ANALYSIS_RESPONSE_ADAPTER.validate_json(normalized)
    except ValidationError as error:
        raise InvalidModelResponse(_validation_summary(error)) from error


def _validate_semantics(
    result: RoleAnalysisResponse,
    request: RoleAnalysisRequest,
) -> RoleAnalysisResponse:
    if request.clarification_answers and isinstance(result, NeedsClarification):
        raise InvalidModelResponse(
            "model requested clarification after clarification answers were provided",
        )

    if isinstance(result, ReadyAnalysis):
        for task in result.analysis.tasks:
            if task.recommendation.value == "automate" and len(task.rationale.strip()) < 20:
                raise InvalidModelResponse(
                    "automated tasks must have a meaningful rationale",
                )
    return result


class RoleAnalysisService:
    def __init__(self, provider: LlmProvider, max_attempts: int = 2) -> None:
        self._provider = provider
        self._max_attempts = max_attempts

    def analyze(self, request: RoleAnalysisRequest) -> RoleAnalysisResponse:
        system_prompt = role_analysis_system_prompt()
        user_prompt = role_analysis_user_prompt(request)
        last_error: InvalidModelResponse | None = None

        for attempt in range(self._max_attempts):
            raw_response = self._provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
            )
            try:
                result = parse_model_response(raw_response)
                return _validate_semantics(result, request)
            except InvalidModelResponse as error:
                last_error = error
                if attempt + 1 < self._max_attempts:
                    user_prompt = retry_user_prompt(request, str(error))

        raise last_error or InvalidModelResponse("model response is invalid")
