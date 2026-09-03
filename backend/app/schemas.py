from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, TypeAlias, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


QuestionText = Annotated[str, Field(min_length=1, max_length=500)]
AssumptionText = Annotated[str, Field(min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )


class Recommendation(str, Enum):
    HUMAN = "human"
    AUTOMATE = "automate"
    CONTRACTOR = "contractor"


class ClarificationAnswer(StrictModel):
    question: QuestionText
    answer: Annotated[str, Field(min_length=1, max_length=2_000)]


class RoleAnalysisRequest(StrictModel):
    role_description: Annotated[str, Field(min_length=10, max_length=12_000)]
    clarification_answers: list[ClarificationAnswer] = Field(
        default_factory=list,
        max_length=3,
    )


class JudgeRequest(StrictModel):
    task: Annotated[str, Field(min_length=10, max_length=12_000)]


class RoleTask(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    description: Annotated[str, Field(min_length=1, max_length=1_000)]
    recommendation: Recommendation
    rationale: Annotated[str, Field(min_length=1, max_length=800)]
    assumptions: list[AssumptionText] = Field(default_factory=list, max_length=10)


class RoleAnalysis(StrictModel):
    role_title: Annotated[str, Field(min_length=1, max_length=160)]
    role_summary: Annotated[str, Field(min_length=1, max_length=1_000)]
    tasks: list[RoleTask] = Field(min_length=1, max_length=20)
    global_assumptions: list[AssumptionText] = Field(
        default_factory=list,
        max_length=10,
    )

    @model_validator(mode="after")
    def validate_unique_task_titles(self) -> RoleAnalysis:
        normalized_titles = [
            " ".join(task.title.casefold().split())
            for task in self.tasks
        ]
        if len(normalized_titles) != len(set(normalized_titles)):
            raise ValueError("tasks must have unique titles")
        return self


class NeedsClarification(StrictModel):
    status: Literal["needs_clarification"]
    questions: list[QuestionText] = Field(min_length=1, max_length=3)
    analysis: None = None


class ReadyAnalysis(StrictModel):
    status: Literal["ready"]
    questions: list[QuestionText] = Field(default_factory=list, max_length=3)
    analysis: RoleAnalysis

    @field_validator("questions")
    @classmethod
    def ready_response_must_not_contain_questions(cls, value: list[str]) -> list[str]:
        if value:
            raise ValueError("ready response must have an empty questions list")
        return value


RoleAnalysisResponse: TypeAlias = Annotated[
    Union[NeedsClarification, ReadyAnalysis],
    Field(discriminator="status"),
]


ROLE_ANALYSIS_RESPONSE_ADAPTER = TypeAdapter(RoleAnalysisResponse)
