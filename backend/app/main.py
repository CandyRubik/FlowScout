from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
import json
import logging
import os
from queue import Queue
from threading import Thread
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from benchmarks.day5_model_versions import (
    VARIANTS,
    ModelVariant,
    run_once,
    summarise_variant,
)

from .providers.deepseek import (
    DeepSeekProvider,
    LlmConfigurationError,
    LlmRequestError,
)
from .schemas import JudgeRequest, RoleAnalysisRequest, RoleAnalysisResponse
from .services.llm_judge import JudgeUpdate, LlmJudgeService
from .services.role_analyzer import (
    InvalidModelResponse,
    RoleAnalysisService,
)


logger = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    configured_origins = os.getenv("FRONTEND_ORIGINS")
    if not configured_origins:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]

    return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]


app = FastAPI(title="FlowScout API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def get_role_analysis_service() -> RoleAnalysisService:
    return RoleAnalysisService(DeepSeekProvider())


def get_llm_judge_service() -> LlmJudgeService:
    return LlmJudgeService(DeepSeekProvider())


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        "data: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )


def _judge_streaming_response(
    updates: Iterator[JudgeUpdate],
) -> StreamingResponse:
    def events() -> Iterator[str]:
        try:
            for update in updates:
                event_type = str(update.get("type", "status"))
                payload = {
                    key: value
                    for key, value in update.items()
                    if key != "type"
                }
                yield _sse_event(event_type, payload)
        except LlmConfigurationError:
            yield _sse_event(
                "error",
                {"message": "DeepSeek API is not configured"},
            )
        except LlmRequestError:
            logger.exception("DeepSeek llm-as-a-judge request failed")
            yield _sse_event("error", {"message": "DeepSeek request failed"})
        except Exception:
            logger.exception("Unexpected llm-as-a-judge streaming error")
            yield _sse_event("error", {"message": "Не удалось завершить проверку"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _public_benchmark_result(result: dict[str, object]) -> dict[str, object]:
    return {
        key: result[key]
        for key in (
            "variant",
            "success",
            "final_answer_received",
            "all_calls_success",
            "call_failures",
            "error_type",
            "duration_ms",
            "stage_durations_ms",
            "request_count",
            "usage",
            "cost_usd",
            "automatic_quality",
            "final_answer",
        )
    }


def _benchmark_variant_stream(
    variant: ModelVariant,
    task: str,
) -> Iterator[dict[str, object]]:
    updates: Queue[dict[str, object] | None] = Queue()
    outcome: dict[str, object] = {}
    errors: list[Exception] = []

    def report(update: dict[str, object]) -> None:
        update_type = update.get("type")
        if update_type == "stage":
            updates.put(
                {
                    "type": "variant_stage",
                    "variant": variant.key,
                    "stage": update.get("stage"),
                    "message": update.get("message"),
                },
            )
        elif update_type == "agent_done":
            updates.put(
                {
                    "type": "variant_agent_done",
                    "variant": variant.key,
                    "agent": update.get("agent"),
                },
            )

    def worker() -> None:
        try:
            outcome["result"] = run_once(
                variant,
                task,
                phase="measurement",
                repetition=1,
                cache_isolation=True,
                on_update=report,
            )
        except Exception as error:
            errors.append(error)
        finally:
            updates.put(None)

    worker_thread = Thread(
        target=worker,
        name=f"day5-benchmark-{variant.key}",
        daemon=True,
    )
    worker_thread.start()

    while True:
        update = updates.get()
        if update is None:
            break
        yield update

    worker_thread.join()
    if errors:
        raise errors[0]

    result = outcome.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Benchmark did not return a result")
    yield {
        "type": "variant_result",
        **_public_benchmark_result(result),
    }


def _day5_benchmark_stream(task: str) -> Iterator[dict[str, object]]:
    yield {
        "type": "benchmark_start",
        "task": task,
        "variants": [asdict(variant) for variant in VARIANTS],
        "runs_per_variant": 1,
        "execution_order": "weak → medium → strong",
    }

    runs: list[dict[str, object]] = []
    for variant in VARIANTS:
        yield {
            "type": "variant_start",
            "variant": asdict(variant),
        }
        for update in _benchmark_variant_stream(variant, task):
            if update.get("type") == "variant_result":
                runs.append(
                    {
                        key: value
                        for key, value in update.items()
                        if key != "type"
                    },
                )
            yield update

    summary = [
        summarise_variant(
            variant,
            [
                run
                for run in runs
                if run["variant"]["key"] == variant.key
            ],
        )
        for variant in VARIANTS
    ]
    yield {
        "type": "benchmark_done",
        "summary": summary,
        "measured_cost_usd": round(
            sum(float(run["cost_usd"]) for run in runs),
            8,
        ),
    }


@app.get("/api/health")
def health() -> dict[str, bool | str]:
    return {
        "status": "ok",
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
    }


@app.post("/api/role-analysis", response_model=RoleAnalysisResponse)
def role_analysis(
    request: RoleAnalysisRequest,
    service: RoleAnalysisService = Depends(get_role_analysis_service),
) -> RoleAnalysisResponse:
    try:
        return service.analyze(request)
    except LlmConfigurationError:
        raise HTTPException(
            status_code=503,
            detail="DeepSeek API is not configured",
        ) from None
    except LlmRequestError:
        logger.exception("DeepSeek role analysis request failed")
        raise HTTPException(
            status_code=502,
            detail="DeepSeek request failed",
        ) from None
    except InvalidModelResponse:
        logger.exception("DeepSeek returned an invalid role analysis")
        raise HTTPException(
            status_code=502,
            detail="DeepSeek returned an invalid structured response",
        ) from None


@app.post("/api/llm-as-judge")
def llm_as_judge(
    request: JudgeRequest,
    service: LlmJudgeService = Depends(get_llm_judge_service),
) -> StreamingResponse:
    return _judge_streaming_response(service.stream(request))


@app.post("/api/day5-benchmark")
def day5_benchmark(request: JudgeRequest) -> StreamingResponse:
    return _judge_streaming_response(_day5_benchmark_stream(request.task))
