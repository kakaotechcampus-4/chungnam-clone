from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.external_people_store import normalize_external_member_names
from fixed.langchain_trace import extract_agent_events, extract_final_text
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from fixed.schedule_decision import (
    CommonSlotCandidate,
    decide_final_slot_payload,
    find_common_available_slots_payload,
    normalize_date_bound,
)
from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week02_structure_natural_language_requests import (
    extract_schedule_request,
)
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts, week04_tools
from student_parts.week05_load_kanas_past_conversations import (
    collect_member_schedules,
    extract_schedules_from_history,
    list_shared_schedules,
    load_conversation_messages,
    search_previous_conversations,
    week05_prompt_parts,
)


_NANA_SUBAGENT: Any | None = None
_KANA_SUBAGENT: Any | None = None
_SUPERVISOR_AGENT: Any | None = None


def week06_system_prompt() -> str:
    """6주차 supervisor agent가 따르는 시스템 프롬프트입니다."""

    return supervisor_system_prompt()


def week06_prompt_parts() -> list[str]:
    """1~6주차 supervisor system prompt 조각을 누적합니다."""

    return [
        *week05_prompt_parts(),
        f"""
        너는 Kanana의 Week 6 supervisor다. 현재 날짜는 {current_app_date_iso()}이다.
        이전 주차의 직접 처리 지시보다 아래 위임 규칙을 최우선으로 따른다.

        너에게 공개된 nana_agent와 kana_agent는 각각 전문 하위 에이전트를 실행하는 도구다.
        사용자의 업무를 직접 처리하거나 일정·대화 내용을 추측하지 말고, 반드시 담당 하위
        에이전트에 원래 요청의 핵심 조건과 문맥을 빠짐없이 전달한다.

        다음 요청은 nana_agent에 위임한다.
        - 내 개인 일정, 할 일, 알림의 생성·조회·수정·삭제
        - 개인 참고자료와 현재 앱에 저장된 내 대화·일정 RAG 검색
        - 참석자가 있더라도 날짜와 시간이 이미 확정된 일정의 앱 DB 등록

        다음 요청은 kana_agent에 위임한다.
        - 외부 멤버의 과거 대화나 일정 조회
        - 공유 일정 저장소 row 조회
        - 내 일정과 외부 멤버 일정을 비교하는 일정 조율
        - 여러 사람의 공통 가능 시간 후보 검증과 최종 시간 결정

        "누구와 언제 가능한지 찾아줘"처럼 시간을 찾아야 하는 요청은 Kana 담당이고,
        "8월 8일 15시에 민준과 회의 등록해줘"처럼 시간이 이미 확정된 저장 요청은 Nana 담당이다.
        담당이 불분명하면 내용을 만들어 답하지 말고 가장 관련 있는 하위 에이전트가 필요한
        정보를 확인하도록 요청 전체를 그대로 위임한다.
        """,
    ]


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        *week04_prompt_parts(),
        f"""
        너는 Week 6의 Nana 하위 에이전트다. 현재 날짜는 {current_app_date_iso()}이다.
        개인 데이터와 앱 내부 저장 업무만 담당하며, 답변은 반드시 제공된 도구 결과에 근거한다.

        담당 업무는 다음과 같다.
        - 개인 일정, 할 일, 알림의 생성·조회·수정·삭제
        - 개인 참고자료 저장·검색
        - 현재 앱에 저장된 이전 대화와 구조화 기록 검색
        - 날짜와 시간이 이미 정해진 개인 또는 그룹 일정의 앱 DB 저장

        새 일정·할 일·알림은 먼저 extract_schedule_request로 구조화하고, 그 결과를
        save_structured_request에 전달한다. Week 3 이후의 조회·수정·삭제는 SQLite 저장 도구를
        기준으로 처리하며, 임시 메모리 도구를 저장된 일정의 근거로 사용하지 않는다.
        사용자가 상대 날짜를 말하면 현재 날짜를 기준으로 ISO 날짜로 해석하되, 필요한 날짜나
        대상이 불명확하면 임의로 만들지 말고 사용자에게 확인한다.

        외부 멤버의 과거 대화 조회, 여러 사람의 빈 시간 탐색, 공통 시간 후보 선택은 Kana의
        담당이다. 그런 요청이 들어오면 가능한 시간을 추측하거나 개인 일정만으로 결론 내리지 말고
        Kana 담당 요청임을 분명히 알린다. 단, 참석자가 있어도 날짜와 시간이 이미 확정된 일정의
        앱 DB 등록은 Nana가 처리한다.
        """,
    ]


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        f"""
        너는 Week 6의 Kana 하위 에이전트다. 현재 날짜는 {current_app_date_iso()}이다.
        외부 멤버의 과거 대화·일정 조회와 여러 사람의 일정 조율을 담당한다. 개인 일정 저장이나
        앱 내부 개인 RAG 결과를 추측하지 말고, 반드시 제공된 도구 결과만 근거로 답한다.

        외부 멤버의 과거 대화 내용을 찾을 때는 search_previous_conversations를 먼저 호출하고,
        검색 결과의 특정 conversation_id에 대한 전체 메시지가 필요할 때만
        load_conversation_messages를 호출한다. 외부 멤버의 일정만 조회할 때는
        extract_schedules_from_history를 사용한다. 내 일정과 외부 멤버 일정을 비교하거나 공통 시간을
        찾아야 할 때는 collect_member_schedules를 사용한다.

        일정 조회와 조율에는 date_from과 date_to가 필요하다. 오늘·내일·이번 주·다음 주처럼
        해석 가능한 표현은 현재 날짜를 기준으로 구체적인 ISO 날짜 범위로 바꾼다. 기간을 알 수
        없으면 임의로 생성하지 말고 먼저 사용자에게 물어본다. 일정 수집 결과의 ok가 false이거나
        rows를 얻지 못했으면 후보 시간을 만들지 말고 조회 실패를 설명한다.

        공통 가능 시간을 결정할 때는 다음 순서를 지킨다.
        1. collect_member_schedules로 내 일정과 대상 멤버의 busy rows를 수집한다.
        2. rows의 날짜·시작·종료 시간을 직접 비교해 겹치지 않는 candidate_slots를 고른다.
        3. 각 후보에 date, start_time, end_time, duration_minutes, reason을 모두 채우고,
           수집한 busy_rows를 그대로 find_common_available_slots에 전달해 검증한다.
        4. 검증 결과의 candidate_slots 중 하나를 직접 선택해 decide_final_slot을 호출한다.
        5. 최종 선택 근거와 함께 final_slot을 사용자에게 설명한다.

        find_common_available_slots와 decide_final_slot은 시간을 대신 골라주는 도구가 아니다.
        네가 busy_rows를 읽고 후보와 최종 선택을 인자로 제공해야 한다. 검증된 후보가 없으면
        시간을 만들어내지 말고 final_slot은 null, needs_agent_selection은 true로 둔다.

        날짜와 시간이 이미 확정된 일정의 앱 DB 저장은 Nana 담당이다. Kana는 최종 시간을 정한 뒤
        저장했다고 말하지 않으며, 사용자가 저장까지 요청하면 확정된 제목·참석자·날짜·시간을
        명확히 제시하고 Nana가 저장해야 한다고 답한다.
        """,
    ]


