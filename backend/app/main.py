from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .providers.deepseek import (
    DeepSeekProvider,
    LlmConfigurationError,
    LlmRequestError,
)
from .schemas import RoleAnalysisRequest, RoleAnalysisResponse
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
