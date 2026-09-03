from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from ..prompts import (
    TEMPERATURE_EXPERIMENT_SYSTEM_PROMPT,
    temperature_experiment_user_prompt,
)
from ..providers.deepseek import LlmConfigurationError, LlmRequestError
from ..schemas import (
    TEMPERATURE_VALUES,
    TemperatureExperimentRequest,
    TemperatureExperimentResponse,
    TemperatureExperimentResult,
)


class TemperatureLlmProvider(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> str:
        ...


class TemperatureExperimentService:
    def __init__(self, provider: TemperatureLlmProvider) -> None:
        self._provider = provider

    def _run_one(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> TemperatureExperimentResult:
        try:
            answer = self._provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
            ).strip()
            if not answer:
                raise LlmRequestError("The model returned an empty response")
            return TemperatureExperimentResult(
                temperature=temperature,
                answer=answer,
            )
        except LlmConfigurationError:
            raise
        except LlmRequestError:
            return TemperatureExperimentResult(
                temperature=temperature,
                error="Модель не вернула ответ",
            )
        except Exception:
            return TemperatureExperimentResult(
                temperature=temperature,
                error="Не удалось выполнить этот вариант",
            )

    def run(
        self,
        request: TemperatureExperimentRequest,
    ) -> TemperatureExperimentResponse:
        system_prompt = TEMPERATURE_EXPERIMENT_SYSTEM_PROMPT
        user_prompt = temperature_experiment_user_prompt(request.task)

        with ThreadPoolExecutor(max_workers=len(TEMPERATURE_VALUES)) as executor:
            results = list(
                executor.map(
                    lambda temperature: self._run_one(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        temperature=temperature,
                    ),
                    TEMPERATURE_VALUES,
                ),
            )

        return TemperatureExperimentResponse(
            task=request.task,
            results=results,
        )
