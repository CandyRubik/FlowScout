from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Callable

from ..providers.deepseek import LlmStreamChunk, LlmUsage


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class LlmCallMetric:
    """Measurements for one logical agent call in the judge workflow."""

    agent: str
    duration_ms: float
    time_to_first_reasoning_ms: float | None
    time_to_first_content_ms: float | None
    finish_reason: str | None
    retry_count: int
    usage: LlmUsage
    success: bool
    error_type: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "duration_ms": round(self.duration_ms, 2),
            "time_to_first_reasoning_ms": (
                round(self.time_to_first_reasoning_ms, 2)
                if self.time_to_first_reasoning_ms is not None
                else None
            ),
            "time_to_first_content_ms": (
                round(self.time_to_first_content_ms, 2)
                if self.time_to_first_content_ms is not None
                else None
            ),
            "finish_reason": self.finish_reason,
            "retry_count": self.retry_count,
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
                "prompt_cache_hit_tokens": self.usage.prompt_cache_hit_tokens,
                "prompt_cache_miss_tokens": self.usage.prompt_cache_miss_tokens,
                "reasoning_tokens": self.usage.reasoning_tokens,
            },
            "success": self.success,
            "error_type": self.error_type,
        }


class LlmMetricsCollector:
    """Thread-safe collector for the sequential and parallel judge stages."""

    def __init__(self, clock: Clock = perf_counter) -> None:
        self._clock = clock
        self._lock = Lock()
        self._calls: list[LlmCallMetric] = []

    def start(self, agent: str) -> LlmCallRecorder:
        return LlmCallRecorder(self, agent, self._clock)

    def _add(self, metric: LlmCallMetric) -> None:
        with self._lock:
            self._calls.append(metric)

    @property
    def calls(self) -> list[LlmCallMetric]:
        with self._lock:
            return sorted(self._calls, key=lambda item: item.duration_ms)


class LlmCallRecorder:
    def __init__(
        self,
        collector: LlmMetricsCollector,
        agent: str,
        clock: Clock,
    ) -> None:
        self._collector = collector
        self._agent = agent
        self._clock = clock
        self._started_at = clock()
        self._first_reasoning_at: float | None = None
        self._first_content_at: float | None = None
        self._finish_reason: str | None = None
        self._retry_count = 0
        self._usage = LlmUsage()
        self._finished = False

    def observe(self, chunk: LlmStreamChunk) -> None:
        now = self._clock()
        if chunk.reasoning and self._first_reasoning_at is None:
            self._first_reasoning_at = now
        if chunk.content and self._first_content_at is None:
            self._first_content_at = now
        if chunk.finish_reason:
            self._finish_reason = chunk.finish_reason
        if chunk.status:
            self._retry_count += 1
        if chunk.usage is not None:
            self._usage += chunk.usage

    def finish(self, error: Exception | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        finished_at = self._clock()
        self._collector._add(
            LlmCallMetric(
                agent=self._agent,
                duration_ms=(finished_at - self._started_at) * 1_000,
                time_to_first_reasoning_ms=(
                    (self._first_reasoning_at - self._started_at) * 1_000
                    if self._first_reasoning_at is not None
                    else None
                ),
                time_to_first_content_ms=(
                    (self._first_content_at - self._started_at) * 1_000
                    if self._first_content_at is not None
                    else None
                ),
                finish_reason=self._finish_reason,
                retry_count=self._retry_count,
                usage=self._usage,
                success=error is None,
                error_type=type(error).__name__ if error is not None else None,
            ),
        )
