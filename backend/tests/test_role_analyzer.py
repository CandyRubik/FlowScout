from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_reasoning_stream_service, get_role_analysis_service
from app.providers.deepseek import LlmStreamChunk
from app.prompts import role_analysis_system_prompt
from app.schemas import RoleAnalysisRequest
from app.services.reasoning_stream import ReasoningStreamService
from app.services.role_analyzer import (
    InvalidModelResponse,
    RoleAnalysisService,
    parse_model_response,
)


ROLE_DESCRIPTION = "Операционный менеджер контролирует заказы и готовит отчёты."
AMBIGUOUS_ROLE_DESCRIPTION = (
    "Я HR-специалист. Иногда автоматически отказываю кандидатам и отправляю "
    "им офферы, но правила зависят от руководителя."
)


def ready_response() -> str:
    return json.dumps(
        {
            "status": "ready",
            "questions": [],
            "analysis": {
                "role_title": "Операционный менеджер",
                "role_summary": "Контролирует исполнение заказов и готовит отчётность.",
                "tasks": [
                    {
                        "title": "Контролировать заказы",
                        "description": "Проверять состояние заказов и отклонения.",
                        "recommendation": "automate",
                        "rationale": "Задача повторяется и имеет понятные входные данные.",
                        "assumptions": [],
                    },
                ],
                "global_assumptions": [],
            },
        },
        ensure_ascii=False,
    )


class FakeProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeStreamingProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def stream(self, **kwargs: object):
        yield LlmStreamChunk(reasoning="Проверяю критерии. ")
        yield LlmStreamChunk(content=self.response)


class QueuedStreamingProvider:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    def stream(self, **kwargs: object):
        yield LlmStreamChunk(reasoning="Проверяю критерии. ")
        yield LlmStreamChunk(content=self.responses.pop(0))


def test_parse_ready_response() -> None:
    result = parse_model_response(ready_response())

    assert result.status == "ready"
    assert result.analysis.tasks[0].recommendation.value == "automate"


def test_parse_clarification_response() -> None:
    result = parse_model_response(
        json.dumps(
            {
                "status": "needs_clarification",
                "questions": ["Требует ли задача обязательного согласования?"],
                "analysis": None,
            },
            ensure_ascii=False,
        ),
    )

    assert result.status == "needs_clarification"
    assert len(result.questions) == 1


def test_system_prompt_uses_calibrated_clarification_gate() -> None:
    prompt = role_analysis_system_prompt()

    assert "clarification is a last resort" in prompt.lower()
    assert "missing information is not automatically ambiguity" in prompt.lower()
    assert "fully design a" in prompt.lower()
    assert "defer technical implementation questions" in prompt.lower()
    assert "same recommendation remains safe" in prompt.lower()
    assert "ask one concise, specific question by default" in prompt.lower()
    assert "candidate rejection" in prompt.lower()


def test_service_accepts_clarification_for_ambiguous_role() -> None:
    response = json.dumps(
        {
            "status": "needs_clarification",
            "questions": [
                "Кто утверждает решение об отказе кандидату?",
                "Нужна ли ручная проверка письма об отказе?",
            ],
            "analysis": None,
        },
        ensure_ascii=False,
    )
    service = RoleAnalysisService(FakeProvider(response), max_attempts=1)

    result = service.analyze(
        RoleAnalysisRequest(role_description=AMBIGUOUS_ROLE_DESCRIPTION),
    )

    assert result.status == "needs_clarification"
    assert len(result.questions) == 2


def test_parse_json_code_fence() -> None:
    result = parse_model_response(f"```json\n{ready_response()}\n```")

    assert result.status == "ready"


def test_parse_rejects_unknown_fields() -> None:
    payload = json.loads(ready_response())
    payload["execute_now"] = True

    with pytest.raises(InvalidModelResponse):
        parse_model_response(json.dumps(payload))


