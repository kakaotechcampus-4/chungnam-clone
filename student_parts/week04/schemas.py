from typing import Literal

from pydantic import BaseModel, Field


class AddPersonalReferenceInput(BaseModel):
    """개인 참고자료 추가 입력입니다."""

    title: str
    content: str
    tags: list[str] | None = None


class SearchPersonalReferencesInput(BaseModel):
    """개인 참고자료 검색 입력입니다."""

    query: str
    top_k: int = Field(default=2, ge=1, le=20)


class SearchSavedRequestsInput(BaseModel):
    """SQLite 저장 요청 검색 입력입니다."""

    query: str
    top_k: int = Field(default=3, ge=1, le=50)


# class SearchConversationMessagesInput(BaseModel):
#     """앱 대화 RAG 검색 입력입니다."""

#     query: str
#     top_k: int = Field(default=5, ge=1, le=50)
#     conversation_id: str | None = None


class SearchNanaMemoryInput(BaseModel):
    """Week 4 호환 통합 검색 입력입니다."""

    query: str
    date_from: str | None = None
    date_to: str | None = None
    attendee: str | None = None
    limit: int = Field(default=5, ge=1, le=20)

class SearchConversationMessagesInput(BaseModel):
    """앱 대화 RAG 검색 입력입니다."""
    query: str
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
    )

class LoadConversationContextInput(BaseModel):
    conversation_id: str = Field(min_length=1)

class MemoryRoute(BaseModel):
    """기억 검색 출처와 검색어입니다."""

    source: Literal[
        "structured",
        "conversation",
        "reference",
    ]
    search_query: str = Field(min_length=1)