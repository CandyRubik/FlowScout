from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .prompts import (
    EXPERT_INSTRUCTIONS,
    PROMPT_ENGINEER_SYSTEM_PROMPT,
    STEP_BY_STEP_INSTRUCTION,
    add_experiment_instruction,
    prompt_engineer_user_prompt,
)
from .providers.deepseek import (
    DeepSeekProvider,
    LlmEmptyStreamError,
    LlmConfigurationError,
    LlmRequestError,
)
from .schemas import (
    ReasoningExperimentRequest,
    RoleAnalysisRequest,
    RoleAnalysisResponse,
)
from .services.reasoning_stream import ReasoningStreamService, StreamUpdate
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


def get_reasoning_stream_service() -> ReasoningStreamService:
    return ReasoningStreamService(DeepSeekProvider())


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _streaming_response(updates: Iterator[StreamUpdate]) -> StreamingResponse:
    def events() -> Iterator[str]:
        try:
            for update in updates:
                event_type = str(update.get("type", "status"))
                payload = {
                    key: value for key, value in update.items() if key != "type"
                }
                yield _sse_event(event_type, payload)
        except LlmConfigurationError:
            yield _sse_event(
                "error",
                {"message": "DeepSeek API is not configured", "status": 503},
            )
        except LlmEmptyStreamError as error:
            logger.exception("DeepSeek returned no final streamed content")
            yield _sse_event(
                "error",
                {
                    "message": "DeepSeek не вернул финальный ответ в потоковом режиме",
                    "detail": str(error),
                    "status": 502,
                },
            )
        except LlmRequestError:
            logger.exception("DeepSeek streaming request failed")
            yield _sse_event(
                "error",
                {"message": "DeepSeek request failed", "status": 502},
            )
        except InvalidModelResponse:
            logger.exception("DeepSeek returned an invalid streamed response")
            yield _sse_event(
                "error",
                {"message": "DeepSeek returned an invalid structured response", "status": 502},
            )
        except Exception:
            logger.exception("Unexpected streaming request failure")
            yield _sse_event(
                "error",
                {"message": "Unexpected streaming request failure", "status": 500},
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _experiment_role_request(request: ReasoningExperimentRequest) -> RoleAnalysisRequest:
    return RoleAnalysisRequest(role_description=request.task)


def _prompt_generated_updates(
    request: ReasoningExperimentRequest,
    stream_service: ReasoningStreamService,
) -> Iterator[StreamUpdate]:
    role_request = _experiment_role_request(request)
    yield {
        "type": "phase",
        "phase": "prompt_generation",
        "message": "Составляем промпт для решения задачи…",
    }

    generated_prompt_parts: list[str] = []
    for update in stream_service.stream_raw(
        system_prompt=PROMPT_ENGINEER_SYSTEM_PROMPT,
        user_prompt=prompt_engineer_user_prompt(request.task),
    ):
        update_type = update.get("type")
        if update_type == "complete":
            if not generated_prompt_parts:
                generated_prompt_parts.append(str(update.get("content", "")))
        elif update_type == "content":
            text = str(update.get("text", ""))
            generated_prompt_parts.append(text)
            yield {"type": "prompt", "text": text, "phase": "prompt_generation"}
        else:
            yield {**update, "phase": "prompt_generation"}

    generated_prompt = "".join(generated_prompt_parts).strip()
    if not generated_prompt:
        raise LlmRequestError("Prompt engineer returned an empty prompt")

    yield {
        "type": "prompt_ready",
        "prompt": generated_prompt,
    }
    yield {
        "type": "phase",
        "phase": "solution",
        "message": "Решаем задачу по сгенерированному промпту…",
    }

    generated_system_prompt = add_experiment_instruction(
        "Generated additional prompt from the prompt engineer:\n"
        + generated_prompt,
    )
    for update in stream_service.stream_role_analysis(
        role_request,
        system_prompt=generated_system_prompt,
    ):
        if update.get("type") == "done":
            yield {
                **update,
                "generated_prompt": generated_prompt,
            }
        else:
            yield {**update, "phase": "solution"}


def _expert_updates(
    request: ReasoningExperimentRequest,
    stream_service: ReasoningStreamService,
) -> Iterator[StreamUpdate]:
    role_request = _experiment_role_request(request)
    expert_results: list[dict[str, Any]] = []

    for expert, instruction in EXPERT_INSTRUCTIONS.items():
        yield {
            "type": "phase",
            "phase": "expert",
            "expert": expert,
            "message": f"Эксперт «{expert}» приступает к анализу…",
        }
        for update in stream_service.stream_role_analysis(
            role_request,
            system_prompt=add_experiment_instruction(instruction),
        ):
            if update.get("type") == "done":
                result = update.get("result")
                if isinstance(result, dict):
                    expert_results.append({"expert": expert, "result": result})
                    yield {
                        "type": "expert_done",
                        "expert": expert,
                        "result": result,
                    }
            else:
                yield {**update, "phase": "expert", "expert": expert}

    yield {
        "type": "done",
        "experts": expert_results,
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
    stream_service: ReasoningStreamService = Depends(get_reasoning_stream_service),
    stream: bool = Query(default=False),
) -> Any:
    if stream:
        return _streaming_response(stream_service.stream_role_analysis(request))

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


@app.post("/api/reasoning/step-by-step")
def step_by_step_reasoning(
    request: ReasoningExperimentRequest,
    stream_service: ReasoningStreamService = Depends(get_reasoning_stream_service),
) -> StreamingResponse:
    role_request = _experiment_role_request(request)
    return _streaming_response(
        stream_service.stream_role_analysis(
            role_request,
            system_prompt=add_experiment_instruction(STEP_BY_STEP_INSTRUCTION),
        ),
    )


@app.post("/api/reasoning/prompt-generated")
def prompt_generated_reasoning(
    request: ReasoningExperimentRequest,
    stream_service: ReasoningStreamService = Depends(get_reasoning_stream_service),
) -> StreamingResponse:
    return _streaming_response(_prompt_generated_updates(request, stream_service))


@app.post("/api/reasoning/experts")
def expert_reasoning(
    request: ReasoningExperimentRequest,
    stream_service: ReasoningStreamService = Depends(get_reasoning_stream_service),
) -> StreamingResponse:
    return _streaming_response(_expert_updates(request, stream_service))
