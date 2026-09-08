from __future__ import annotations

from collections.abc import Sequence

from fastapi.testclient import TestClient

from app.agents.chat import ChatAgent, ModelMessage
from app.main import app, get_chat_session_service
from app.schemas import ChatExperimentSettings
from app.services.chat_sessions import (
    ChatSessionService,
    InMemoryChatSessionRepository,
)


class FakeChatModel:
    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or ["Ответ агента"]
        self.calls: list[tuple[list[ModelMessage], int]] = []

    def complete_chat(
        self,
        *,
        messages: Sequence[ModelMessage],
        max_tokens: int = 2_000,
    ) -> str:
        self.calls.append((list(messages), max_tokens))
        return self.answers.pop(0)


def test_agent_encapsulates_policy_and_model_call() -> None:
    model = FakeChatModel(["  Готово  "])
    agent = ChatAgent(model)

    answer = agent.respond(
        [{"role": "user", "content": "Раньше"}, {"role": "assistant", "content": "Да"}],
        "  Продолжим?  ",
    )

    assert answer == "Готово"
    assert model.calls[0][0][0]["role"] == "system"
    assert model.calls[0][0][-1] == {"role": "user", "content": "Продолжим?"}


def test_sessions_keep_histories_isolated() -> None:
    model = FakeChatModel(["Ответ A", "Ответ B", "Ответ A2"])
    service = ChatSessionService(InMemoryChatSessionRepository(), ChatAgent(model))
    first = service.create()
    second = service.create()

    service.send(first.id, "Вопрос A")
    service.send(second.id, "Вопрос B")
    service.send(first.id, "Ещё A")

    assert [message.content for message in service.get(first.id).messages] == [
        "Вопрос A", "Ответ A", "Ещё A", "Ответ A2",
    ]
    assert [message.content for message in service.get(second.id).messages] == [
        "Вопрос B", "Ответ B",
    ]
    assert "Вопрос B" not in [message["content"] for message in model.calls[2][0]]


def test_agent_applies_runtime_experiment_options() -> None:
    model = FakeChatModel()
    agent = ChatAgent(
        model,
        system_prompt="Экспериментальный prompt",
        max_tokens=777,
        history_enabled=False,
    )

    agent.respond([{"role": "user", "content": "Скрытая история"}], "Новый вопрос")

    messages, max_tokens = model.calls[0]
    assert messages == [
        {"role": "system", "content": "Экспериментальный prompt"},
        {"role": "user", "content": "Новый вопрос"},
    ]
    assert max_tokens == 777


def test_chat_session_http_flow() -> None:
    service = ChatSessionService(
        InMemoryChatSessionRepository(),
        ChatAgent(FakeChatModel(["Привет! Чем помочь?"])),
    )
    app.dependency_overrides[get_chat_session_service] = lambda: service
    client = TestClient(app)
    try:
        created = client.post("/api/chat/sessions")
        session_id = created.json()["id"]
        sent = client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={"content": "Привет"},
        )
        loaded = client.get(f"/api/chat/sessions/{session_id}")
        sessions = client.get("/api/chat/sessions")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert sent.status_code == 200
    assert sent.json()["assistant_message"]["content"] == "Привет! Чем помочь?"
    assert [message["role"] for message in loaded.json()["messages"]] == [
        "user", "assistant",
    ]
    assert sessions.json()[0]["title"] == "Привет"


def test_unknown_chat_session_returns_404() -> None:
    service = ChatSessionService(InMemoryChatSessionRepository(), ChatAgent(FakeChatModel()))
    app.dependency_overrides[get_chat_session_service] = lambda: service
    try:
        response = TestClient(app).get("/api/chat/sessions/missing")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_debug_settings_can_be_changed_at_runtime() -> None:
    changed = ChatExperimentSettings(
        model="deepseek-v4-pro",
        thinking_enabled=False,
        history_enabled=False,
        max_tokens=512,
        system_prompt="Тестовый prompt",
    )
    client = TestClient(app)
    try:
        response = client.put("/api/debug/settings", json=changed.model_dump())
        loaded = client.get("/api/debug/settings")
    finally:
        client.put("/api/debug/settings", json=ChatExperimentSettings().model_dump())

    assert response.status_code == 200
    assert loaded.json()["model"] == "deepseek-v4-pro"
    assert loaded.json()["history_enabled"] is False
