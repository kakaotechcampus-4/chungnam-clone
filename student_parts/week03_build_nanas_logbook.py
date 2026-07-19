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
# from student_parts.week02_structure_natural_language_requests import (
#     week02_prompt_parts,
# )
from student_parts.week03.save import save_request
from student_parts.week03.read import (
    list_saved_requests,
    get_saved_request,
    personal_list_saved_schedules,
)
from student_parts.week03.update import personal_update_saved_schedule
from student_parts.week03.delete import personal_delete_saved_schedules
from student_parts.week03.confirmation import confirm_pending_schedule_action

_WEEK03_AGENT: Any | None = None

SQLITE_MEMORY_PROMPT = """
일정, 할 일, 알림은 대화 내용에만 의존하지 않고, SQLite 저장 도구를 사용해라.
새 대화에서 이전 기록을 질문받으면 저장된 데이터를 조회해라.
"""

WEEK03_TOOL_CALL_PROMPT = """
새 일정, 할 일, 알림을 저장할 때는
save_request를 호출한다.

사용자가 “내 일정”, “내 일정 전부”, “약속”, “회의”를 조회하면
personal_list_saved_schedules를 호출한다.
사용자가 개인 일정 또는 그룹 일정을 명확히 구분하지 않았다면
kind를 전달하지 않는다.

사용자가 할 일을 조회하면
list_saved_requests를 kind="todo"로 호출한다.

사용자가 알림을 조회하면
list_saved_requests를 kind="reminder"로 호출한다.

사용자가 일정, 할 일, 알림을 포함한 저장 요청 전체를 명확히 요구하면
list_saved_requests를 kind 없이 호출한다.

일정 수정·삭제 요청을 받으면
먼저 personal_list_saved_schedules로 후보를 조회한다.
후보를 조회할 때 personal_schedule인지 group_schedule인지 추측하지 않는다.
사용자가 종류를 명확히 지정하지 않았다면 kind를 전달하지 않는다.

후보 조회 결과의 schedule_id, title, attendees를 확인하여
사용자가 말한 대상과 일치하는 일정을 선택한다.
제목에 참석자 이름이 포함되어 있더라도
실제 저장된 title과 attendees가 분리되어 있을 수 있음을 고려한다.

수정할 일정의 schedule_id를 확인한 뒤
personal_update_saved_schedule을 호출한다.

삭제할 일정의 schedule_id를 확인한 뒤
personal_delete_saved_schedules를 호출한다.

todo와 reminder는 이번 과제에서 조회만 지원한다.
todo 또는 reminder의 수정·삭제 요청에는
일정 수정·삭제 도구를 호출하지 않는다.

사용자가 승인을 하는 말을 하면
confirm_pending_schedule_action을 confirm=true로 호출한다.

사용자가 취소하거나 거절하는 말을 하면
confirm_pending_schedule_action을 confirm=false로 호출한다.

사용자가 다른 일정을 수정·삭제해 달라고 요청하면
새로운 수정·삭제 도구를 호출하여 확인 대상을 교체한다.

사용자의 승인 없이 confirm=true를 호출하지 않는다.
schedule_id를 추측하지 않는다.
"""


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
        personal_delete_saved_schedules,
        confirm_pending_schedule_action
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
        _WEEK03_AGENT = create_agent(
            model=chat_model(),
            tools=week03_tools(),
            system_prompt=week03_system_prompt(),
        )
    return _WEEK03_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week03_agent()


def what_is_time():
    now = datetime.fromisoformat(app_started_at_iso())
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return now.strftime("%Y-%m-%d %H:%M:%S") + f" {weekdays[now.weekday()]}요일"