def nana_system_prompt() -> str:
    return join_system_prompt(nana_prompt_parts())


def kana_system_prompt() -> str:
    return join_system_prompt(kana_prompt_parts())


def supervisor_system_prompt() -> str:
    return join_system_prompt(
        [
            *week06_prompt_parts(),
            """
            실행 규칙:
            - 최종 답변 전에 반드시 nana_agent 또는 kana_agent 중 하나를 호출한다.
            - 원칙적으로 한 요청에는 주된 담당 에이전트 하나를 선택한다. 서로 다른 업무가 명시적으로
              함께 들어온 경우에만 필요한 순서대로 둘을 호출한다.
            - 하위 에이전트의 query에는 사용자의 원래 요청, 직전 문맥, 멤버, 날짜 범위, 회의 길이 등
              판단에 필요한 조건을 생략하지 않는다.
            - 하위 에이전트가 반환한 JSON의 answer와 payload만 근거로 자연스럽게 답한다.
            - final_slot_payload가 있으면 최종 시간과 근거를 정확히 전달하고, 없으면 임의로 보충하지 않는다.
            - 하위 에이전트가 추가 정보를 요청하거나 실패를 보고하면 그 내용을 사용자에게 전달한다.
            - 내부 trace, tool 이름, JSON 구조는 사용자가 요청하지 않는 한 그대로 노출하지 않는다.
            """,
        ]
    )


