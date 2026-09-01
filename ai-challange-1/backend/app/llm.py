from __future__ import annotations

import os
from functools import lru_cache

from openai import OpenAI


SYSTEM_PROMPT = "You are a helpful assistant. Answer clearly and concisely."


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def get_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def complete(messages: list[dict[str, str]]) -> str:
    response = get_client().chat.completions.create(
        model=get_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *messages,
        ],
    )
    return (response.choices[0].message.content or "").strip()
