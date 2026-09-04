from __future__ import annotations

import pytest

from app.providers.deepseek import LlmStreamChunk, LlmUsage
from app.services.llm_metrics import LlmMetricsCollector


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.1
        return current


def test_metrics_collect_first_tokens_usage_and_retries() -> None:
    clock = FakeClock()
    collector = LlmMetricsCollector(clock=clock)
    recorder = collector.start("judge")

    recorder.observe(LlmStreamChunk(reasoning="Думаю."))
    recorder.observe(
        LlmStreamChunk(
            content="Ответ.",
            finish_reason="stop",
            status="Повторяем без thinking…",
            usage=LlmUsage(
                prompt_tokens=10,
                completion_tokens=12,
                total_tokens=22,
                prompt_cache_hit_tokens=4,
                prompt_cache_miss_tokens=6,
                reasoning_tokens=7,
            ),
        ),
    )
    recorder.finish()

    metric = collector.calls[0]
    assert metric.agent == "judge"
    assert metric.duration_ms == pytest.approx(300.0)
    assert metric.time_to_first_reasoning_ms == pytest.approx(100.0)
    assert metric.time_to_first_content_ms == pytest.approx(200.0)
    assert metric.retry_count == 1
    assert metric.usage.total_tokens == 22
    assert metric.usage.reasoning_tokens == 7
    assert metric.success is True