def _tool_call_names(events: list[dict[str, Any]]) -> list[str]:
    return [
        event["tool_name"]
        for event in events
        if event.get("event") == "tool_call" and event.get("tool_name")
    ]


def extract_langchain_trace(result: dict[str, Any]) -> dict[str, Any]:
    """Week 6 supervisor 실행 결과를 UI trace payload로 변환합니다."""

    events = extract_agent_events(result)
    inner_tool_names: list[str] = []
    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None
    selected_agent: str | None = None

    for event in events:
        if event.get("event") == "tool_call" and event.get("tool_name") in {
            "nana_agent",
            "kana_agent",
        }:
            selected_agent = event["tool_name"]
        content = event.get("content")
        if isinstance(content, dict):
            inner_tool_names.extend(content.get("inner_tool_names") or [])
            if content.get("final_slot_payload"):
                final_slot_payload = content["final_slot_payload"]
            elif "final_slot" in content:
                final_slot_payload = content
            if content.get("final_decision_payload"):
                final_decision_payload = content["final_decision_payload"]

    return {
        "events": events,
        "supervisor_selected_agent": selected_agent,
        "inner_tool_names": inner_tool_names,
        "final_slot_payload": final_slot_payload,
        "final_decision_payload": final_decision_payload,
    }


def tool_name(tool_object: Any) -> str:
    return getattr(
        tool_object, "name", getattr(tool_object, "__name__", str(tool_object))
    )


FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION = (
    "내 일정과 외부 멤버 일정에서 공통 가능 시간 후보를 검증하고 기록하는 도구다. "
    "이 도구는 후보 시간을 자동으로 계산하거나 선택하지 않는다. 호출 전에 Kana agent가 "
    "collect_member_schedules 결과의 rows를 직접 읽고, 모든 참석자의 busy time과 겹치지 않는 "
    "candidate_slots를 직접 만들어 전달해야 한다. busy_rows에는 앞선 일정 수집 결과의 rows를 "
    "가공하지 말고 그대로 복사한다. busy_rows가 없을 때만 도구가 collect_member_schedules를 "
    "호출해 일정을 수집한다. "
    "각 candidate_slots 항목은 date(YYYY-MM-DD), start_time(HH:MM), end_time(HH:MM), "
    "duration_minutes, reason을 모두 포함해야 한다. 후보는 date_from부터 date_to까지의 범위와 "
    "workday_start부터 workday_end까지의 업무 시간 안에 있어야 하고, 요청한 duration_minutes를 "
    "충족하며 어떤 busy row와도 겹치면 안 된다. member_names, date_from, date_to, "
    "duration_minutes와 필요하면 workday_start, workday_end, limit도 함께 전달한다. "
    "반환된 candidate_slots는 검증을 통과한 후보이므로 결과만 설명하고 끝내지 말고, 그 목록과 "
    "같은 busy_rows 및 요청 조건을 decide_final_slot에 전달해 Kana agent가 최종 시간을 선택해야 한다."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    "find_common_available_slots가 검증한 후보 중 Kana agent가 직접 고른 최종 시간을 기록하는 "
    "도구다. 이 도구는 가장 빠른 후보나 첫 번째 후보를 자동으로 선택하지 않는다. Kana agent가 "
    "candidate_slots를 직접 비교한 뒤 selected_index(0부터 시작) 또는 selected_slot을 명시하고, "
    "선택한 후보와 일치하는 final_slot을 'YYYY-MM-DD HH:MM-HH:MM' 형식으로 전달해야 한다. "
    "최종 시간을 확정했다면 needs_agent_selection은 false로 두고, reason에는 그 후보를 선택한 "
    "사용자-facing 근거를 적는다. 검증된 후보가 없거나 아직 선택할 수 없다면 selected_index와 "
    "selected_slot을 전달하지 말고 final_slot은 null, needs_agent_selection은 true로 둔다. "
    "근거 trace가 보존되도록 find_common_available_slots 결과의 candidate_slots와 busy_rows를 그대로 "
    "복사하고 member_names, date_from, date_to, duration_minutes도 함께 전달한다. 검증 결과에 없는 "
    "시간을 새로 만들거나, 선택 정보 없이 도구가 알아서 결정할 것이라고 기대해서는 안 된다."
)


