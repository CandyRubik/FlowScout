from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import queue
from typing import Any

from ..prompts import (
    JUDGE_EXPERT_INSTRUCTIONS,
    JUDGE_FIRST_AGENT_SYSTEM_PROMPT,
    JUDGE_FINAL_SYSTEM_PROMPT,
    judge_expert_user_prompt,
    judge_final_user_prompt,
    judge_task_user_prompt,
)
from ..providers.deepseek import (
    DeepSeekProvider,
    LlmRequestError,
    LlmStreamChunk,
)
from ..schemas import JudgeRequest


JudgeUpdate = dict[str, Any]


EXPERT_LABELS = {
    "engineer": "Инженер",
    "analyst": "Аналитик",
    "process_pm": "Проджект-менеджер",
}


class LlmJudgeService:
    def __init__(self, provider: DeepSeekProvider) -> None:
        self._provider = provider

    def _stream_agent(
        self,
        *,
        agent: str,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, object] | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[JudgeUpdate]:
        answer_parts: list[str] = []
        stream_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_format": response_format,
        }
        if max_tokens is not None:
            stream_kwargs["max_tokens"] = max_tokens

        for chunk in self._provider.stream(**stream_kwargs):
            if chunk.status:
                yield {
                    "type": "status",
                    "agent": agent,
                    "message": chunk.status,
                }
            if chunk.reasoning:
                yield {
                    "type": "reasoning",
                    "agent": agent,
                    "text": chunk.reasoning,
                }
            if chunk.content:
                answer_parts.append(chunk.content)
                yield {
                    "type": "content",
                    "agent": agent,
                    "text": chunk.content,
                }

        yield {
            "type": "complete",
            "agent": agent,
            "answer": "".join(answer_parts).strip(),
        }

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, LlmRequestError):
            return "Агент не вернул финальный ответ"
        return "Агент временно недоступен"

    def _run_expert(
        self,
        *,
        agent: str,
        task: str,
        initial_answer: str,
        updates: queue.Queue[JudgeUpdate],
    ) -> None:
        try:
            for update in self._stream_agent(
                agent=agent,
                system_prompt=JUDGE_EXPERT_INSTRUCTIONS[agent],
                user_prompt=judge_expert_user_prompt(task, initial_answer),
            ):
                updates.put(update)
        except Exception as error:
            updates.put(
                {
                    "type": "agent_error",
                    "agent": agent,
                    "message": self._error_message(error),
                },
            )
        finally:
            updates.put({"type": "worker_done", "agent": agent})

    def stream(self, request: JudgeRequest) -> Iterator[JudgeUpdate]:
        yield {
            "type": "stage",
            "stage": "first",
            "agents": ["first"],
            "message": "Первый агент формирует исходное решение…",
        }
        initial_answer = ""
        for update in self._stream_agent(
            agent="first",
            system_prompt=JUDGE_FIRST_AGENT_SYSTEM_PROMPT,
            user_prompt=judge_task_user_prompt(request.task),
        ):
            if update["type"] == "complete":
                initial_answer = str(update["answer"])
                yield {
                    "type": "agent_done",
                    "agent": "first",
                    "answer": initial_answer,
                }
            else:
                yield update

        if not initial_answer:
            raise LlmRequestError("First agent returned an empty answer")

        yield {
            "type": "stage",
            "stage": "experts",
            "agents": list(EXPERT_LABELS),
            "message": "Подключаем инженера, аналитика и проджект-менеджера…",
        }
        expert_updates: queue.Queue[JudgeUpdate] = queue.Queue()
        expert_answers: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=len(EXPERT_LABELS)) as executor:
            for agent in EXPERT_LABELS:
                executor.submit(
                    self._run_expert,
                    agent=agent,
                    task=request.task,
                    initial_answer=initial_answer,
                    updates=expert_updates,
                )

            finished_workers = 0
            while finished_workers < len(EXPERT_LABELS):
                update = expert_updates.get()
                update_type = update.get("type")
                if update_type == "worker_done":
                    finished_workers += 1
                elif update_type == "complete":
                    expert_answers[str(update["agent"])] = str(update["answer"])
                    yield {
                        "type": "agent_done",
                        "agent": update["agent"],
                        "answer": update["answer"],
                    }
                else:
                    yield update

        for agent in EXPERT_LABELS:
            expert_answers.setdefault(agent, "Эксперт не смог предоставить ответ.")

        yield {
            "type": "stage",
            "stage": "judge",
            "agents": ["judge"],
            "message": "Судья сопоставляет все ответы и формирует решение…",
        }
        final_answer = ""
        for update in self._stream_agent(
            agent="judge",
            system_prompt=JUDGE_FINAL_SYSTEM_PROMPT,
            user_prompt=judge_final_user_prompt(
                request.task,
                initial_answer,
                expert_answers,
            ),
            response_format={"type": "json_object"},
            max_tokens=4_000,
        ):
            if update["type"] == "complete":
                final_answer = str(update["answer"])
            else:
                yield update

        if not final_answer:
            raise LlmRequestError("Judge returned an empty answer")

        yield {
            "type": "done",
            "answer": final_answer,
            "initial_answer": initial_answer,
            "expert_answers": expert_answers,
        }
