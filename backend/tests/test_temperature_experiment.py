from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, get_temperature_experiment_service
from app.schemas import (
    TEMPERATURE_VALUES,
    TemperatureExperimentRequest,
    TemperatureExperimentResponse,
    TemperatureExperimentResult,
)
from app.services.temperature_experiment import TemperatureExperimentService


TASK = "Операционный менеджер сверяет заказы и готовит еженедельный отчёт."


class FakeTemperatureProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
            },
        )
        return f"Ответ при temperature={temperature}"


def test_experiment_uses_same_prompt_and_all_temperatures() -> None:
    provider = FakeTemperatureProvider()
    service = TemperatureExperimentService(provider)

    response = service.run(TemperatureExperimentRequest(task=TASK))

    assert response.task == TASK
    assert [result.temperature for result in response.results] == list(TEMPERATURE_VALUES)
    assert [result.answer for result in response.results] == [
        "Ответ при temperature=0.0",
        "Ответ при temperature=0.7",
        "Ответ при temperature=1.2",
    ]
    assert len(provider.calls) == len(TEMPERATURE_VALUES)
    assert len({call["system_prompt"] for call in provider.calls}) == 1
    assert len({call["user_prompt"] for call in provider.calls}) == 1
    assert {call["temperature"] for call in provider.calls} == set(TEMPERATURE_VALUES)


class FakeTemperatureService:
    def run(self, request: TemperatureExperimentRequest) -> TemperatureExperimentResponse:
        return TemperatureExperimentResponse(
            task=request.task,
            results=[
                TemperatureExperimentResult(
                    temperature=temperature,
                    answer=f"Вариант {temperature}",
                )
                for temperature in TEMPERATURE_VALUES
            ],
        )


def test_temperature_experiment_endpoint_returns_all_results() -> None:
    app.dependency_overrides[get_temperature_experiment_service] = (
        lambda: FakeTemperatureService()  # type: ignore[assignment]
    )
    try:
        response = TestClient(app).post(
            "/api/temperature-experiment",
            json={"task": TASK},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"] == TASK
    assert [item["temperature"] for item in payload["results"]] == list(
        TEMPERATURE_VALUES,
    )


def test_temperature_experiment_endpoint_validates_input() -> None:
    response = TestClient(app).post(
        "/api/temperature-experiment",
        json={"task": "short"},
    )

    assert response.status_code == 422
