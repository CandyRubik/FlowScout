from __future__ import annotations

import json

from app.providers.deepseek import LlmStreamChunk
from app.schemas import RoleAnalysisRequest
from app.services.reasoning_stream import ReasoningStreamService


def ready_response() -> str:
    return json.dumps(
        {
            "status": "ready",
            "questions": [],
            "analysis": {
                "role_title": "Операционный менеджер",
                "role_summary": "Формирует отчётность по заказам.",
                "tasks": [
                    {
                        "title": "Формировать отчёт",
                        "description": "Собирать данные и готовить сводку.",
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


class FakeStreamingProvider:
    def __init__(self, *streams: list[LlmStreamChunk]) -> None:
        self.streams = list(streams)
        self.calls: list[dict[str, object]] = []

    def stream(self, **kwargs: object):
        self.calls.append(kwargs)
        yield from self.streams.pop(0)


def test_streaming_service_exposes_reasoning_and_validated_result() -> None:
    response = ready_response()
    provider = FakeStreamingProvider(
        [
            LlmStreamChunk(reasoning="Проверяю повторяемость. "),
            LlmStreamChunk(content=response[:40]),
            LlmStreamChunk(content=response[40:]),
        ],
    )
    service = ReasoningStreamService(provider, max_attempts=1)

    updates = list(
        service.stream_role_analysis(
            RoleAnalysisRequest(
                role_description="Каждую пятницу формировать отчёт по заказам.",
            ),
        ),
    )

    assert [update["type"] for update in updates] == [
        "reasoning",
        "content",
        "content",
        "done",
    ]
    assert updates[-1]["result"]["status"] == "ready"
    assert provider.calls[0]["response_format"] == {"type": "json_object"}
