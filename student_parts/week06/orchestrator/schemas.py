from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal["nana", "kana"]
TaskStatus = Literal["ok", "fail", "skipped"]


class AgentTask(BaseModel):
    """분해기가 만드는 하나의 원자적 작업."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    requires_external_data: bool
    external_members: list[str] = Field(default_factory=list)
    query: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


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
