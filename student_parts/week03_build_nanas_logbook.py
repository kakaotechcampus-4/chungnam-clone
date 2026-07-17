from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from fixed.config import CONFIG
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from fixed.app_store import AppSQLiteStore
from student_parts.week01_wake_up_nana import (
    join_system_prompt,
    personal_create_schedule as week01_personal_create_schedule,
    week01_tools,
)
from student_parts.week02_structure_natural_language_requests import (
    RequestKind,
    StructuredRequest,
    extract_schedule_request,
    extract_structured_request,
    week02_prompt_parts,
)
from student_parts.week03_helpers import (
    _delete_saved_schedules,
    _save_input_from,
    _store,
    _tool_name,
    delete_saved_schedules_dict,
    json_payload,
    save_structured_request_payload,
    structured_request_from_week01_schedule,
    tool_result,
)
from student_parts.week03_models import (
    SaveStructuredRequestInput,
    SavedRequestGetInput,
    SavedRequestListInput,
    SavedScheduleDeleteInput,
    SavedScheduleListInput,
    SavedScheduleUpdateInput,
)
from student_parts.week03_tools import (
    get_saved_request,
    list_saved_requests,
    personal_create_schedule,
    personal_delete_saved_schedules,
    personal_list_saved_schedules,
    personal_update_saved_schedule,
    save_structured_request,
    week03_tools,
)


_WEEK03_AGENT: Any | None = None

SQLITE_MEMORY_PROMPT = """
Week 3부터 일정/할 일/알림은 앱 SQLite DB에 영속 저장된다.
저장된 기록은 새 대화를 시작하거나 앱을 다시 시작해도 유지되며, 저장 tool로 조회할 수 있다.
Week 1의 현재 대화 임시 메모리 규칙은 SQLite 영속 메모리 규칙으로 대체한다.
"""

WEEK03_TOOL_CALL_PROMPT = """
Week 3 tool 호출 규칙:
- 저장 요청은 extract_schedule_request(query=사용자 요청)를 먼저 호출한 뒤 save_structured_request를 호출한다.
- save_structured_request에는 structured_request 내부의
  kind/title/date/start_time/end_time/members/priority/reason/original_text 필드만 전달한다.
  ok/tool_name/base_date 또는 결과 JSON 전체를 전달하지 않는다.
- 저장된 일정은 personal_list_saved_schedules로 조회한다.
- 구조화 요청 기록은 list_saved_requests 또는 get_saved_request로 조회한다.
- 날짜가 명확한 조회는 date_from/date_to에 YYYY-MM-DD 형식으로 전달한다.
"""


def week03_system_prompt() -> str:
    """3주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week03_prompt_parts())


def week03_prompt_parts() -> list[str]:
    """1~3주차 system prompt 조각을 누적합니다."""

    return [
        *week02_prompt_parts(),
        """
        WEEK 3:
        너는 Kanana Schedule Agent다.
        Week 2의 "tool을 다시 호출하지 않고 structured_response로 만든다"는 지시는 Week 3부터 적용하지 않는다.
        구조화 결과는 저장 tool 호출로 이어가고, 최종 답변은 자연스러운 한국어 문장으로 한다.
        """,
        SQLITE_MEMORY_PROMPT,
        WEEK03_TOOL_CALL_PROMPT,
        f"""
        오늘 날짜는 {current_app_date_iso()}이다. 상대 날짜는 오늘 날짜를 기준으로 해석한다.
        Week 3 tool 선택 기준:
        - 일정/할 일/알림을 만들거나 저장하는 요청은 personal_create_schedule이 아니라
          extract_schedule_request → save_structured_request 흐름으로 처리한다.
        - 저장된 일정 조회에는 personal_list_saved_schedules를 사용하고,
          personal_list_schedules는 현재 대화에서 만든 임시 일정을 볼 때만 사용한다.
        이번 주차의 범위: SQLite 저장/조회까지 다루며, RAG와 외부 멤버 일정 조율은 하지 않는다.
        """,
    ]


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
