from __future__ import annotations

from types import SimpleNamespace

from app.providers.deepseek import DeepSeekProvider


class FakeCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    def create(self, **request: object) -> SimpleNamespace:
        self.request = request
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"status":"ready"}'),
                ),
            ],
        )


def test_provider_uses_structured_analysis_defaults() -> None:
    completions = FakeCompletions()
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
    assert completions.request == {
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
    }
