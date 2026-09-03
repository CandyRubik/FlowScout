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
        iter(
            [
                stream_chunk(reasoning="Сначала проверю факты. "),
                stream_chunk(content="Итоговый ответ.", finish_reason="stop"),
            ],
        ),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    chunks = list(provider.stream(system_prompt="system", user_prompt="user"))

    assert [chunk.reasoning for chunk in chunks if chunk.reasoning] == [
        "Сначала проверю факты. ",
    ]
    assert [chunk.content for chunk in chunks if chunk.content] == [
        "Итоговый ответ.",
    ]
    assert completions.requests[0]["stream"] is True
    assert completions.requests[0]["extra_body"] == {
        "thinking": {"type": "enabled"},
    }


def test_provider_retries_empty_stream_without_thinking() -> None:
    completions = FakeCompletions(
        iter(
            [
                stream_chunk(reasoning="Думаю, но финала пока нет."),
                stream_chunk(finish_reason="stop"),
            ],
        ),
        iter(
            [
                stream_chunk(content="Ответ после повтора.", finish_reason="stop"),
            ],
        ),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = DeepSeekProvider(client=client)  # type: ignore[arg-type]

    chunks = list(provider.stream(system_prompt="system", user_prompt="user"))

    assert any(chunk.status for chunk in chunks)
    assert "Ответ после повтора." in "".join(chunk.content for chunk in chunks)
    assert len(completions.requests) == 2
    assert completions.requests[1]["extra_body"] == {
        "thinking": {"type": "disabled"},
    }
    assert "reasoning_effort" not in completions.requests[1]
