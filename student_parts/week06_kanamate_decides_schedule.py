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
    PERSONAL_SHARED_MEMBER_NAME,  # TODO: 실제 정의 위치 확인 후 import 경로 조정
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


# [6주차 수강생 구현 가이드]
#
# 목표
#   Week 6은 "모든 기능을 한 agent가 직접 처리"하지 않고 supervisor가 Nana/Kana 하위 agent로 위임하게 만듭니다.
#   Nana는 개인 일정/저장/RAG를 맡고, Kana는 외부 대화/멤버 일정/그룹 시간 결정을 맡습니다.
#   supervisor가 직접 볼 수 있는 tool은 nana_agent와 kana_agent 두 개뿐입니다.
#
# 과제 구성
#   - 메인과제: 한 agent가 모두 처리하던 구조를 supervisor + Nana/Kana 하위 agent로 나누어
#     supervisor가 요청을 알맞은 하위 agent에 위임하는 뼈대를 완성합니다.
#     세 agent의 system prompt를 직접 작성하는 것과 위임 wrapper tool 두 개 구현이 여기 들어갑니다.
#   - 추가 과제: Kana의 공통 가능 시간 후보 검증(find_common_available_slots)과
#     최종 시간 결정(decide_final_slot)까지 붙여 그룹 일정 조율을 마무리합니다.
#
# 구현 위치와 사용할 코드
#   - 이 파일(student_parts/week06_kanamate_decides_schedule.py)의 Week 6 전용 tool과 sub-agent wrapper를 구현합니다.
#   - 공통 가능 시간 검증/최종 선택 payload 생성은 fixed/schedule_decision.py의
#     find_common_available_slots_payload(), decide_final_slot_payload(), normalize_date_bound()를 사용합니다.
#   - Nana 하위 agent 도구는 student_parts/week04_retrieve_nanas_memory.py의 week04_tools()를 그대로 사용합니다.
#   - Kana 하위 agent 도구는 이 파일의 kana_tools()에서 구성하며, Week 2 extract_schedule_request와
#     Week 5 wrapper tool(search_previous_conversations, extract_schedules_from_history,
#     collect_member_schedules 등), find_common_available_slots, decide_final_slot을 포함합니다.
#   - supervisor가 볼 수 있는 도구는 supervisor_tools()의 nana_agent, kana_agent 두 개뿐입니다.
#   - nana_agent()/kana_agent()/build_langchain_supervisor_agent()는 create_agent(...)로 각각 필요한 agent를 만들고 재사용합니다.
#   - trace 정리는 fixed/langchain_trace.py의 extract_agent_events(), extract_final_text()를 사용합니다.
#
# 메인과제 구현 대상
#   1. week06_prompt_parts / nana_prompt_parts / kana_prompt_parts / supervisor_system_prompt
#      - supervisor와 Nana/Kana 하위 에이전트의 역할 분담을 prompt로 직접 정의합니다.
#      - supervisor는 직접 업무를 처리하지 않고 nana_agent 또는 kana_agent로만 위임하게 씁니다.
#      - Nana는 개인 일정/저장/RAG, Kana는 외부 멤버 일정/공통 시간 결정을 담당하게 씁니다.
#      - week06_prompt_parts는 week05_prompt_parts()를, nana_prompt_parts는 week04_prompt_parts()를 누적합니다.
#        kana_prompt_parts만 누적 없이 시작하므로 Kana 역할을 처음부터 작성해야 합니다.
#      - 하위 에이전트는 supervisor prompt를 공유하지 않으므로 각자 필요한 지시를 스스로 갖고 있어야 합니다.
#
#   2. nana_agent
#      - supervisor가 넘긴 query로 Nana 하위 agent를 이 tool 안에서 만들거나 재사용해 실행합니다.
#      - 개인 일정 조회/생성/수정/삭제 판단은 하위 agent가 prompt와 tool description을 근거로 수행합니다.
#      - 하위 agent 결과에서 answer, trace, inner_tool_names를 뽑아 JSON 문자열로 반환합니다.
#      - 개인 일정 생성/조회/수정/삭제, todo/reminder 저장, 개인 참고자료와 앱 대화 RAG는 Nana 담당입니다.
#
#   3. kana_agent
#      - supervisor가 넘긴 query로 Kana 하위 agent를 이 tool 안에서 만들거나 재사용해 실행합니다.
#      - 하위 trace를 훑어 decide_final_slot 결과를 final_slot_payload로 끌어올립니다.
#      - answer, trace, inner_tool_names, final_slot_payload, final_decision_payload를 JSON으로 반환합니다.
#      - 외부 멤버 일정 조회, 공유 일정 row 조회, 공통 가능 시간 후보 검증과 최종 시간 결정은 Kana 담당입니다.
#
# 추가 과제 구현 대상 (구현하지 않으려면 kana_tools() 목록에서 해당 tool을 제거)
#   1. FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION / DECIDE_FINAL_SLOT_DESCRIPTION
#      - Kana agent가 두 tool을 언제 어떤 argument로 호출할지 판단하는 유일한 근거가 tool description입니다.
#      - Python tool이 자동으로 최적 시간을 고르는 것이 아니라, agent가 busy_rows를 근거로 후보와 최종 시간을
#        직접 골라 argument로 넘기게 만들어야 합니다. 이 점이 description에 없으면 agent가 tool에 계산을 떠넘깁니다.
#      - candidate_slots 항목 형식(date, start_time, end_time, duration_minutes, reason)과
#        final_slot 형식('YYYY-MM-DD HH:MM-HH:MM')을 명시해 argument 형태를 고정합니다.
#
#   2. find_common_available_slots_dict / find_common_available_slots / decide_final_slot
#      - find_common_available_slots는 busy-time row를 Python 룰이나 nested LLM으로 훑지 않고,
#        Kana agent가 tool description을 읽고 직접 고른 candidate_slots payload를 검증/기록합니다.
#      - date_from/date_to에 ISO datetime이 들어오면 normalize_date_bound()로 날짜 부분만 사용합니다.
#      - busy_rows가 None이면 collect_member_schedules를 호출해 내 일정과 외부 멤버 busy-time을 모읍니다.
#      - decide_final_slot도 nested LLM을 만들지 않고 Kana agent가 넘긴 final_slot, selected_index,
#        needs_agent_selection, reason payload를 그대로 course repo JSON 계약에 맞춰 기록합니다.
#      - 반환 JSON은 course repo 기준 top-level final_slot, reason, candidates를 반드시 포함합니다.
#      - 후보 판단을 수행한 경우 members, busy_rows, candidate_slots도 함께 남겨 근거를 확인할 수 있게 합니다.
#      - selected_index나 selected_slot이 없으면 final_slot을 자동으로 고르지 말고 needs_agent_selection=True 상태를 유지합니다.
#
# 중요한 구조
#   Week 6 파일은 Week 1-5 구현을 다시 작성하지 않습니다.
#   이전 주차 tool을 import하고 kana_tools(), supervisor_tools()에서 역할별로 조립합니다.
#   prompt 함수는 메인과제 구현 대상입니다. supervisor와 Nana/Kana는 서로 다른 system prompt로 동작하므로,
#   위임 규칙과 역할 분담을 어떻게 쓰느냐가 Week 6 동작을 그대로 좌우합니다.
#   두 tool description 상수도 추가 과제 구현 대상입니다. Python 구현과 description이 서로 다른 계약을 말하면
#   agent가 잘못된 argument를 넘기므로, 두 tool을 구현할 때 description도 같은 계약으로 함께 씁니다.
#   각 tool이 받는 argument 이름과 형식은 FindCommonAvailableSlotsInput / DecideFinalSlotInput에 이미 정의되어 있으니
#   description은 그 스키마를 말로 풀어 agent가 언제 무엇을 채울지 판단하게 만드는 역할입니다.
#   find_common_available_slots/decide_final_slot의 실제 겹침 검증과 payload 정리는 fixed/schedule_decision.py가 맡습니다.
#
# Compatibility helper
#   propose_group_schedule은 기존 흐름을 위해 구현된 상태로 유지하며 kana_tools()에는 들어가지 않습니다.
#   현재 supervisor/kana_tools() 경로의 구현 대상은 prompt 함수 4개와 nana_agent, kana_agent(메인),
#   tool description 상수 2개와 find_common_available_slots_dict, find_common_available_slots,
#   decide_final_slot(추가)입니다.
#
# 검증 방법
#   - 메인과제: ./run.sh --week6을 실행하고, supervisor trace에서 nana_agent 또는 kana_agent 중
#     무엇이 선택됐는지, 개인 일정 조회에서 Nana 하위 agent trace에 personal_list_saved_schedules
#     호출이 남는지 확인합니다. 위임이 엉뚱한 agent로 가면 tool 구현이 아니라 prompt의 판단 기준을 먼저 고칩니다.
#     추가 과제를 아직 구현하지 않았다면 kana_tools()에서 find_common_available_slots와
#     decide_final_slot을 빼고 Kana prompt에서도 두 tool 언급을 지운 뒤 위임 흐름만 확인합니다.
#   - 추가 과제: 그룹 일정 요청에서 하위 trace에 search_previous_conversations,
#     extract_schedules_from_history 또는 collect_member_schedules, find_common_available_slots,
#     decide_final_slot이 이어지고 final_slot_payload가 최종 답변과 일치하는지 확인합니다.
#
# 함수별 동작 설명 ([메인]/[추가]/[공통]은 각 함수가 속한 과제 티어입니다)
#   - [메인] week06_system_prompt() / week06_prompt_parts()
#     supervisor agent의 system prompt를 만듭니다. supervisor는 직접 업무를 처리하지 않고 nana_agent 또는 kana_agent로 위임합니다.
#
#   - [메인] nana_prompt_parts() / kana_prompt_parts()
#     하위 에이전트별 역할 prompt를 만듭니다. Nana는 개인 일정/저장/RAG, Kana는 외부 멤버 일정/공통 시간 결정을 담당합니다.
#
#   - [메인] nana_system_prompt() / kana_system_prompt() / supervisor_system_prompt()
#     prompt 조각을 join_system_prompt(...)로 합쳐 실제 create_agent(...)에 넘길 system prompt 문자열을 만듭니다.
#     supervisor_system_prompt()는 누적 조각 뒤에 supervisor 실행 역할 지시를 덧붙이는 자리입니다.
#
#   - [공통] _tool_call_names(events)
#     trace event 목록에서 tool_call 이벤트의 tool_name만 뽑아 UI와 테스트가 호출 순서를 쉽게 확인하게 합니다.
#
#   - [공통] extract_langchain_trace(result)
#     supervisor 실행 결과를 events, 선택된 하위 agent, 내부 tool 이름, 최종 시간 payload가 포함된 trace dict로 정리합니다.
#
#   - [공통] tool_name(tool_object)
#     LangChain tool 객체와 일반 함수 객체에서 이름을 안전하게 읽습니다. agent_tool_names(...)에서 사용합니다.
#
#   - [추가] FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION / DECIDE_FINAL_SLOT_DESCRIPTION
#     Kana agent가 두 tool을 언제 어떤 argument로 호출할지 판단하는 근거가 되는 tool description입니다.
#     tool이 후보나 최종 시간을 대신 계산해주지 않는다는 점을 agent가 알 수 있게 써야 합니다.
#
#   - [추가] FindCommonAvailableSlotsInput / DecideFinalSlotInput
#     Kana agent가 공통 가능 시간 후보와 최종 선택을 tool argument로 넘길 때 쓰는 Pydantic 입력 스키마입니다.
#
#   - [공통] ProposeGroupScheduleInput / AgentQueryInput
#     호환용 그룹 일정 제안 tool(구현 완료)과 supervisor가 하위 agent에 query를 넘기는 wrapper tool(메인과제)의 입력 스키마입니다.
#
#   - [추가] find_common_available_slots_dict(...)
#     멤버 이름과 날짜 범위를 정규화하고, busy_rows가 없으면 collect_member_schedules를 호출해 수집합니다.
#     실제 후보 검증 payload 생성은 fixed/schedule_decision.py의 find_common_available_slots_payload(...)가 맡습니다.
#
#   - [추가] find_common_available_slots(...)
#     Kana agent가 직접 고른 candidate_slots가 busy_rows와 겹치지 않는지 검증하고 JSON 문자열로 반환하는 tool입니다.
#
#   - [추가] decide_final_slot(...)
#     Kana agent가 직접 고른 selected_index/final_slot/reason을 course repo 계약에 맞는 최종 payload로 기록합니다.
#
#   - [공통] kana_tools() / supervisor_tools() / agent_tool_names(agent_name)
#     Kana 하위 agent와 supervisor가 볼 수 있는 tool 목록을 역할별로 조립하고 이름 목록을 제공합니다.
#
#   - [공통] propose_group_schedule(...)
#     이전 실습 흐름과의 호환을 위해 남겨 둔 그룹 일정 최종 제안 helper입니다. 구현 완료 상태이고
#     kana_tools()에도 들어가지 않습니다. 현재 핵심 경로는 decide_final_slot입니다.
#
#   - [메인] nana_agent(query)
#     supervisor가 개인 업무를 위임할 때 호출하는 tool입니다. Week 4 tool을 가진 Nana 하위 agent를 실행합니다.
#
#   - [메인] kana_agent(query)
#     supervisor가 외부 멤버/그룹 조율 업무를 위임할 때 호출하는 tool입니다. Kana 하위 agent trace에서
#     final_slot_payload와 final_decision_payload를 끌어올려 supervisor가 최종 답변에 사용할 수 있게 합니다.
#
#   - [공통] build_langchain_supervisor_agent() / build_week_agent()
#     supervisor agent를 한 번만 만들고 재사용합니다. build_week_agent()는 실행기가 호출하는 표준 entry point입니다.


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
            "한 하위 agent가 자신의 담당 범위가 아니라고 명시적으로 거절하면(예: '제 담당이 아닙니다', "
            "'그룹 조율이 필요한 요청이라 처리할 수 없습니다'), 다른 하위 agent에도 위임해서 확인합니다. "
            "정상적으로 조회했지만 결과가 비어있다고 답한 경우(예: '해당 조건의 일정을 찾지 못했습니다')는 "
            "거절이 아니므로 다른 agent에 다시 위임하지 않습니다."
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
            "요청을 받으면, 항상 같은 형식으로 짧게 답합니다: '이 요청은 제 담당이 아닙니다.' 이 "
            "표현은 supervisor가 위임 실패를 판정하는 기준이 되므로 임의로 다른 문구로 바꾸지 "
            "않습니다. 이런 요청은 당신의 도구로 답을 찾을 수 없으니 추측하지 않습니다."
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
            "대신 계산해주지 않으므로, 당신이 먼저 후보를 판단해야 합니다. 넘긴 candidate_slots 중 "
            "일부가 결과에서 조용히 제외될 수 있습니다(범위 밖, 업무 시간 밖, 최소 길이 미달, busy_rows와 "
            "겹침 등 조건을 벗어나면 예외 없이 제외됩니다) — 결과 개수가 줄어든 것을 '가능한 시간이 "
            "없다'로 단정하지 말고, 필요하면 다른 후보를 다시 골라 재시도합니다. 검증이 끝나면 반드시 "
            f"이어서 {decide_final_slot.name}을 호출해 최종 시간을 확정하거나(사용자에게 후보를 제시하고 "
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
    "골랐는지)을 포함해야 합니다. 조건(범위 밖, 업무 시간 밖, duration 미달, busy_rows와 겹침)을 벗어난 "
    "후보는 결과에서 예외 없이 조용히 제외되므로, 넘긴 개수보다 결과가 적게 나올 수 있습니다. 이 tool의 "
    "결과만으로 답변을 끝내지 말고, 검증된 후보 중 하나를 최종 확정하거나 사용자 확인이 필요하다는 것을 "
    "기록하기 위해 반드시 decide_final_slot을 이어서 호출하세요."
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

    busy_rows가 주어지지 않으면 collect_member_schedules로 새로 수집합니다. 이때 나 자신은
    member_names에 넣지 않아도 collect_member_schedules가 _personal_schedules_for_current_scope()로
    내 개인 일정을 자동으로 rows에 합쳐줍니다. 다만 find_common_available_slots_payload에 넘기는
    member_names(근거로 기록되는 필드)에는 "나"를 명시적으로 포함시켜서, 이 검증이 실제로 어떤 사람들의
    일정을 근거로 삼았는지가 payload/trace에 정확히 남도록 합니다.
    """

    normalized_members = normalize_external_member_names(member_names)
    normalized_date_from = normalize_date_bound(date_from)
    normalized_date_to = normalize_date_bound(date_to)

    if busy_rows is None:
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

    if PERSONAL_SHARED_MEMBER_NAME in normalized_members:
        payload_member_names = normalized_members
    else:
        payload_member_names = [PERSONAL_SHARED_MEMBER_NAME, *normalized_members]

    return find_common_available_slots_payload(
        member_names=payload_member_names,
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

    # busy_rows는 LLM이 채우는 인자라 "아직 안 모았다"(None)와 "모았는데 없다"([])를 항상
    # 정확히 구분해서 넘겨준다는 보장이 없습니다. LLM이 실수로 []를 넘기면 is None 검사로는
    # 수집을 건너뛰고 빈 캘린더 위에서 검증을 통과시켜버리는 위험이 있어, 여기서는 not busy_rows로
    # 넓게 잡아 "비어있어도 다시 수집"하는 쪽을 선택했습니다. (정말 아무도 안 바쁜 정상 케이스에서
    # collect_member_schedules를 한 번 더 호출하는 비용을, 잘못 확정된 회의 시간의 비용보다
    # 낮다고 판단했습니다.)
    if not busy_rows:
        busy_rows = None

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
    """LLM이 직접 고른 후보/최종 시간을 course repo payload로 기록합니다."""

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