class FindCommonAvailableSlotsInput(BaseModel):
    member_names: list[str] = Field(
        description="공통 가능 시간을 찾아야 하는 외부 멤버 이름 목록"
    )
    date_from: str = Field(
        description="조회 시작 날짜. ISO datetime이면 날짜 부분만 사용"
    )
    date_to: str = Field(
        description="조회 종료 날짜. ISO datetime이면 날짜 부분만 사용"
    )
    duration_minutes: int = Field(
        default=60, ge=30, le=480, description="회의 길이(분)"
    )
    workday_start: str = Field(default="09:00", description="허용 업무 시간 시작 HH:MM")
    workday_end: str = Field(default="18:00", description="허용 업무 시간 종료 HH:MM")
    limit: int = Field(default=5, ge=1, le=20, description="최대 후보 수")
    busy_rows: list[dict[str, Any]] | None = Field(
        default=None,
        description="앞선 일정 조회 tool output에서 복사한 busy_rows. 후보는 이 row들과 overlap/겹치면 안 됩니다.",
    )
    candidate_slots: list[CommonSlotCandidate] = Field(
        default_factory=list,
        description=(
            "LLM agent가 직접 고른 후보 목록. 각 항목은 date, start_time, end_time, "
            "duration_minutes, reason을 포함하고 busy_rows와 겹치면 안 됩니다."
        ),
    )
    llm_reason: str | None = Field(
        default=None, description="LLM agent가 후보 목록을 고른 전체 이유"
    )


class DecideFinalSlotInput(BaseModel):
    candidate_slots: list[Any] = Field(
        default_factory=list, description="find_common_available_slots 결과의 후보 목록"
    )
    selected_slot: Any | None = Field(
        default=None, description="LLM agent가 직접 고른 후보 객체"
    )
    selected_index: int | None = Field(
        default=None, description="LLM agent가 직접 고른 candidate_slots index"
    )
    final_slot: str | None = Field(
        default=None,
        description="최종 확정 시간 텍스트. 형식은 'YYYY-MM-DD HH:MM-HH:MM'. 미확정이면 null",
    )
    needs_agent_selection: bool | None = Field(
        default=None,
        description="후보 선택이 더 필요하면 true, final_slot을 확정했으면 false",
    )
    member_names: list[str] | None = Field(
        default=None, description="회의 대상 멤버 목록"
    )
    date_from: str | None = Field(default=None, description="요청 날짜 범위 시작")
    date_to: str | None = Field(default=None, description="요청 날짜 범위 종료")
    duration_minutes: int = Field(default=60, description="회의 길이(분)")
    reason: str | None = Field(
        default=None, description="최종 선택 또는 보류에 대한 사용자-facing 설명"
    )
    busy_rows: list[dict[str, Any]] | None = Field(
        default=None, description="최종 결정 근거로 남길 busy_rows"
    )


