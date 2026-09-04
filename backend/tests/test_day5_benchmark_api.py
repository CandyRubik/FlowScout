from __future__ import annotations

from dataclasses import asdict

from fastapi.testclient import TestClient

from app.main import app
from benchmarks.day5_model_versions import VARIANTS


VALID_ANSWER = (
    '{"summary":"Итог","rating":"Корректен","why":"Причина",'
    '"improve":"Не требуется","tasks":[{"title":"Проверить данные",'
    '"description":"Сверить заказ","recommendation":"automate",'
    '"rationale":"Повторяемая операция с понятными данными",'
    '"assumptions":[]}]}'
)


def test_day5_benchmark_endpoint_streams_all_variant_results(monkeypatch) -> None:
    def fake_run_once(
        variant,
        task: str,
        *,
        phase: str,
        repetition: int,
        cache_isolation: bool,
        pricing_override: str = "auto",
        on_update=None,
    ) -> dict[str, object]:
        assert task == "Проверить еженедельный процесс роли"
        assert phase == "measurement"
        assert repetition == 1
        assert cache_isolation is True
        assert pricing_override == "auto"
        if on_update:
            on_update(
                {
                    "type": "stage",
                    "stage": "first",
                    "message": "Начинаем",
                },
            )
            on_update({"type": "agent_done", "agent": "first"})

        return {
            "variant": asdict(variant),
            "success": True,
            "final_answer_received": True,
            "all_calls_success": True,
            "call_failures": [],
            "error_type": None,
            "duration_ms": 100.0,
            "stage_durations_ms": {
                "first": 20.0,
                "experts": 50.0,
                "judge": 30.0,
            },
            "request_count": 5,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 10,
                "reasoning_tokens": 5,
            },
            "cost_usd": 0.0001,
            "automatic_quality": {
                "json_valid": True,
                "contract_valid": True,
                "errors": [],
            },
            "final_answer": VALID_ANSWER,
        }

    monkeypatch.setattr("app.main.run_once", fake_run_once)

    response = TestClient(app).post(
        "/api/day5-benchmark",
        json={"task": "Проверить еженедельный процесс роли"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: variant_start") == len(VARIANTS)
    assert response.text.count("event: variant_result") == len(VARIANTS)
    assert "event: benchmark_start" in response.text
    assert "event: variant_stage" in response.text
    assert "event: benchmark_done" in response.text
    assert "deepseek-v4-pro" in response.text
