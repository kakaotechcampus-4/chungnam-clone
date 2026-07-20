from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from datetime import datetime
from fixed.config import CONFIG
from fixed.llm import chat_model
from fixed.runtime_clock import app_started_at_iso
from student_parts.week01_wake_up_nana import (
    join_system_prompt,
    # week01_tools,
)
from student_parts.week03.router import PendingRoutedAgent
from student_parts.week03.prompts import (
    SQLITE_MEMORY_PROMPT,
    WEEK03_TOOL_CALL_PROMPT,
)
from student_parts.week03.save import save_request
from student_parts.week03.read import (
    list_saved_requests,
    get_saved_request,
    personal_list_saved_schedules,
)
from student_parts.week03.update import personal_update_saved_schedule
from student_parts.week03.delete import personal_delete_saved_schedules

_WEEK03_AGENT: Any | None = None

# def _tool_name(item: Any) -> str:
#     return getattr(item, "name", getattr(item, "__name__", str(item)))


def week03_tools() -> list[Any]:
    # base_tools = [
    # item
    # for item in week01_tools()
    # if _tool_name(item) not in {
    #     "personal_create_schedule",
    #     "personal_delete_schedule",
    # }]

    return [
        # *base_tools,
        save_request,
        list_saved_requests,
        get_saved_request,
        personal_list_saved_schedules,
        personal_update_saved_schedule,
        personal_delete_saved_schedules
    ]


def week03_system_prompt() -> str:
    """3주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week03_prompt_parts())


def week03_prompt_parts() -> list[str]:
    """1~3주차 system prompt 조각을 누적합니다."""

    return [
        # *week02_prompt_parts(),
        SQLITE_MEMORY_PROMPT,
        WEEK03_TOOL_CALL_PROMPT,
       f"""
        현재 시각은 {what_is_time()}이다.

        이번 주차의 역할은 구조화된 요청을 SQLite에 저장하고,
        저장된 기록을 조회·수정·삭제하는 것이다.
        """
        
    ]

#        Week 3에서는 Week 2의 'SQLite에 저장하지 않는다'는 규칙과
#        'StructuredRequestBatch만 반환한다'는 규칙을 적용하지 않는다.

def build_week03_agent() -> object:
    """Week 1-3 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK03_AGENT
    if _WEEK03_AGENT is None:
        main_agent = create_agent(
            model=chat_model(),
            tools=week03_tools(),
            system_prompt=week03_system_prompt(),
        )
        _WEEK03_AGENT=PendingRoutedAgent(main_agent)
    return _WEEK03_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week03_agent()


def what_is_time():
    now = datetime.fromisoformat(app_started_at_iso())
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return now.strftime("%Y-%m-%d %H:%M:%S") + f" {weekdays[now.weekday()]}요일"
