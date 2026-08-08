from __future__ import annotations

import json
from typing import Any
from langchain.agents.structured_output import ToolStrategy
from langchain.agents import create_agent
from langchain_core.tools import tool
from fixed.runtime_clock import current_app_date_iso
from fixed.langchain_trace import extract_agent_events
from fixed.llm import chat_model
from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week02_structure_natural_language_requests import extract_schedule_request
from student_parts.week05_load_kanas_past_conversations import (
    collect_member_schedules,
    extract_schedules_from_history,
    list_shared_schedules,
    load_conversation_messages,
    search_previous_conversations,
)
from student_parts.week06.decision import decide_final_slot, find_common_available_slots
from student_parts.week06.schemas import AgentQueryInput, SubagentResponse
from student_parts.week06.trace import _tool_call_names


_KANA_SUBAGENT: Any | None = None


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        # TODO 메인: Week 6 Kana 하위 에이전트 전용 system prompt를 자유롭게 추가하세요.
        #   - 다른 주차 prompt를 누적하지 않으므로 Kana 역할을 처음부터 작성해야 합니다.
        #   - 외부 멤버 일정/공통 가능 시간/그룹 조율을 담당하고, 확정된 일정 저장은 Nana 담당이라고 답하게 합니다.
        #   - 추가 과제를 구현했다면 find_common_available_slots와 decide_final_slot까지 이어서 호출하도록 지시합니다.
    f"""
    [Week 6 Kana 역할]
    너는 Supervisor에게 외부 멤버와 그룹 일정 업무를 위임받아 처리하는 Kana다.
    
    담당 업무:
    - 외부 멤버와 나눈 과거 대화 검색 및 내용 조회
    - 과거 대화에서 외부 멤버의 일정 조회
    - 공유 일정 저장소 조회
    - 내 일정과 외부 멤버 일정을 함께 수집
    
    현재 날짜: {current_app_date_iso()}

    처리 규칙:
    - 일정과 대화 정보는 반드시 제공된 도구를 호출하여 확인한다.
    - 도구 결과에 없는 일정이나 대화 내용을 추측하지 않는다.
    - 내 일정과 외부 멤버 일정을 함께 확인할 때는 collect_member_schedules를 사용한다.
    - 현재 메인 과제 단계에서는 공통 가능 시간을 직접 계산하거나 최종 시간을 확정하지 않는다.
    - 개인 일정의 생성·수정·삭제, 할 일·알림과 개인 RAG는 Nana 담당이다.
    - 담당 밖의 요청은 직접 처리하지 말고 Nana 담당이라고 알린다.
    - 도구 결과를 근거로 간결하게 답한다.
    
    도구 사용 가이드:
    - search_previous_conversations는 외부 멤버의 과거 대화를 검색할 때 사용한다.
    query에는 짧은 핵심 검색어를 넣고, 사용자가 멤버를 지정했다면 member_names에 반드시 전달한다.
    대화 후보만 찾을 때는 include_messages=false, 검색과 함께 전체 내용도 필요하면 true로 설정한다.
    - load_conversation_messages는 검색 결과로 받은 conversation_id의 전체 메시지가 필요할 때 사용한다.
    conversation_id를 추측하지 않으며, include_messages=true로 이미 내용을 받았다면 중복 호출하지 않는다.
    - extract_schedules_from_history는 외부 멤버의 특정 날짜 또는 기간 일정을 조회할 때 사용한다.
    - list_shared_schedules는 과거 대화가 아니라 공유 일정 저장소에 등록된 일정을 조회할 때 사용한다.
    - collect_member_schedules는 내 일정과 외부 멤버 일정을 함께 조회할 때 사용한다.

    날짜 규칙:
    - 오늘, 내일 같은 상대 날짜는 현재 날짜를 기준으로 YYYY-MM-DD로 변환한다.
    - date_from과 date_to에는 YYYY-MM-DD 형식을 사용하며, 하루만 조회하면 두 값에 같은 날짜를 넣는다.
    - 필요한 날짜 범위를 사용자 요청에서 결정할 수 없다면 임의로 추측하지 말고 다시 질문한다.
    """
    ]


def kana_system_prompt() -> str:
    return join_system_prompt(kana_prompt_parts())


def kana_tools() -> list[Any]:
    return [
        extract_schedule_request,
        search_previous_conversations,
        load_conversation_messages,
        extract_schedules_from_history,
        list_shared_schedules,
        collect_member_schedules,
        #find_common_available_slots,
        #decide_final_slot,
    ]


@tool(args_schema=AgentQueryInput)
def kana_agent(query: str) -> str:
    """그룹 일정 종합 작업을 프롬프트 기반 Kana 하위 에이전트에게 위임합니다."""

    # TODO 메인: Kana 하위 agent를 실행하고 trace에서 final_slot_payload/final_decision_payload를 끌어올려 반환하세요.
    #   - _KANA_SUBAGENT를 kana_tools()와 kana_system_prompt()로 한 번만 만들고 재사용합니다.
    #   - trace event의 content를 훑어 final_slot이 들어 있는 dict와 final_decision 값을 찾습니다.
    #   - answer, trace, inner_tool_names, final_slot_payload, final_decision_payload를 JSON으로 반환합니다.
    global _KANA_SUBAGENT

    if _KANA_SUBAGENT is None:
        _KANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=kana_tools(),
            response_format=ToolStrategy(SubagentResponse),
            system_prompt=kana_system_prompt(),
        )

    result = _KANA_SUBAGENT.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": query,
                }
            ]
        }
    )

    events = extract_agent_events(result)
    structured_response = SubagentResponse.model_validate(result.get("structured_response"))
    final_slot_payload = None
    final_decision_payload = None

    for event in events:
        content = event.get("content")

        if not isinstance(content, dict):
            continue

        if content.get("final_slot_payload"):
            final_slot_payload = content["final_slot_payload"]
        elif "final_slot" in content:
            final_slot_payload = content

        if content.get("final_decision_payload"):
            final_decision_payload = content["final_decision_payload"]
        elif content.get("final_decision"):
            final_decision_payload = content["final_decision"]

    return json.dumps(
        {
            "selected_agent": "kana_agent",
            "answer": structured_response.answer,
            "condition_value": structured_response.condition_value,
            "trace": events,
            "inner_tool_names": _tool_call_names(events),
            "final_slot_payload": final_slot_payload,
            "final_decision_payload": final_decision_payload,
        },
        ensure_ascii=False,
    )
