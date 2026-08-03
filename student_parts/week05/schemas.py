from __future__ import annotations

from pydantic import BaseModel, Field


class SearchPreviousConversationsInput(BaseModel):
    """외부 이전 대화 검색 입력입니다."""
    include_messages: bool = Field(default=False,description="검색된 대화방의 전체 메시지까지 함께 조회할지 여부")
    query: str=Field(description="이전 대화 검색에 사용할 짧은 핵심 검색어")
    member_names: list[str] | None =Field(default=None,description="검색할 멤버 이름 목록. 지정하지 않으면 모든 멤버를 검색")
    limit: int = Field(default=5, ge=1, le=50, description="반환할 최대 대화 수",)


class LoadConversationMessagesInput(BaseModel):
    """외부 대화 메시지 조회 입력입니다."""

    conversation_id: str


class ExtractSchedulesFromHistoryInput(BaseModel):
    """외부 멤버 일정 추출 입력입니다."""

    member_names: list[str]
    date_from: str
    date_to: str


class CreateSharedScheduleInput(BaseModel):
    """공유 일정 생성 입력입니다."""

    member_name: str
    title: str
    date: str
    start_time: str
    end_time: str = "미정"
    notes: str | None = None
    source_conversation_id: str | None = None
    schedule_id: str | None = None


class DeleteSharedScheduleInput(BaseModel):
    """공유 일정 삭제 입력입니다."""

    schedule_id: str | None = None
    source_conversation_id: str | None = None


class ListSharedSchedulesInput(BaseModel):
    """공유 일정 조회 입력입니다."""

    member_names: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None
    source_conversation_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class CollectMemberSchedulesInput(BaseModel):
    """내 일정과 외부 멤버 busy-time 수집 입력입니다."""

    member_names: list[str]
    date_from: str
    date_to: str
