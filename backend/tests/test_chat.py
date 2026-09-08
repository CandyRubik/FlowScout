from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.agent import Agent, AgentMessage
from app.main import app, get_chat_session_service
from app.schemas import ChatExperimentSettings
from app.services.chat_sessions import ChatSessionService, SQLiteChatSessionRepository


class FakeLanguageModel:
    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or ["Ответ агента"]
        self.calls: list[tuple[list[AgentMessage], int]] = []

    def generate(
        self,
        *,
        messages: Sequence[AgentMessage],
        max_tokens: int = 2_000,
    ) -> str:
        self.calls.append((list(messages), max_tokens))
        return self.answers.pop(0)


def repository(tmp_path: Path) -> SQLiteChatSessionRepository:
    return SQLiteChatSessionRepository(tmp_path / "chat.sqlite3")


def test_agent_encapsulates_context_policy_and_model_call() -> None:
    model = FakeLanguageModel(["  Готово  "])
    agent = Agent(model)

    answer = agent.respond(
        [{"role": "user", "content": "Раньше"}, {"role": "assistant", "content": "Да"}],
        "  Продолжим?  ",
    )

    assert answer == "Готово"
    assert model.calls[0][0][0]["role"] == "system"
    assert model.calls[0][0][-1] == {"role": "user", "content": "Продолжим?"}


def test_sessions_keep_contexts_isolated(tmp_path: Path) -> None:
    model = FakeLanguageModel(["Ответ A", "Ответ B", "Ответ A2"])
    service = ChatSessionService(repository(tmp_path), Agent(model))
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


def test_context_survives_backend_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent-chat.sqlite3"
    first_service = ChatSessionService(
        SQLiteChatSessionRepository(database_path),
        Agent(FakeLanguageModel(["Тебя зовут Лена"])),
    )
    session = first_service.create()
    first_service.send(session.id, "Запомни: меня зовут Лена")

    # New repository, service and agent simulate a freshly started backend process.
    restarted_model = FakeLanguageModel(["Тебя зовут Лена"])
    restarted_service = ChatSessionService(
        SQLiteChatSessionRepository(database_path),
        Agent(restarted_model),
    )
    restarted_service.send(session.id, "Как меня зовут?")

    model_context, _ = restarted_model.calls[0]
    assert model_context[-3:] == [
        {"role": "user", "content": "Запомни: меня зовут Лена"},
        {"role": "assistant", "content": "Тебя зовут Лена"},
        {"role": "user", "content": "Как меня зовут?"},
    ]
    assert [message.content for message in restarted_service.get(session.id).messages] == [
        "Запомни: меня зовут Лена",
        "Тебя зовут Лена",
        "Как меня зовут?",
        "Тебя зовут Лена",
    ]


def test_agent_applies_runtime_experiment_options() -> None:
    model = FakeLanguageModel()
    agent = Agent(
        model,
        system_prompt="Экспериментальный prompt",
        max_tokens=777,
        context_enabled=False,
    )

    agent.respond([{"role": "user", "content": "Скрытый контекст"}], "Новый вопрос")

    messages, max_tokens = model.calls[0]
    assert messages == [
        {"role": "system", "content": "Экспериментальный prompt"},
        {"role": "user", "content": "Новый вопрос"},
    ]
    assert max_tokens == 777


def test_chat_session_http_flow(tmp_path: Path) -> None:
    service = ChatSessionService(
        repository(tmp_path),
        Agent(FakeLanguageModel(["Привет! Чем помочь?"])),
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


def test_clear_chat_database_removes_all_sessions(tmp_path: Path) -> None:
    service = ChatSessionService(
        repository(tmp_path),
        Agent(FakeLanguageModel(["Ответ"])),
    )
    session = service.create()
    service.send(session.id, "Сообщение")
    service.create()
    app.dependency_overrides[get_chat_session_service] = lambda: service
    try:
        response = TestClient(app).delete("/api/chat/sessions")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert service.list() == []


def test_unknown_chat_session_returns_404(tmp_path: Path) -> None:
    service = ChatSessionService(repository(tmp_path), Agent(FakeLanguageModel()))
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
