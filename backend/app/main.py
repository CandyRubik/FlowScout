from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agents.chat import AgentInputError, AgentOutputError, ChatAgent
from .providers.deepseek import (
    DeepSeekProvider,
    LlmConfigurationError,
    LlmRequestError,
)
from .schemas import (
    ChatSendRequest,
    ChatSendResponse,
    ChatExperimentSettings,
    ChatSession,
    ChatSessionSummary,
    JudgeRequest,
    RoleAnalysisRequest,
    RoleAnalysisResponse,
)
from .services.chat_sessions import (
    ChatSessionNotFound,
    ChatSessionService,
    InMemoryChatSessionRepository,
)
from .services.experiment_settings import ExperimentSettingsStore
from .services.llm_judge import JudgeUpdate, LlmJudgeService
from .services.role_analyzer import (
    InvalidModelResponse,
    RoleAnalysisService,
)


logger = logging.getLogger(__name__)
_chat_repository = InMemoryChatSessionRepository()
_experiment_settings = ExperimentSettingsStore()


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
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)


def get_role_analysis_service() -> RoleAnalysisService:
    return RoleAnalysisService(DeepSeekProvider())


def get_llm_judge_service() -> LlmJudgeService:
    return LlmJudgeService(DeepSeekProvider())


def get_chat_session_service() -> ChatSessionService:
    settings = _experiment_settings.get()
    model = DeepSeekProvider(
        model=settings.model,
        thinking_enabled=settings.thinking_enabled,
    )
    agent = ChatAgent(
        model,
        system_prompt=settings.system_prompt,
        max_tokens=settings.max_tokens,
        history_enabled=settings.history_enabled,
    )
    return ChatSessionService(_chat_repository, agent)


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


@app.get("/api/debug/settings", response_model=ChatExperimentSettings)
def get_debug_settings() -> ChatExperimentSettings:
    return _experiment_settings.get()


@app.put("/api/debug/settings", response_model=ChatExperimentSettings)
def update_debug_settings(settings: ChatExperimentSettings) -> ChatExperimentSettings:
    return _experiment_settings.replace(settings)


@app.post("/api/chat/sessions", response_model=ChatSession, status_code=201)
def create_chat_session(
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSession:
    return service.create()


@app.get("/api/chat/sessions", response_model=list[ChatSessionSummary])
def list_chat_sessions(
    service: ChatSessionService = Depends(get_chat_session_service),
) -> list[ChatSessionSummary]:
    return service.list()


@app.get("/api/chat/sessions/{session_id}", response_model=ChatSession)
def get_chat_session(
    session_id: str,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSession:
    try:
        return service.get(session_id)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Chat session not found") from None


@app.post(
    "/api/chat/sessions/{session_id}/messages",
    response_model=ChatSendResponse,
)
def send_chat_message(
    session_id: str,
    request: ChatSendRequest,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSendResponse:
    try:
        return service.send(session_id, request.content)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Chat session not found") from None
    except AgentInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except (AgentOutputError, LlmRequestError):
        logger.exception("Chat agent request failed")
        raise HTTPException(status_code=502, detail="LLM request failed") from None
    except LlmConfigurationError:
        raise HTTPException(status_code=503, detail="DeepSeek API is not configured") from None


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
