from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from student_parts.week02_structure_natural_language_requests import (
    RequestKind,
    StructuredRequest,
)


TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date_or_none(value: Any) -> str | None:
    """None은 허용하지만, 값이 존재한다면 실제 YYYY-MM-DD 날짜여야 한다."""

    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("날짜는 문자열이어야 합니다.")

    if not DATE_PATTERN.fullmatch(value):
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")

    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("존재하지 않는 날짜입니다.") from error

    return value


def validate_time_or_none(value: Any) -> str | None:
    """None은 허용하지만, 값이 존재한다면 HH:MM 형식이어야 한다."""

    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("시간은 문자열이어야 합니다.")

    if not TIME_PATTERN.fullmatch(value):
        raise ValueError("시간은 HH:MM 형식이어야 합니다.")

    return value


def normalize_list_or_none(value: Any) -> list[str] | None:
    """문자열 하나 또는 문자열 목록을 정규화한다."""

    if value is None:
        return None

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        raise ValueError("문자열 또는 문자열 목록이어야 합니다.")

    normalized: list[str] = []

    for item in value:
        if not isinstance(item, str):
            raise ValueError("목록의 모든 값은 문자열이어야 합니다.")

        item = item.strip()

        if not item:
            raise ValueError("목록에 빈 문자열을 넣을 수 없습니다.")

        normalized.append(item)

    return normalized


def normalize_blank_str_or_none(value: Any) -> str | None:
    """None은 허용하지만, 값이 존재한다면 비어 있지 않은 문자열이어야 한다."""

    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("문자열이어야 합니다.")

    value = value.strip()

    if not value:
        raise ValueError("빈 문자열은 허용되지 않습니다.")

    return value


def normalize_required_string(value: Any) -> str:
    """필수 문자열 필드를 검사한다."""

    normalized = normalize_blank_str_or_none(value)

    if normalized is None:
        raise ValueError("필수 값입니다.")

    return normalized


class SaveStructuredRequestInput(StructuredRequest):
    """Week02 구조화 결과를 SQLite에 저장하기 위한 입력 스키마."""

    kind: RequestKind = Field(
        default="unknown",
        description="분류된 요청 종류",
    )

    source_schedule_id: str | None = Field(
        default=None,
        description="Week 1 임시 일정에서 넘어온 원본 일정 ID",
    )

    @model_validator(mode="before")
    @classmethod
    def unwrap_and_validate_payload(cls, value: Any) -> Any:
        if isinstance(value, StructuredRequest):
            value = value.model_dump()

        while isinstance(value, dict):
            if isinstance(value.get("payload"), dict):
                value = value["payload"]
            elif isinstance(value.get("structured_request"), dict):
                value = value["structured_request"]
            else:
                break

        if not isinstance(value, dict):
            return value

        if "date" in value:
            value["date"] = validate_date_or_none(value["date"])

        if "start_time" in value:
            value["start_time"] = validate_time_or_none(
                value["start_time"]
            )

        if "end_time" in value:
            value["end_time"] = validate_time_or_none(
                value["end_time"]
            )

        if "source_schedule_id" in value:
            value["source_schedule_id"] = normalize_blank_str_or_none(
                value["source_schedule_id"]
            )

        if "members" in value:
            members = normalize_list_or_none(value["members"])
            value["members"] = members or []

        return value


class DateRangeInput(BaseModel):
    """기간 조회에 공통으로 사용하는 입력."""

    date_from: str | None = None
    date_to: str | None = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def validate_date_range_field(cls, value: Any) -> str | None:
        return validate_date_or_none(value)

    @model_validator(mode="after")
    def validate_date_order(self):
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_to < self.date_from
        ):
            raise ValueError(
                "date_to는 date_from보다 빠를 수 없습니다."
            )

        return self


class SavedRequestListInput(DateRangeInput):
    """저장된 구조화 요청 목록 조회 입력."""

    kind: RequestKind | None = None


class SavedRequestGetInput(BaseModel):
    """저장된 구조화 요청 단건 조회 입력."""

    request_id: str

    @field_validator("request_id", mode="before")
    @classmethod
    def validate_request_id(cls, value: Any) -> str:
        return normalize_required_string(value)


class SavedScheduleListInput(DateRangeInput):
    """저장된 일정 목록 조회 입력."""

    limit: int = Field(default=50, ge=1, le=200)
    kind: RequestKind | None = None


class SavedScheduleFields(BaseModel):
    """일정 수정과 삭제가 공통으로 사용하는 필드."""

    title: str | None = None
    date: str | None = None
    start_time: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value: Any) -> str | None:
        return normalize_blank_str_or_none(value)

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, value: Any) -> str | None:
        return validate_date_or_none(value)

    @field_validator("start_time", mode="before")
    @classmethod
    def validate_start_time(cls, value: Any) -> str | None:
        return validate_time_or_none(value)


class SavedScheduleUpdateInput(SavedScheduleFields):
    """저장된 일정 수정 입력."""

    schedule_id: str
    end_time: str | None = None
    attendees: list[str] | None = None

    @field_validator("schedule_id", mode="before")
    @classmethod
    def validate_schedule_id(cls, value: Any) -> str:
        return normalize_required_string(value)

    @field_validator("end_time", mode="before")
    @classmethod
    def validate_end_time(cls, value: Any) -> str | None:
        return validate_time_or_none(value)

    @field_validator("attendees", mode="before")
    @classmethod
    def validate_attendees(
        cls,
        value: Any,
    ) -> list[str] | None:
        return normalize_list_or_none(value)

    @model_validator(mode="after")
    def validate_time_order(self):
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time < self.start_time
        ):
            raise ValueError(
                "end_time은 start_time보다 빠를 수 없습니다."
            )

        return self


class SavedScheduleDeleteInput(SavedScheduleFields):
    """저장된 일정 삭제 입력."""

    schedule_ids: list[str] | None = None
    time_unspecified: bool = False
    delete_all: bool = False

    @field_validator("schedule_ids", mode="before")
    @classmethod
    def validate_schedule_ids(
        cls,
        value: Any,
    ) -> list[str] | None:
        return normalize_list_or_none(value)
