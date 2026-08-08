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
from student_parts.week02_structure_natural_language_requests import extract_schedule_request
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


WEEK06_SUPERVISOR_PROMPT = (
    "[Week 6] 너는 Nana와 Kana를 관리하는 supervisor다. 이 지시는 이전 주차의 모든 지시보다 우선한다. "
    "이전 주차 프롬프트에 나온 일정 도구 이름들은 하위 에이전트가 쓰는 것이고, 너는 그 도구를 직접 호출할 수 없다. "
    "너에게 있는 도구는 nana_agent와 kana_agent 두 개뿐이며, 반드시 그중 하나를 호출한다. "
    "\n"
    "판단은 '참석자가 있는지'가 아니라 '시간이 정해져 있는지'로 한다. "
    "\n"
    "kana_agent에 위임하는 요청: 시간이 아직 정해지지 않아 조율이 필요한 경우. "
    "예를 들어 '민준이랑 회의 시간 잡아줘', '팀원들이랑 언제 만날까', '다음 주에 다 같이 되는 시간 찾아줘', "
    "'민준이 언제 바쁜지 알려줘', '철수가 예전에 뭐라고 했었지' 같은 요청이다. "
    "\n"
    "nana_agent에 위임하는 요청: 시간이 이미 확정되어 저장·조회·수정·삭제만 하면 되는 경우. "
    "참석자가 함께 언급되어도 날짜와 시간이 이미 정해져 있으면 조율할 것이 없으므로 nana_agent 담당이다. "
    "예를 들어 '8월 20일 15시부터 16시까지 민준과 프로젝트 킥오프 저장해줘'는 참석자가 있지만 "
    "시간이 확정되어 있으므로 nana_agent에 위임한다. "
    "'내 일정 보여줘', '내일 3시에 치과 예약 저장해줘', '그 일정 삭제해줘', '메모해둬'도 nana_agent 담당이다. "
    "\n"
    "요청 안에 조율과 저장이 모두 필요하면(예: '민준이랑 시간 잡고 저장해줘') "
    "먼저 kana_agent로 시간을 정한 뒤, 그 결과를 kana_agent 답변에서 읽어 nana_agent에 저장을 위임한다."
)


def week06_prompt_parts() -> list[str]:
    """1~6주차 supervisor system prompt 조각을 누적합니다."""

    return [
        *week05_prompt_parts(),
        WEEK06_SUPERVISOR_PROMPT,
    ]


NANA_ROLE_PROMPT = (
    "[Week 6 Nana] 너는 사용자 개인 업무를 담당하는 Nana 하위 에이전트다. "
    "개인 일정 생성/조회/수정/삭제, 할 일과 알림 저장, 개인 참고자료 추가와 검색, 앱 대화 검색이 네 담당이다. "
    "외부 멤버의 일정 조회나 여러 사람의 회의 시간 조율은 네 담당이 아니다. "
    "다른 사람과 시간을 맞춰야 하는 요청('OO이랑 회의 시간 잡아줘' 등)을 받으면, "
    "사용자에게 되묻지 말고 도구도 호출하지 말고 "
    "'여러 사람의 시간 조율은 Kana 담당입니다'라고만 짧게 답한다. "
    "네가 담당하는 일은 반드시 도구를 호출해서 처리하고, 도구 결과를 근거로 답한다."
)


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        *week04_prompt_parts(),
        NANA_ROLE_PROMPT,
    ]


KANA_ROLE_PROMPT = (
    "[Week 6 Kana] 너는 여러 사람의 일정 조율을 담당하는 Kana 하위 에이전트다. "
    f"오늘 날짜는 {current_app_date_iso()}이다. "
    "외부 멤버의 과거 대화 검색, 멤버별 바쁜 시간(busy-time) 조회, 공유 일정 저장소 확인, "
    "여러 사람의 공통 가능 시간 후보 검증과 최종 회의 시간 결정이 네 담당이다. "
    "사용자 개인 일정을 저장하거나 수정하는 일은 Nana 담당이므로, 확정한 시간을 저장해야 하면 "
    "'확정된 일정 저장은 Nana 담당입니다'라고 함께 알린다."
)