class ProposeGroupScheduleInput(BaseModel):
    """기존 호환용 그룹 일정 제안 입력입니다."""

    title: str
    member_names: list[str]
    candidate_slots: list[CommonSlotCandidate] = Field(default_factory=list)
    selected_slot: CommonSlotCandidate | None = None
    reason: str | None = None


class AgentQueryInput(BaseModel):
    """하위 에이전트 위임 입력입니다."""

    query: str


def find_common_available_slots_dict(
    member_names: list[str],
    date_from: str,
    date_to: str,
    duration_minutes: int = 60,
    workday_start: str = "09:00",
    workday_end: str = "18:00",
    limit: int = 5,
    busy_rows: list[dict[str, Any]] | None = None,
    candidate_slots: list[dict[str, Any]] | None = None,
    llm_reason: str | None = None,
) -> dict[str, Any]:
    """멤버별 busy-time rows와 LLM이 고른 후보 payload를 검증 결과로 바꿉니다."""

    normalized_members = normalize_external_member_names(member_names)
    normalized_date_from = normalize_date_bound(date_from)
    normalized_date_to = normalize_date_bound(date_to)

    resolved_busy_rows = busy_rows
    if resolved_busy_rows is None:
        raw_payload = collect_member_schedules.invoke(
            {
                "member_names": normalized_members,
                "date_from": normalized_date_from,
                "date_to": normalized_date_to,
            }
        )

        collected_payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        )

        if not isinstance(collected_payload, dict):
            raise ValueError(
                "collect_member_schedules 결과가 올바른 dict 형식이 아닙니다."
            )

        if collected_payload.get("ok") is False:
            error = collected_payload.get("error") or {}
            message = error.get("message") or "멤버 일정 조회에 실패했습니다."
            raise RuntimeError(message)

        collected_rows = collected_payload.get("rows")

        if not isinstance(collected_rows, list):
            raise ValueError("collect_member_schedules 결과의 rows가 list가 아닙니다.")

        resolved_busy_rows = collected_rows

    members_with_me = [
        "나",
        *[name for name in normalized_members if name != "나"],
    ]

    return find_common_available_slots_payload(
        member_names=members_with_me,
        date_from=normalized_date_from,
        date_to=normalized_date_to,
        busy_rows=resolved_busy_rows,
        duration_minutes=duration_minutes,
        workday_start=workday_start,
        workday_end=workday_end,
        limit=limit,
        candidate_slots=candidate_slots,
        llm_reason=llm_reason,
    )


@tool(
    description=FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION,
    args_schema=FindCommonAvailableSlotsInput,
)
def find_common_available_slots(
    member_names: list[str],
    date_from: str,
    date_to: str,
    duration_minutes: int = 60,
    workday_start: str = "09:00",
    workday_end: str = "18:00",
    limit: int = 5,
    busy_rows: list[dict[str, Any]] | None = None,
    candidate_slots: list[Any] | None = None,
    llm_reason: str | None = None,
) -> str:
    """수집된 멤버 일정에서 LLM이 직접 고른 공통 가능 후보 시간을 검증합니다."""

    result = find_common_available_slots_dict(
        member_names,
        date_from,
        date_to,
        duration_minutes,
        workday_start,
        workday_end,
        limit,
        busy_rows,
        candidate_slots,
        llm_reason,
    )
    return json.dumps(result)


