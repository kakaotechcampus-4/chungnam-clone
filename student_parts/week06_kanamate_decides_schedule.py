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


# ... (가이드 주석 블록은 원본 그대로 유지) ...


def week06_system_prompt() -> str:
    """6주차 supervisor agent가 따르는 시스템 프롬프트입니다."""

    return supervisor_system_prompt()


def week06_prompt_parts() -> list[str]:
    """1~6주차 supervisor system prompt 조각을 누적합니다."""

    return [
        *week05_prompt_parts(),
        (
            "Week 6부터 당신(supervisor)은 요청을 직접 처리하지 않습니다. 반드시 nana_agent 또는 "
            "kana_agent 중 하나를 호출해서 위임하고, 그 결과만 근거로 답합니다. 당신이 직접 "
            "personal_list_saved_schedules 같은 하위 tool을 호출하는 일은 없습니다.\n"
            "판단 기준: 질문에 다른 사람 이름이 없고 '내 일정', '내가 저장한 할 일', '내 참고자료', "
            "'예전에 나눈 대화' 처럼 본인 이야기면 nana_agent에 위임합니다. 질문에 다른 사람 이름이 "
            "등장하거나(그 사람의 일정/바쁜 시간/이전 대화), 여러 사람이 만날 시간을 정해야 하면 "
            "kana_agent에 위임합니다. '철수랑 잡은 회의를 이미 저장해뒀는지'처럼 다른 사람 이름이 "
            "있어도 내 앱 DB에 답이 있을 수 있는 질문은, 먼저 nana_agent에게 물어보고 결과가 없거나 "
            "부족하면 kana_agent에도 위임해 봅니다.\n"
            "한 하위 agent의 답이 비어 있거나 요청을 처리 못 했다고 답하면, 바로 포기하지 말고 "
            "다른 하위 agent에도 위임해서 확인합니다."
        ),
    ]


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        *week04_prompt_parts(),
        (
            "Week 6부터 당신(Nana)은 supervisor로부터 위임받은 개인 업무만 처리하는 하위 "
            "에이전트입니다. supervisor의 지시나 대화 맥락은 공유받지 않으므로, 전달받은 query만 "
            "보고 판단합니다.\n"
            "당신의 담당 범위: 개인 일정 생성/조회/수정/삭제, todo/reminder 저장 및 조회, 개인 "
            "참고자료 검색, 이전 앱 대화 발화 검색(RAG)입니다. 이 범위 안의 요청은 지금까지 배운 "
            "Week 1~4 도구로 직접 처리하고 답합니다.\n"
            "다른 사람의 일정을 조회하거나, 여러 사람이 만날 시간을 정하는 등 그룹 조율이 필요한 "
            "요청을 받으면, 당신이 처리할 수 없다고 짧게 답합니다. 이런 요청은 당신의 도구로 답을 "
            "찾을 수 없으니 추측하지 않습니다."
        ),
    ]


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    today_iso = current_app_date_iso()
    return [
        (
            f"당신은 Kana라는 이름의 외부 멤버 일정/그룹 조율 담당 하위 에이전트입니다. 오늘 날짜는 "
            f"{today_iso}입니다. supervisor로부터 위임받은 query만 보고 판단하며, 이전 대화 맥락은 "
            "공유받지 않습니다.\n"
            "당신의 담당 범위: 외부 멤버(나 자신이 아닌 다른 사람)의 이전 대화 검색·조회, 그 사람의 "
            "일정/바쁜 시간 조회, 공유 일정 저장소 row 조회, 그리고 여러 사람이 함께 만날 수 있는 "
            "공통 가능 시간의 후보 검증과 최종 시간 결정입니다.\n"
            f"자연어 요청을 구조화해야 하면 {extract_schedule_request.name}을 사용합니다. 그 사람의 "
            f"'무슨 얘기를 했는지'가 궁금하면 {search_previous_conversations.name}로 이전 대화를 "
            f"검색한 뒤 필요하면 {load_conversation_messages.name}로 전체 메시지를 읽습니다. 그 "
            f"사람의 '일정'이나 '바쁜 시간' 자체가 궁금하면 대화 검색 없이 바로 "
            f"{extract_schedules_from_history.name}을 호출합니다. 공유 일정 저장소에 등록된 row "
            f"자체를 확인하려면 {list_shared_schedules.name}을 사용합니다.\n"
            f"내 일정과 다른 사람들의 busy-time을 한 번에 모으려면 {collect_member_schedules.name}을 "
            "사용합니다. 여러 사람이 만날 수 있는 시간을 찾아야 하면, 먼저 "
            f"{collect_member_schedules.name}으로 busy_rows를 모으고, 그 busy_rows를 직접 검토해서 "
            f"업무 시간(기본 09:00~18:00) 안에서 겹치지 않는 후보들을 스스로 골라 "
            f"{find_common_available_slots.name}의 candidate_slots 인자로 넘깁니다. 이 tool은 후보를 "
            "대신 계산해주지 않으므로, 당신이 먼저 후보를 판단해야 합니다. 검증이 끝나면 반드시 이어서 "
            f"{decide_final_slot.name}을 호출해 최종 시간을 확정하거나(사용자에게 후보를 제시하고 "
            "확인이 필요하면) needs_agent_selection=true 상태로 남깁니다. 이 tool도 최종 시간을 "
            "자동으로 고르지 않으므로, 당신이 후보 중 하나를 선택하거나 아직 선택할 수 없다는 것을 "
            "직접 판단해서 인자로 넘겨야 합니다.\n"
            "겹치는 일정이 없다고 해서 그 시간에 실제로 모두 만날 수 있다고 섣불리 단정하지 않습니다. "
            "확정된 그룹 일정을 SQLite에 저장하는 것은 당신의 역할이 아니라 Nana가 담당하므로, 저장이 "
            "필요하면 그렇게 안내합니다."
        ),
    ]