def test_parse_rejects_duplicate_task_titles() -> None:
    payload = json.loads(ready_response())
    task = payload["analysis"]["tasks"][0]
    payload["analysis"]["tasks"].append({**task, "description": "Дублирующая задача."})

    with pytest.raises(InvalidModelResponse):
        parse_model_response(json.dumps(payload))


def test_service_retries_once_after_invalid_response() -> None:
    provider = FakeProvider("not json", ready_response())
    service = RoleAnalysisService(provider)

    result = service.analyze(RoleAnalysisRequest(role_description=ROLE_DESCRIPTION))

    assert result.status == "ready"
    assert len(provider.calls) == 2
    assert "Validation errors" in str(provider.calls[1]["user_prompt"])


def test_service_does_not_ask_again_after_clarifications() -> None:
    clarification = json.dumps(
        {
            "status": "needs_clarification",
            "questions": ["Требует ли задача обязательного согласования?"],
            "analysis": None,
        },
    )
    provider = FakeProvider(clarification)
    service = RoleAnalysisService(provider, max_attempts=1)
    request = RoleAnalysisRequest(
        role_description=ROLE_DESCRIPTION,
        clarification_answers=[
            {
                "question": "Требует ли задача обязательного согласования?",
                "answer": "Да",
            },
        ],
    )

    with pytest.raises(InvalidModelResponse, match="after clarification answers"):
        service.analyze(request)


def test_role_analysis_endpoint_returns_structured_response() -> None:
    service = RoleAnalysisService(FakeProvider(ready_response()))
    app.dependency_overrides[get_role_analysis_service] = lambda: service
    try:
        response = TestClient(app).post(
            "/api/role-analysis",
            json={"role_description": ROLE_DESCRIPTION},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["analysis"]["tasks"][0]["recommendation"] == "automate"


def test_role_analysis_endpoint_validates_input() -> None:
    response = TestClient(app).post(
        "/api/role-analysis",
        json={"role_description": "short"},
    )

    assert response.status_code == 422


def test_role_analysis_stream_endpoint_returns_sse_events() -> None:
    provider = FakeStreamingProvider(ready_response())
    stream_service = ReasoningStreamService(provider, max_attempts=1)
    app.dependency_overrides[get_reasoning_stream_service] = lambda: stream_service
    try:
        response = TestClient(app).post(
            "/api/role-analysis?stream=true",
            json={"role_description": ROLE_DESCRIPTION},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: reasoning" in response.text
    assert "Проверяю критерии." in response.text
    assert "event: done" in response.text


def test_step_by_step_endpoint_uses_streaming_route() -> None:
    provider = FakeStreamingProvider(ready_response())
    stream_service = ReasoningStreamService(provider, max_attempts=1)
    app.dependency_overrides[get_reasoning_stream_service] = lambda: stream_service
    try:
        response = TestClient(app).post(
            "/api/reasoning/step-by-step",
            json={"task": ROLE_DESCRIPTION},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "event: reasoning" in response.text
    assert "event: done" in response.text


def test_prompt_generated_endpoint_streams_prompt_then_solution() -> None:
    provider = QueuedStreamingProvider(
        "Сначала проверь повторяемость задачи.",
        ready_response(),
    )
    stream_service = ReasoningStreamService(provider, max_attempts=1)
    app.dependency_overrides[get_reasoning_stream_service] = lambda: stream_service
    try:
        response = TestClient(app).post(
            "/api/reasoning/prompt-generated",
            json={"task": ROLE_DESCRIPTION},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "event: prompt" in response.text
    assert "event: prompt_ready" in response.text
    assert "event: done" in response.text


def test_expert_endpoint_returns_each_expert_result() -> None:
    provider = QueuedStreamingProvider(ready_response(), ready_response(), ready_response())
    stream_service = ReasoningStreamService(provider, max_attempts=1)
    app.dependency_overrides[get_reasoning_stream_service] = lambda: stream_service
    try:
        response = TestClient(app).post(
            "/api/reasoning/experts",
            json={"task": ROLE_DESCRIPTION},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.text.count("event: expert_done") == 3
    assert "event: done" in response.text