KANA_TOOL_FLOW_PROMPT = (
    "[Week 6 Kana] 도구 사용 순서는 다음과 같다. "
    "자연어 요청은 필요하면 extract_schedule_request로 먼저 구조화한다. "
    "특정 멤버가 과거에 무슨 말을 했는지 찾을 때는 search_previous_conversations로 대화를 찾고, "
    "그 결과의 conversation_id로 load_conversation_messages를 호출해 전체 내용을 읽는다. "
    "여러 사람의 바쁜 시간을 모을 때는 collect_member_schedules를 사용한다. "
    "공유 일정 저장소 row 자체를 확인할 때는 list_shared_schedules를 사용한다."
)

KANA_DECISION_FLOW_PROMPT = (
    "[Week 6 Kana] 회의 시간을 잡아달라는 요청을 받으면, 먼저 조회할 날짜 범위가 요청에 있는지 확인한다. "
    "'다음 주', '8월 10일부터 14일까지'처럼 기간을 특정할 수 있는 표현이 없으면 "
    "날짜 범위를 임의로 정하지 말고 도구를 호출하지 않는다. "
    "대신 사용자에게 어느 기간에 회의를 잡을지 되묻고, 예시로 '다음 주' 또는 "
    "'8월 10일부터 14일까지' 같은 형식을 함께 안내한다. "
    "일정 조율은 잘못 확정하면 실제 약속에 영향을 주므로, 기간이 불분명한 상태에서 추측으로 진행하지 않는다. "
    "\n"
    "날짜 범위가 확인된 뒤에는 세 단계를 반드시 이어서 수행한다. "
    "첫째, collect_member_schedules로 나와 대상 멤버의 busy_rows를 모은다. "
    "둘째, 그 busy_rows를 직접 읽고 겹치지 않는 후보 시간을 스스로 골라 "
    "find_common_available_slots에 candidate_slots로 넘겨 검증한다. "
    "셋째, 검증된 후보 중 하나를 직접 선택해 decide_final_slot에 final_slot과 selected_index를 넘겨 확정한다. "
    "후보 검증에서 답변을 끝내지 말고 반드시 decide_final_slot까지 호출한다. "
    "도구가 후보나 최종 시간을 계산해 주지 않으므로, 계산과 선택은 네가 직접 한다. "
    "\n"
    "회의 길이가 명시되지 않은 경우에는 기본값 60분으로 진행하되, 최종 답변에 "
    "'1시간 기준으로 찾았다'는 점을 함께 알려 사용자가 조정할 수 있게 한다."
)


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        KANA_ROLE_PROMPT,
        KANA_TOOL_FLOW_PROMPT,
        KANA_DECISION_FLOW_PROMPT,
    ]


def nana_system_prompt() -> str:
    return join_system_prompt(nana_prompt_parts())


def kana_system_prompt() -> str:
    return join_system_prompt(kana_prompt_parts())


def supervisor_system_prompt() -> str:
    return join_system_prompt(
        [
            *week06_prompt_parts(),
            "[Week 6] 사용자 요청을 받으면 반드시 nana_agent 또는 kana_agent 중 하나를 호출한 뒤, "
            "그 도구가 돌려준 answer와 trace만 근거로 최종 답변을 만든다. "
            "하위 에이전트를 호출하지 않고 직접 답하거나, 도구 결과에 없는 내용을 추측해서 답하지 않는다. "
            "kana_agent가 final_slot_payload를 돌려줬으면 그 최종 시간을 답변에 그대로 인용한다.",
        ]
    )


def _tool_call_names(events: list[dict[str, Any]]) -> list[str]:
    return [event["tool_name"] for event in events if event.get("event") == "tool_call" and event.get("tool_name")]


def extract_langchain_trace(result: dict[str, Any]) -> dict[str, Any]:
    """Week 6 supervisor 실행 결과를 UI trace payload로 변환합니다."""

    events = extract_agent_events(result)
    inner_tool_names: list[str] = []
    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None
    selected_agent: str | None = None

    for event in events:
        if event.get("event") == "tool_call" and event.get("tool_name") in {"nana_agent", "kana_agent"}:
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
    return getattr(tool_object, "name", getattr(tool_object, "__name__", str(tool_object)))


FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION = (
    "여러 사람의 공통 가능 시간 후보를 검증합니다. "
    "이 도구는 후보 시간을 계산해 주지 않습니다. 호출하는 agent가 busy_rows를 직접 읽고 "
    "겹치지 않는 후보를 스스로 골라 candidate_slots로 넘겨야 합니다. "
    "candidate_slots의 각 항목은 date('YYYY-MM-DD'), start_time('HH:MM'), end_time('HH:MM'), "
    "duration_minutes(정수), reason(이 시간을 고른 이유)을 포함해야 합니다. "
    "각 후보는 busy_rows의 어떤 row와도 시간이 겹치면 안 되고, workday_start~workday_end 범위 안에 있어야 합니다. "
    "busy_rows는 앞서 호출한 collect_member_schedules 결과의 rows를 그대로 복사해서 넘깁니다. "
    "busy_rows를 넘기지 않으면 이 도구가 collect_member_schedules를 대신 호출해 수집합니다. "
    "이 도구의 결과로 답변을 끝내지 말고, 검증된 후보 중 하나를 골라 decide_final_slot을 반드시 이어서 호출하세요."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    "회의 최종 시간을 기록합니다. "
    "이 도구는 최종 시간을 자동으로 골라 주지 않습니다. 호출하는 agent가 candidate_slots 중 하나를 직접 선택해 "
    "selected_index(또는 selected_slot)와 final_slot을 함께 넘겨야 합니다. "
    "final_slot 형식은 'YYYY-MM-DD HH:MM-HH:MM'입니다. "
    "시간을 확정했으면 needs_agent_selection은 false로, 아직 고르지 못했으면 final_slot을 null로 두고 "
    "needs_agent_selection을 true로 넘깁니다. "
    "reason에는 왜 그 시간을 골랐는지(또는 왜 확정하지 못했는지) 사용자에게 보여줄 설명을 적습니다. "
    "근거를 남기기 위해 candidate_slots, busy_rows, member_names, date_from, date_to도 함께 넘깁니다."
)


class FindCommonAvailableSlotsInput(BaseModel):
    member_names: list[str] = Field(description="공통 가능 시간을 찾아야 하는 외부 멤버 이름 목록")
    date_from: str = Field(description="조회 시작 날짜. ISO datetime이면 날짜 부분만 사용")
    date_to: str = Field(description="조회 종료 날짜. ISO datetime이면 날짜 부분만 사용")
    duration_minutes: int = Field(default=60, ge=30, le=480, description="회의 길이(분)")
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
    llm_reason: str | None = Field(default=None, description="LLM agent가 후보 목록을 고른 전체 이유")