def nana_system_prompt() -> str:
    return join_system_prompt(nana_prompt_parts())


def kana_system_prompt() -> str:
    return join_system_prompt(kana_prompt_parts())


def supervisor_system_prompt() -> str:
    return join_system_prompt(
        [
            *week06_prompt_parts(),
            (
                "반드시 nana_agent 또는 kana_agent 중 하나를 호출한 뒤, 그 tool 결과(answer)만 근거로 "
                "사용자에게 답합니다. tool을 호출하지 않고 직접 답하거나, tool 결과와 다른 내용을 "
                "지어내지 않습니다."
            ),
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
    "수집된 busy_rows를 근거로, 당신(Kana)이 직접 고른 공통 가능 후보 시간(candidate_slots)이 "
    "실제로 아무와도 겹치지 않는지 검증하고 기록하는 tool입니다. 이 tool은 후보를 스스로 계산하지 "
    "않습니다 — 반드시 collect_member_schedules 등으로 얻은 busy_rows를 이 tool의 busy_rows 인자에 "
    "그대로 복사해서 넘기고, 그 busy_rows를 당신이 직접 검토해서 업무 시간(workday_start~workday_end) "
    "안에서 겹치지 않는 시간대를 candidate_slots로 골라 넘겨야 합니다. candidate_slots의 각 항목은 "
    "date(YYYY-MM-DD), start_time(HH:MM), end_time(HH:MM), duration_minutes, reason(왜 이 시간을 "
    "골랐는지)을 포함해야 합니다. 이 tool의 결과만으로 답변을 끝내지 말고, 검증된 후보 중 하나를 "
    "최종 확정하거나 사용자 확인이 필요하다는 것을 기록하기 위해 반드시 decide_final_slot을 이어서 "
    "호출하세요."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    "find_common_available_slots로 검증된 후보 중에서, 당신(Kana)이 직접 고른 최종 시간을 "
    "기록하는 tool입니다. 이 tool은 최종 시간을 자동으로 선택하지 않습니다 — selected_index(고른 "
    "candidate_slots의 index) 또는 selected_slot과, final_slot('YYYY-MM-DD HH:MM-HH:MM' 형식의 "
    "확정 시간 텍스트)을 당신이 직접 판단해서 채워야 합니다. 아직 사용자 확인이나 추가 정보가 "
    "필요해서 최종 시간을 못 정했다면, final_slot은 비워두고(null) needs_agent_selection=true로 "
    "남기세요. 근거를 남기기 위해 candidate_slots, busy_rows, member_names, date_from/date_to도 "
    "함께 넘기고, reason에는 이 시간을 선택했거나 아직 못 정한 이유를 사용자가 이해할 수 있게 "
    "적으세요."
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
    """멤버별 busy-time rows와 LLM이 고른 후보 payload를 검증 결과로 바꿉니다.

    NOTE: find_common_available_slots_payload의 실제 키워드 인자 이름은
    fixed/schedule_decision.py에서 한 번 확인해 주세요. 아래는 FindCommonAvailableSlotsInput
    필드 이름을 그대로 전달한다는 가정하에 작성했습니다.
    """

    normalized_members = normalize_external_member_names(member_names)
    normalized_date_from = normalize_date_bound(date_from)
    normalized_date_to = normalize_date_bound(date_to)

    if busy_rows is None:
        # 내 일정도 근거가 되어야 하므로 "나"를 포함해서 수집합니다.
        collect_result = json.loads(
            collect_member_schedules.invoke(
                {
                    "member_names": normalized_members,
                    "date_from": normalized_date_from,
                    "date_to": normalized_date_to,
                }
            )
        )
        busy_rows = collect_result.get("rows", [])

    return find_common_available_slots_payload(
        member_names=normalized_members,
        date_from=normalized_date_from,
        date_to=normalized_date_to,
        duration_minutes=duration_minutes,
        workday_start=workday_start,
        workday_end=workday_end,
        limit=limit,
        busy_rows=busy_rows,
        candidate_slots=candidate_slots or [],
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

    result = find_common_available_slots_dict(
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
    return json.dumps(result, ensure_ascii=False)


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
    """LLM이 직접 고른 후보/최종 시간을 course repo payload로 기록합니다.

    NOTE: decide_final_slot_payload의 실제 키워드 인자 이름도 fixed/schedule_decision.py에서
    확인해 주세요. 아래는 DecideFinalSlotInput 필드 이름을 그대로 전달한다는 가정입니다.
    """

    result = decide_final_slot_payload(
        candidate_slots=candidate_slots or [],
        selected_slot=selected_slot,
        selected_index=selected_index,
        final_slot=final_slot,
        needs_agent_selection=needs_agent_selection,
        member_names=member_names,
        date_from=normalize_date_bound(date_from) if date_from else date_from,
        date_to=normalize_date_bound(date_to) if date_to else date_to,
        duration_minutes=duration_minutes,
        reason=reason,
        busy_rows=busy_rows,
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
    trace = extract_agent_events(result)
    answer = extract_final_text(result)
    inner_tool_names = _tool_call_names(trace)

    return json.dumps(
        {
            "selected_agent": "nana_agent",
            "answer": answer,
            "trace": trace,
            "inner_tool_names": inner_tool_names,
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
    trace = extract_agent_events(result)
    answer = extract_final_text(result)
    inner_tool_names = _tool_call_names(trace)

    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None
    for event in trace:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        if event.get("tool_name") == "decide_final_slot" or "final_slot" in content:
            final_slot_payload = content
        if content.get("final_decision"):
            final_decision_payload = content["final_decision"]

    return json.dumps(
        {
            "selected_agent": "kana_agent",
            "answer": answer,
            "trace": trace,
            "inner_tool_names": inner_tool_names,
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