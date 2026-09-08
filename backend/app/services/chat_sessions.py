from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from ..agents.chat import ChatAgent, ModelMessage
from ..schemas import ChatMessage, ChatSendResponse, ChatSession, ChatSessionSummary


class ChatSessionNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: str
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredSession:
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: tuple[StoredMessage, ...] = ()


class InMemoryChatSessionRepository:
    """Process-local session storage; intentionally cleared on backend restart."""

    def __init__(self) -> None:
        self._sessions: dict[str, StoredSession] = {}
        self._lock = RLock()

    def create(self) -> StoredSession:
        session_id = str(uuid4())
        now = datetime.now(timezone.utc)
        session = StoredSession(session_id, "Новый чат", now, now)
        with self._lock:
            self._sessions[session_id] = session
        return session

    def list(self) -> list[StoredSession]:
        with self._lock:
            return sorted(
                self._sessions.values(),
                key=lambda session: session.updated_at,
                reverse=True,
            )[:100]

    def get(self, session_id: str) -> StoredSession:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as error:
                raise ChatSessionNotFound(session_id) from error

    def append_exchange(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
    ) -> StoredSession:
        now = datetime.now(timezone.utc)
        with self._lock:
            session = self.get(session_id)
            messages = session.messages + (
                StoredMessage(str(uuid4()), "user", user_content, now),
                StoredMessage(str(uuid4()), "assistant", assistant_content, now),
            )
            title = session.title
            if not session.messages:
                title = user_content.replace("\n", " ").strip()[:60] or "Новый чат"
            updated = StoredSession(
                session.id,
                title,
                session.created_at,
                now,
                messages,
            )
            self._sessions[session_id] = updated
            return updated


class ChatSessionService:
    def __init__(self, repository: InMemoryChatSessionRepository, agent: ChatAgent) -> None:
        self._repository = repository
        self._agent = agent

    @staticmethod
    def _summary(session: StoredSession) -> ChatSessionSummary:
        return ChatSessionSummary(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _message(message: StoredMessage) -> ChatMessage:
        return ChatMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )

    def create(self) -> ChatSession:
        session = self._repository.create()
        return ChatSession(**self._summary(session).model_dump(), messages=[])

    def list(self) -> list[ChatSessionSummary]:
        return [self._summary(session) for session in self._repository.list()]

    def get(self, session_id: str) -> ChatSession:
        session = self._repository.get(session_id)
        return ChatSession(
            **self._summary(session).model_dump(),
            messages=[self._message(message) for message in session.messages],
        )

    def send(self, session_id: str, content: str) -> ChatSendResponse:
        session = self._repository.get(session_id)
        history: list[ModelMessage] = [
            {"role": message.role, "content": message.content}
            for message in session.messages
        ]
        answer = self._agent.respond(history, content)
        updated = self._repository.append_exchange(session_id, content.strip(), answer)
        return ChatSendResponse(
            session=self._summary(updated),
            user_message=self._message(updated.messages[-2]),
            assistant_message=self._message(updated.messages[-1]),
        )