@tool(description=DECIDE_FINAL_SLOT_DESCRIPTION, args_schema=DecideFinalSlotInput)
def decide_final_slot(
    candidate_slots: list[Any] | None = None,
    selected_slot: Any | None = None,
    selected_index: int | None = None,
    final_slot: str | None = None,
    needs_agent_selection: bool | None = None,
    member_names: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    duration_minutes: int = 60,
    reason: str | None = None,
    busy_rows: list[dict[str, Any]] | None = None,
) -> str:
    """LLM이 직접 고른 후보/최종 시간을 course repo payload로 기록합니다."""

    result = decide_final_slot_payload(
        candidate_slots,
        selected_slot,
        selected_index,
        final_slot,
        needs_agent_selection,
        member_names,
        date_from,
        date_to,
        duration_minutes,
        reason,
        busy_rows,
    )
    return json.dumps(result, ensure_ascii=False)


def kana_tools() -> list[Any]:
    return [
        extract_schedule_request,
        search_previous_conversations,
        load_conversation_messages,
        extract_schedules_from_history,
        list_shared_schedules,
        collect_member_schedules,
        find_common_available_slots,
        decide_final_slot,
    ]


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


@tool(args_schema=ProposeGroupScheduleInput)
def propose_group_schedule(
    title: str,
    member_names: list[str],
    candidate_slots: list[Any] | None = None,
    selected_slot: Any | None = None,
    reason: str | None = None,
) -> str:
    """Kana가 고른 후보 시간으로 최종 그룹 일정 결정 페이로드를 만듭니다."""

    slots = [
        slot.model_dump() if hasattr(slot, "model_dump") else slot
        for slot in candidate_slots or []
    ]
    selected = (
        selected_slot.model_dump()
        if hasattr(selected_slot, "model_dump")
        else selected_slot
    )
    payload = {
        "title": title,
        "members": normalize_external_member_names(member_names),
        "selected_slot": selected,
        "status": "confirmed" if selected else "needs_manual_review",
        "reason": reason,
        "candidate_slots": slots,
    }
    return json.dumps(
        {"ok": True, "tool_name": "propose_group_schedule", "final_decision": payload},
        ensure_ascii=False,
    )


@tool(args_schema=AgentQueryInput)
def nana_agent(query: str) -> str:
    """개인 일정과 개인 RAG 작업을 프롬프트 기반 Nana 하위 에이전트에게 위임합니다."""

    global _NANA_SUBAGENT

    if _NANA_SUBAGENT is None:
        _NANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=week04_tools(),
            system_prompt=nana_system_prompt(),
        )
    result = _NANA_SUBAGENT.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": query,
                }
            ]
        }
    )
    trace = extract_agent_events(result)
    answer = extract_final_text(result)
    inner_tool_names = _tool_call_names(trace)

    payload = {
        "selected_agent": "nana_agent",
        "answer": answer,
        "trace": trace,
        "inner_tool_names": inner_tool_names,
    }
    return json.dumps(payload, ensure_ascii=False)


@tool(args_schema=AgentQueryInput)
def kana_agent(query: str) -> str:
    """그룹 일정 종합 작업을 프롬프트 기반 Kana 하위 에이전트에게 위임합니다."""

    global _KANA_SUBAGENT

    if _NANA_SUBAGENT is None:
        _KANA_SUBAGENT = create_agent(
            model=chat_model(), tools=kana_tools(), system_prompt=kana_system_prompt()
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

    trace = extract_agent_events(result)
    answer = extract_final_text(result)
    inner_tool_names = _tool_call_names(trace)

    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None

    for event in trace:
        content = event.get("content")

        if not isinstance(content, dict):
            continue

        if isinstance(content.get("final_slot_payload"), dict):
            final_slot_payload = content["final_slot_payload"]
        elif "final_slot" in content:
            final_slot_payload = content

        if isinstance(content.get("final_decision_payload"), dict):
            final_decision_payload = content["final_decision_payload"]
        elif isinstance(content.get("final_decision"), dict):
            final_decision_payload = content["final_decision"]

    payload = {
        "selected_agent": "kana_agent",
        "answer": answer,
        "trace": trace,
        "inner_tool_names": inner_tool_names,
        "final_slot_payload": final_slot_payload,
        "final_decision_payload": final_decision_payload,
    }

    return json.dumps(payload, ensure_ascii=False)


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
