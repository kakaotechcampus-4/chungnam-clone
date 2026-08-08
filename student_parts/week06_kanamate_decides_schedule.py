from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from fixed.llm import chat_model
from fixed.schedule_decision import CommonSlotCandidate
from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week02_structure_natural_language_requests import extract_schedule_request
from student_parts.week04_retrieve_nanas_memory import week04_tools
from student_parts.week05_load_kanas_past_conversations import (
    collect_member_schedules,
    extract_schedules_from_history,
    list_shared_schedules,
    load_conversation_messages,
    search_previous_conversations,
    week05_prompt_parts,
)
from student_parts.week06.decision import (
    DECIDE_FINAL_SLOT_DESCRIPTION,
    FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION,
    decide_final_slot,
    find_common_available_slots,
    find_common_available_slots_dict,
    propose_group_schedule,
)
from student_parts.week06.kana import (
    kana_agent,
    kana_prompt_parts,
    kana_system_prompt,
    kana_tools,
)
from student_parts.week06.nana import nana_agent, nana_prompt_parts, nana_system_prompt
from student_parts.week06.schemas import (
    AgentQueryInput,
    DecideFinalSlotInput,
    FindCommonAvailableSlotsInput,
    ProposeGroupScheduleInput,
)
from student_parts.week06.trace import _tool_call_names, extract_langchain_trace


_SUPERVISOR_AGENT: Any | None = None


# Week 6 진입 파일은 supervisor 조립만 담당합니다.
# - Nana의 prompt/tool 실행은 student_parts.week06.nana
# - Kana의 prompt/tool 실행은 student_parts.week06.kana
# - 공통 시간 후보 검증과 최종 결정은 student_parts.week06.decision
# - 입력 스키마와 trace 변환은 각각 student_parts.week06.schemas/trace
#
# 위에서 기존 이름을 다시 import하므로 이 모듈을 사용하던 실행기와 테스트의 import 경로는 유지됩니다.


def week06_system_prompt() -> str:
    """6주차 supervisor agent가 따르는 시스템 프롬프트입니다."""

    return supervisor_system_prompt()


def week06_prompt_parts() -> list[str]:
    """1~6주차 supervisor system prompt 조각을 누적합니다."""

    return [
        *week05_prompt_parts(),
        # TODO 메인: Week 6 supervisor agent system prompt를 자유롭게 추가하세요.
        #   - supervisor는 직접 업무를 처리하지 않고 nana_agent 또는 kana_agent로만 위임합니다.
        #   - 어떤 요청이 Nana 담당이고 어떤 요청이 Kana 담당인지 판단 기준을 적습니다.
        """
        [Week 6 역할 분담]

        너는 직접 업무를 처리하지 않고 Nana 또는 Kana에게 요청을 위임하는 Supervisor다.
        이 역할 분담 규칙은 이전 주차의 직접적인 도구 사용 지시보다 우선한다.

        Nana에게 위임:
        - 개인 일정 생성, 조회, 수정, 삭제
        - 할 일과 알림
        - 개인 참고자료와 개인 대화 RAG

        Kana에게 위임:
        - 외부 멤버와 나눈 과거 대화 검색
        - 외부 멤버의 일정 조회
        - 공유 일정 조회
        - 내 일정과 외부 멤버 일정 수집
        - 그룹 일정 관련 요청

        외부 멤버나 그룹 일정이 포함된 요청은 Kana에게 위임하고,
        그렇지 않은 개인 영역 요청은 Nana에게 위임한다.
"""

    ]


def supervisor_system_prompt() -> str:
    return join_system_prompt(
        [                     
         *week06_prompt_parts(),
        """
        [Supervisor 실행 규칙]

        - 사용자 요청마다 nana_agent 또는 kana_agent 중 하나를 반드시 호출한다.
        - Supervisor가 직접 기존 주차 도구를 호출하거나 업무 결과를 추측하지 않는다.
        - 하위 Agent의 query에는 사용자 요청의 이름, 날짜, 시간과 의도를 생략하지 않고 전달한다.
        - 하위 Agent가 반환한 JSON의 answer를 근거로 최종 답변한다.
        - trace와 내부 JSON은 사용자가 요구하지 않는 한 그대로 노출하지 않는다.
        """
        ]
    )


def tool_name(tool_object: Any) -> str:
    return getattr(tool_object, "name", getattr(tool_object, "__name__", str(tool_object)))


def supervisor_tools() -> list[Any]:
    return [nana_agent, kana_agent]


def agent_tool_names(agent_name: str) -> list[str]:
    if agent_name == "nana_agent":
        return [tool_name(item) for item in week04_tools()]
    if agent_name == "kana_agent":
        return [tool_name(item) for item in kana_tools()]
    if agent_name == "supervisor":
        return [tool_name(item) for item in supervisor_tools()]
    return []


def build_langchain_supervisor_agent() -> object:
    """nana_agent와 kana_agent 위임 도구만 노출하는 LangChain v1 슈퍼바이저입니다."""

    global _SUPERVISOR_AGENT
    if _SUPERVISOR_AGENT is None:
        _SUPERVISOR_AGENT = create_agent(
            model=chat_model(),
            tools=supervisor_tools(),
            system_prompt=supervisor_system_prompt(),
        )
    return _SUPERVISOR_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_langchain_supervisor_agent()
