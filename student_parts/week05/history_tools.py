from __future__ import annotations

from langchain_core.tools import tool

from student_parts.week05.common import call_mcp_tool_sync
from student_parts.week05.schemas import (
    ExtractSchedulesFromHistoryInput,
    LoadConversationMessagesInput,
    SearchPreviousConversationsInput,
)


@tool(args_schema=SearchPreviousConversationsInput)
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
    include_messages: bool = False,
) -> str:
    """외부 이전 대화를 검색합니다. 상세 내용이 필요하면 전체 메시지까지 함께 조회합니다."""

    return call_mcp_tool_sync(
        "search_previous_conversations_with_messages",
        {
            "query": query,
            "member_names": member_names,
            "limit": limit,
            "include_messages": include_messages,
        },
    )

@tool(args_schema=LoadConversationMessagesInput)
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다."""

    return call_mcp_tool_sync(
    "load_conversation_messages",
    {"conversation_id": conversation_id},
)

@tool(args_schema=ExtractSchedulesFromHistoryInput)
def extract_schedules_from_history(member_names: list[str], date_from: str, date_to: str) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다."""

    return call_mcp_tool_sync(
        "extract_schedules_from_history",
        {
            "member_names": member_names,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
