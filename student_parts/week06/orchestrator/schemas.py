from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal["nana", "kana"]
TaskStatus = Literal["ok", "fail", "skipped"]


class UseResultDependency(BaseModel):
    """선행 작업 결과를 전달받아 현재 작업을 실행하는 관계."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, description="결과를 전달받을 선행 작업 id")
    type: Literal["use_result"] = Field(
        default="use_result",
        description="선행 결과를 사용하되 현재 작업은 조건 없이 실행",
    )


class RunOnlyIfDependency(BaseModel):
    """선행 작업의 참·거짓 결과에 따라 현재 작업을 실행하는 관계."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, description="조건값을 반환하는 선행 작업 id")
    type: Literal["run_only_if"] = Field(
        default="run_only_if",
        description="선행 조건값이 equals와 일치할 때만 현재 작업을 실행",
    )
    equals: bool = Field(
        default=True,
        description="현재 작업을 실행하기 위해 필요한 선행 작업의 조건값",
    )


TaskDependency = Annotated[
    UseResultDependency | RunOnlyIfDependency,
    Field(discriminator="type"),
]


class AgentTask(BaseModel):
    """분해기가 만드는 하나의 원자적 작업."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    requires_external_data:bool = Field(description="현재 작업이 나를 제외한 외부 멤버의 데이터를 필요로 하는지 여부")
    external_members: list[str] = Field(default_factory=list,description="나, 사용자 본인을 제외한 외부 멤버를 의미함")
    query: str = Field(min_length=1)
    dependencies: list[TaskDependency] = Field(
        default_factory=list,
        description="현재 작업이 참조하는 선행 작업과 관계 종류",
    )


class DecomposedPlan(BaseModel):
    """분해기가 반환하는 실행 전 계획."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[AgentTask] = Field(min_length=1)


class TaskResult(BaseModel):
    """Nana/Kana의 호출·반환 계약을 실행기가 소비할 공통 형태로 정규화한 값."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    agent: AgentName
    status: TaskStatus
    answer: str | None = None
    inner_tool_names: list[str] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None
    error: str | None = None
    condition_value: bool | None = Field(
        default=None,
        description="조건 확인 작업이 반환한 참·거짓 값",
    )
