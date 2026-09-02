from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.providers.deepseek import DeepSeekProvider, LlmRequestError


class FakeCompletions:
    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **request: object) -> SimpleNamespace:
        self.requests.append(request)
        return self.responses.pop(0)


def completion(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            ),
        ],
    )


def stream_chunk(
    *,
    reasoning: str = "",
    content: str = "",
    finish_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(
                    reasoning_content=reasoning,
                    content=content,
                ),
            ),
        ],
    )


def test_provider_uses_structured_analysis_defaults() -> None:
    completions = FakeCompletions(completion('{"status":"ready"}'))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    result = provider.complete(
        system_prompt="system",
        user_prompt="user",
        response_format={"type": "json_object"},
    )

    assert result == '{"status":"ready"}'
    assert completions.requests == [{
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "reasoning_effort": "high",
        "max_tokens": 2_000,
        "stream": False,
        "extra_body": {"thinking": {"type": "enabled"}},
        "response_format": {"type": "json_object"},
    }]


def test_provider_retries_empty_response_without_thinking() -> None:
    completions = FakeCompletions(
        completion(""),
        completion('{"status":"ready"}'),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    result = provider.complete(
        system_prompt="system",
        user_prompt="user",
        response_format={"type": "json_object"},
    )

    assert result == '{"status":"ready"}'
    assert len(completions.requests) == 2
    assert completions.requests[0]["extra_body"] == {
        "thinking": {"type": "enabled"},
    }
    assert completions.requests[1]["extra_body"] == {
        "thinking": {"type": "disabled"},
    }
    assert "reasoning_effort" not in completions.requests[1]


def test_provider_does_not_retry_content_filtered_response() -> None:
    completions = FakeCompletions(completion("", finish_reason="content_filter"))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    with pytest.raises(LlmRequestError, match="finish_reason=content_filter"):
        provider.complete(system_prompt="system", user_prompt="user")

    assert len(completions.requests) == 1


def test_provider_streams_reasoning_and_content() -> None:
    completions = FakeCompletions(
        [
            stream_chunk(reasoning="Проверяю критерии. "),
            stream_chunk(content='{"status":"ready"}'),
            stream_chunk(finish_reason="stop"),
        ],  # type: ignore[arg-type]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    result = list(
        provider.stream(
            system_prompt="system",
            user_prompt="user",
            response_format={"type": "json_object"},
        ),
    )

    assert result[0].reasoning == "Проверяю критерии. "
    assert result[1].content == '{"status":"ready"}'
    assert result[2].finish_reason == "stop"
    assert completions.requests[0]["stream"] is True
    assert completions.requests[0]["extra_body"] == {
        "thinking": {"type": "enabled"},
    }


def test_provider_falls_back_without_thinking_for_empty_stream() -> None:
    completions = FakeCompletions(
        [stream_chunk(finish_reason="length")],  # type: ignore[arg-type]
        [stream_chunk(content='{"status":"ready"}')],  # type: ignore[arg-type]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    result = list(provider.stream(system_prompt="system", user_prompt="user"))

    status_chunk = next(chunk for chunk in result if chunk.status)
    content_chunk = next(chunk for chunk in result if chunk.content)
    assert status_chunk.status == (
        "Финальный ответ не пришёл в thinking-режиме; "
        "повторяем без thinking…"
    )
    assert content_chunk.content == '{"status":"ready"}'
    assert completions.requests[0]["extra_body"] == {
        "thinking": {"type": "enabled"},
    }
    assert completions.requests[1]["extra_body"] == {
        "thinking": {"type": "disabled"},
    }
    assert "reasoning_effort" not in completions.requests[1]