class DecideFinalSlotInput(BaseModel):
    candidate_slots: list[Any] = Field(default_factory=list, description="find_common_available_slots 결과의 후보 목록")
    selected_slot: Any | None = Field(default=None, description="LLM agent가 직접 고른 후보 객체")
    selected_index: int | None = Field(default=None, description="LLM agent가 직접 고른 candidate_slots index")
    final_slot: str | None = Field(
        default=None,
        description="최종 확정 시간 텍스트. 형식은 'YYYY-MM-DD HH:MM-HH:MM'. 미확정이면 null",
    )
    needs_agent_selection: bool | None = Field(
        default=None,
        description="후보 선택이 더 필요하면 true, final_slot을 확정했으면 false",
    )
    member_names: list[str] | None = Field(default=None, description="회의 대상 멤버 목록")
    date_from: str | None = Field(default=None, description="요청 날짜 범위 시작")
    date_to: str | None = Field(default=None, description="요청 날짜 범위 종료")
    duration_minutes: int = Field(default=60, description="회의 길이(분)")
    reason: str | None = Field(default=None, description="최종 선택 또는 보류에 대한 사용자-facing 설명")
    busy_rows: list[dict[str, Any]] | None = Field(default=None, description="최종 결정 근거로 남길 busy_rows")


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
    normalized_from = normalize_date_bound(date_from)
    normalized_to = normalize_date_bound(date_to)

    # busy_rows가 없으면 Week 5 도구로 내 일정과 외부 멤버 busy-time을 직접 모은다.
    if busy_rows is None:
        collected_text = collect_member_schedules.invoke(
            {
                "member_names": normalized_members,
                "date_from": normalized_from,
                "date_to": normalized_to,
            }
        )
        try:
            collected = json.loads(collected_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            collected = {}
        busy_rows = collected.get("rows") or []

    slots = [
        slot.model_dump() if hasattr(slot, "model_dump") else slot
        for slot in (candidate_slots or [])
    ]

    # 내 일정도 조율 근거이므로 member_names에 "나"를 함께 포함한다.
    return find_common_available_slots_payload(
        member_names=["나", *[name for name in normalized_members if name != "나"]],
        date_from=normalized_from,
        date_to=normalized_to,
        busy_rows=busy_rows,
        duration_minutes=duration_minutes,
        workday_start=workday_start,
        workday_end=workday_end,
        limit=limit,
        candidate_slots=slots,
        llm_reason=llm_reason,
    )


@tool(description=FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION, args_schema=FindCommonAvailableSlotsInput)
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

    payload = find_common_available_slots_dict(
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        duration_minutes=duration_minutes,
        workday_start=workday_start,
        workday_end=workday_end,
        limit=limit,
        busy_rows=busy_rows,
        candidate_slots=candidate_slots,
        llm_reason=llm_reason,
    )
    return json.dumps(payload, ensure_ascii=False)


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

    slots = [
        slot.model_dump() if hasattr(slot, "model_dump") else slot
        for slot in (candidate_slots or [])
    ]
    selected = selected_slot.model_dump() if hasattr(selected_slot, "model_dump") else selected_slot

    payload = decide_final_slot_payload(
        candidate_slots=slots,
        selected_slot=selected,
        selected_index=selected_index,
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        duration_minutes=duration_minutes,
        final_slot=final_slot,
        needs_agent_selection=needs_agent_selection,
        reason=reason,
        busy_rows=busy_rows,
    )
    return json.dumps(payload, ensure_ascii=False)


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

    slots = [slot.model_dump() if hasattr(slot, "model_dump") else slot for slot in candidate_slots or []]
    selected = selected_slot.model_dump() if hasattr(selected_slot, "model_dump") else selected_slot
    payload = {
        "title": title,
        "members": normalize_external_member_names(member_names),
        "selected_slot": selected,
        "status": "confirmed" if selected else "needs_manual_review",
        "reason": reason,
        "candidate_slots": slots,
    }
    return json.dumps({"ok": True, "tool_name": "propose_group_schedule", "final_decision": payload}, ensure_ascii=False)


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

    result = _NANA_SUBAGENT.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)

    return json.dumps(
        {
            "ok": True,
            "tool_name": "nana_agent",
            "selected_agent": "nana_agent",
            "answer": extract_final_text(result),
            "trace": events,
            "inner_tool_names": _tool_call_names(events),
        },
        ensure_ascii=False,
    )


@tool(args_schema=AgentQueryInput)
def kana_agent(query: str) -> str:
    """그룹 일정 종합 작업을 프롬프트 기반 Kana 하위 에이전트에게 위임합니다."""

    global _KANA_SUBAGENT
    if _KANA_SUBAGENT is None:
        _KANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=kana_tools(),
            system_prompt=kana_system_prompt(),
        )

    result = _KANA_SUBAGENT.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)

    # 하위 trace에서 최종 시간 결정 payload를 끌어올려 supervisor가 답변에 쓸 수 있게 한다.
    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None

    for event in events:
        content = event.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                continue
        if not isinstance(content, dict):
            continue
        if "final_slot" in content:
            final_slot_payload = content
        if content.get("final_decision"):
            final_decision_payload = content["final_decision"]

    return json.dumps(
        {
            "ok": True,
            "tool_name": "kana_agent",
            "selected_agent": "kana_agent",
            "answer": extract_final_text(result),
            "trace": events,
            "inner_tool_names": _tool_call_names(events),
            "final_slot_payload": final_slot_payload,
            "final_decision_payload": final_decision_payload,
        },
        ensure_ascii=False,
    )


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