from __future__ import annotations
from typing import Any
from langchain.agents import create_agent
from fixed.config import CONFIG
from fixed.llm import chat_model
from student_parts.week04_retrieve_nanas_memory import week04_tools
from student_parts.week05.common import (
    call_mcp_tool,
    call_mcp_tool_sync,
    json_payload,
    load_langchain_mcp_tools,
    load_langchain_mcp_tools_sync,
)
from student_parts.week05.history_tools import (
    extract_schedules_from_history,
    load_conversation_messages,
    search_previous_conversations,
)
from student_parts.week05.member_schedules import (
    _collect_member_schedules,
    _personal_schedules_for_current_scope,
    _structured_request_from_schedule_row,
    collect_member_schedules,
)
from student_parts.week05.prompts import week05_prompt_parts, week05_system_prompt
from student_parts.week05.schemas import (
    CollectMemberSchedulesInput,
    CreateSharedScheduleInput,
    DeleteSharedScheduleInput,
    ExtractSchedulesFromHistoryInput,
    ListSharedSchedulesInput,
    LoadConversationMessagesInput,
    SearchPreviousConversationsInput,
)
from student_parts.week05.shared_tools import (
    create_shared_schedule,
    delete_shared_schedule,
    list_shared_schedules,
)
import student_parts.week05.sync_retry


_WEEK05_AGENT: Any | None = None

def week05_tools() -> list[Any]:
    """4주차까지의 도구에 외부 SQLite/MCP 일정 도구를 누적한 목록입니다."""

    return [
        *week04_tools(),
        search_previous_conversations,
        extract_schedules_from_history,
        # create_shared_schedule,
        # delete_shared_schedule,
        list_shared_schedules,
        collect_member_schedules,
    ]


def build_week05_agent() -> object:
    """Week 1-5 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK05_AGENT
    if _WEEK05_AGENT is None:
        _WEEK05_AGENT = create_agent(
            model=chat_model(),
            tools=week05_tools(),
            system_prompt=week05_system_prompt(),
        )
    return _WEEK05_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week05_agent()
