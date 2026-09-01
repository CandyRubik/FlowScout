from __future__ import annotations

import logging
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .llm import complete


logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    message: ChatMessage


def _allowed_origins() -> list[str]:
    configured_origins = os.getenv("FRONTEND_ORIGINS")
    if not configured_origins:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]

    return [
        origin.strip()
        for origin in configured_origins.split(",")
        if origin.strip()
    ]


app = FastAPI(title="AI Challenge 1 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health() -> dict[str, bool | str]:
    return {
        "status": "ok",
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages must not be empty")
    if len(request.messages) > 50:
        raise HTTPException(status_code=400, detail="Too many messages")
    if request.messages[-1].role != "user":
        raise HTTPException(
            status_code=400,
            detail="The last message must be from the user",
        )

    messages = []
    for message in request.messages:
        content = message.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="Messages must not be empty")
        messages.append({"role": message.role, "content": content})

    try:
        answer = complete(messages)
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="DeepSeek API is not configured",
        ) from None
    except Exception:
        logger.exception("DeepSeek request failed")
        raise HTTPException(
            status_code=502,
            detail="DeepSeek request failed",
        ) from None

    if not answer:
        raise HTTPException(status_code=502, detail="DeepSeek returned an empty response")

    return ChatResponse(
        message=ChatMessage(role="assistant", content=answer),
    )
