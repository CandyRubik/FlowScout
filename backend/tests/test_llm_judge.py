from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.main import app, get_llm_judge_service
from app.providers.deepseek import LlmStreamChunk
from app.schemas import JudgeRequest
from app.services.llm_judge import LlmJudgeService
from app.services.llm_metrics import LlmMetricsCollector


TASK = "Стоит ли автоматизировать еженедельную сверку заказов?"


class FakeJudgeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.max_tokens_calls: list[int | None] = []

    def stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, object] | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[LlmStreamChunk]:
        del response_format
        self.max_tokens_calls.append(max_tokens)
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
        )
        if "engineering expert" in system_prompt:
            agent = "engineer"
        elif "analytical expert" in system_prompt:
            agent = "analyst"
        elif "project manager" in system_prompt:
            agent = "process_pm"
        elif "final judge" in system_prompt:
            agent = "judge"
        else:
            agent = "first"

        yield LlmStreamChunk(reasoning=f"Рассуждение {agent}. ")
        yield LlmStreamChunk(content=f"Ответ {agent}.", finish_reason="stop")


class BurstJudgeProvider:
    def stream(self, **kwargs: object) -> Iterator[LlmStreamChunk]:
        del kwargs
        for char in "x" * 1024:
            yield LlmStreamChunk(content=char)


def test_judge_runs_first_agent_experts_and_final_judge() -> None:
    provider = FakeJudgeProvider()
    metrics = LlmMetricsCollector()
    service = LlmJudgeService(provider, metrics=metrics)  # type: ignore[arg-type]

    updates = list(service.stream(JudgeRequest(task=TASK)))

    stages = [update["stage"] for update in updates if update["type"] == "stage"]
    assert stages == ["first", "experts", "judge"]
    assert updates[-1]["type"] == "done"
    assert updates[-1]["answer"] == "Ответ judge."
    assert provider.max_tokens_calls == [10_000] * 5

    completed_agents = {
        update["agent"]
        for update in updates
        if update["type"] == "agent_done"
    }
    assert completed_agents == {"first", "engineer", "analyst", "process_pm"}
    assert len(provider.calls) == 5

    expert_payloads = [
        json.loads(call["user_prompt"])
        for call in provider.calls
        if "first_agent_answer" in call["user_prompt"]
        and "final judge" not in call["system_prompt"]
    ]
    assert len(expert_payloads) == 3
    assert all(payload["original_task"] == TASK for payload in expert_payloads)
    assert all(payload["first_agent_answer"] == "Ответ first." for payload in expert_payloads)
    assert {metric.agent for metric in metrics.calls} == {
        "first",
        "engineer",
        "analyst",
        "process_pm",
        "judge",
    }
    assert all(metric.success for metric in metrics.calls)


def test_judge_stream_coalesces_bursty_chunks(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_judge.monotonic", lambda: 0.0)
    service = LlmJudgeService(BurstJudgeProvider())  # type: ignore[arg-type]

    updates = list(
        service._stream_agent(
            agent="first",
            system_prompt="",
            user_prompt="",
        ),
    )

    content_updates = [update for update in updates if update["type"] == "content"]
    assert len(content_updates) == 1
    assert content_updates[0]["text"] == "x" * 1024
    assert updates[-1] == {
        "type": "complete",
        "agent": "first",
        "answer": "x" * 1024,
    }


class FakeStreamingJudgeService:
    def stream(self, request: JudgeRequest) -> Iterator[dict[str, object]]:
        yield {
            "type": "stage",
            "stage": "first",
            "agents": ["first"],
            "message": "Начинаем",
        }
        yield {"type": "reasoning", "agent": "first", "text": "Проверяю."}
        yield {"type": "done", "answer": f"Готово: {request.task}"}


def test_judge_endpoint_returns_sse_events() -> None:
    app.dependency_overrides[get_llm_judge_service] = lambda: FakeStreamingJudgeService()  # type: ignore[assignment]
    try:
        response = TestClient(app).post(
            "/api/llm-as-judge",
            json={"task": TASK},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: stage" in response.text
    assert "event: reasoning" in response.text
    assert "event: done" in response.text
    assert "Готово" in response.text


def test_judge_endpoint_validates_input() -> None:
    response = TestClient(app).post(
        "/api/llm-as-judge",
        json={"task": "short"},
    )

    assert response.status_code == 422
