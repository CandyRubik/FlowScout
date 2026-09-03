from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .providers.deepseek import (
    DeepSeekProvider,
    LlmConfigurationError,
    LlmRequestError,
)
from .schemas import (
    JudgeRequest,
    RoleAnalysisRequest,
    RoleAnalysisResponse,
    TemperatureExperimentRequest,
    TemperatureExperimentResponse,
)
from .services.llm_judge import JudgeUpdate, LlmJudgeService
from .services.role_analyzer import (
    InvalidModelResponse,
    RoleAnalysisService,
)
from .services.temperature_experiment import TemperatureExperimentService


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


def get_temperature_experiment_service() -> TemperatureExperimentService:
    return TemperatureExperimentService(DeepSeekProvider())


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


@app.post(
    "/api/temperature-experiment",
    response_model=TemperatureExperimentResponse,
)
def temperature_experiment(
    request: TemperatureExperimentRequest,
    service: TemperatureExperimentService = Depends(
        get_temperature_experiment_service,
    ),
) -> TemperatureExperimentResponse:
    try:
        return service.run(request)
    except LlmConfigurationError:
        raise HTTPException(
            status_code=503,
            detail="DeepSeek API is not configured",
        ) from None
    except LlmRequestError:
        logger.exception("DeepSeek temperature experiment request failed")
        raise HTTPException(
            status_code=502,
            detail="DeepSeek request failed",
        ) from None
