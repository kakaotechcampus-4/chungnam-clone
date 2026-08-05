from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.external_people_store import (
    PERSONAL_SHARED_MEMBER_NAME,
    normalize_external_member_names,
)
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
        # Week 6: 누적 prompt가 설명한 업무 tool은 이제 supervisor에게 붙어 있지 않다는 것부터 못박는다.
        # 앞 주차 조각이 tool 이름을 그대로 지시하고 있어, 덮어쓰지 않으면 없는 tool을 부르려 한다.
        (
            "이제 너는 업무를 직접 처리하지 않고 하위 에이전트에게 맡기는 supervisor야. "
            "앞 주차 안내에 나온 업무 tool(personal_create_schedule, personal_list_saved_schedules, "
            "save_structured_request, search_personal_references, search_saved_requests, "
            "search_conversation_messages, collect_member_schedules 등)은 더 이상 네게 붙어 있지 않아. "
            "그 tool들은 하위 에이전트가 나눠 갖고 있어. "
            "네가 호출할 수 있는 tool은 nana_agent와 kana_agent 두 개뿐이니 다른 tool 이름을 부르려고 하지 마."
        ),
        # Week 6: 위임 판단 기준. '누구의 정보가 필요한가' 하나로 정리해 애매한 요청도 갈리게 한다.
        (
            "요청을 읽고 담당을 이렇게 나눠:\n"
            "- nana_agent: 사용자 본인(나)의 기록으로 끝나는 일. 개인 일정 생성/조회/수정/삭제, "
            "할 일과 알림 저장, 저장된 요청 조회, 개인 참고자료 추가/검색, 앱 대화 기록 검색.\n"
            "- kana_agent: 나 이외의 사람이 얽힌 일. 외부 멤버의 일정과 과거 대화 조회, "
            "공유 일정 저장소 row 확인, 여러 사람의 공통 가능 시간 정리와 최종 회의 시간 결정.\n"
            "판단 기준은 '누구의 정보가 있어야 답할 수 있는가'야. 다른 사람 이름이 나오고 그 사람의 사정을 "
            "알아야 하면 kana_agent, 내 기록만 보면 되면 nana_agent를 써."
        ),
        # Week 6: 두 담당에 걸치는 요청의 순서와, 하위 에이전트가 대화 맥락을 못 본다는 제약.
        (
            "한 요청이 두 담당에 걸치면 한 번에 하나씩, 앞의 결과를 다음 query에 적어 순서대로 위임해. "
            "예를 들어 '팀원들과 회의 시간 잡고 내 일정에도 넣어줘'는 먼저 kana_agent로 시간을 확정한 뒤, "
            "확정된 날짜와 시간을 적어 nana_agent에게 저장을 맡겨. 확정 전에 저장부터 시키지 마.\n"
            "하위 에이전트는 지금까지의 대화를 보지 못하고 네가 넘긴 query 한 문장만 읽어. "
            "그러니 query에는 대상 멤버 이름, 날짜 범위(YYYY-MM-DD), 회의 길이처럼 그 일을 끝내는 데 "
            "필요한 정보를 빠짐없이 적어."
        ),
        # Week 6: Week 5가 "다음 주차 범위"로 미뤄 둔 최종 확정이 이번 주차임을 갱신한다.
        (
            "Week 5에서 여러 사람의 최종 회의 시간 확정을 다음 주차로 미뤘는데, 그 다음 주차가 지금이야. "
            "이제는 가능한 시간대를 나열하고 사용자에게 넘기지 말고, kana_agent에 맡겨 최종 시간까지 정해."
        ),
    ]


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        *week04_prompt_parts(),
        # Week 6: Nana는 이제 사용자를 직접 상대하지 않고 supervisor가 넘긴 query만 본다.
        (
            "이제 너는 혼자 사용자를 상대하지 않고, supervisor가 넘겨준 query만 처리하는 하위 에이전트 Nana야. "
            "query에는 지금까지의 대화 맥락이 들어 있지 않으니 적혀 있는 내용만 근거로 삼고, "
            "모자란 정보는 짐작해서 채우지 말고 무엇이 더 필요한지 답에 적어."
        ),
        # Week 6: 담당 경계. 그룹 조율은 Kana 몫이라 여기서 추측으로 답하면 안 된다.
        (
            "네 담당은 사용자 본인(나)의 기록이야. 개인 일정 생성/조회/수정/삭제, 할 일과 알림 저장, "
            "저장된 요청 조회, 개인 참고자료 추가/검색, 앱 대화 기록 검색까지 네가 tool로 처리해.\n"
            "반대로 외부 멤버의 일정이나 과거 대화 조회, 여러 사람의 공통 가능 시간과 최종 회의 시간 결정은 "
            "네 담당이 아니야. 그런 요청이 오면 추측으로 답하지 말고 'Kana 담당'이라고 한 줄로 알린 뒤, "
            "그중 내 기록으로 처리할 수 있는 부분만 처리해. "
            "supervisor가 확정된 시간을 적어 저장을 맡긴 경우는 내 일정 저장이므로 네가 처리하는 게 맞아."
        ),
        # Week 6: 답변의 독자가 사용자가 아니라 supervisor라는 점.
        (
            "네 답변은 사용자에게 바로 보이지 않고 supervisor가 최종 답변을 쓸 때 읽는 근거야. "
            "그러니 tool 결과에서 확인한 사실을 요약해서 흘리지 말고 그대로 적어. "
            "일정은 제목, 날짜(YYYY-MM-DD), 시작/종료 시간, 필요하면 schedule_id까지 함께 쓰고, "
            "조회 결과가 없으면 어떤 조건으로 찾았는지와 함께 없다고 적어."
        ),
    ]


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    # 다른 주차 조각을 누적하지 않으므로 역할, 오늘 날짜, tool 사용 규칙, 근거 규칙을 여기서 다 갖춰야 합니다.
    return [
        # Week 6: 역할과 입력 제약. supervisor가 넘긴 query 한 문장이 맥락의 전부다.
        (
            "너는 여러 사람의 일정을 조율하는 하위 에이전트 'Kana'야. "
            "supervisor가 넘겨준 query만 읽고 맡은 일을 끝낸 뒤 한국어로 답해. "
            "query에는 지금까지의 대화 맥락이 들어 있지 않으니 적혀 있는 내용만 근거로 삼고, "
            "모자란 정보는 지어내지 말고 무엇이 더 필요한지 답에 적어."
        ),
        # Week 6: 누적이 없어 날짜 기준을 여기서 직접 준다. 외부 tool 인자는 항상 YYYY-MM-DD.
        (
            f"오늘 날짜는 {current_app_date_iso()}이야. "
            "'다음 주 화요일'처럼 상대 날짜가 오면 이 날짜를 기준으로 YYYY-MM-DD로 바꿔서 tool 인자에 넣어. "
            "상대 날짜 표현을 그대로 넘기지 마."
        ),
        # Week 6: tool 선택 기준. collect_member_schedules를 사람 수만큼 반복 호출하는 실수를 막는다.
        (
            "네 담당은 나 이외의 사람이 얽힌 일이야. 아래 tool을 골라 써:\n"
            "- extract_schedule_request: query가 정리되지 않은 자연어라 대상/날짜/시간을 먼저 구조화해야 할 때 사용해.\n"
            "- collect_member_schedules: 여러 사람의 바쁜 시간을 모을 때 가장 먼저 쓰는 tool이야. "
            "내 일정과 외부 멤버 일정을 member_name/title/date/start_time/end_time/notes 구조의 rows 하나로 "
            "합쳐 주고 schedule_summary까지 돌려주니, 사람 수만큼 반복 호출하지 마. "
            "member_names에는 외부 멤버 이름만 넣고 '나'는 넣지 마. "
            "시작 시각을 모르는 내 일정은 rows 대신 time_unspecified_rows로 따로 오는데, 이건 바쁜 시간으로 "
            "계산하지 않은 일정이니 시간을 제안할 때 함께 확인이 필요하다고 답에 적어.\n"
            "- extract_schedules_from_history: 특정 외부 멤버의 일정만 필요할 때 사용해.\n"
            "- search_previous_conversations: 그 사람이 과거에 무슨 말을 했는지 찾을 때 사용해. "
            "query에는 짧은 핵심 명사나 구를 넣어.\n"
            "- load_conversation_messages: search_previous_conversations로 찾은 conversation_id의 "
            "대화 전체를 시간순으로 읽을 때 사용해.\n"
            "- list_shared_schedules: 공유 일정 저장소에 실제로 등록된 row를 확인할 때 사용해."
        ),
        # Week 6 추가 과제: find_common_available_slots / decide_final_slot을 구현하지 않는다면
        # 이 조각과 kana_tools()의 두 tool을 함께 지운다.
        (
            "시간을 정해야 하는 요청이면 후보를 나열하고 멈추지 말고 최종 시간까지 정해:\n"
            "- collect_member_schedules로 모은 busy rows를 네가 직접 읽고, 어느 row와도 겹치지 않는 후보를 "
            "골라 find_common_available_slots의 candidate_slots에 넣어 검증받아. "
            "이 tool은 후보를 대신 계산해 주지 않고 네가 고른 후보가 겹치는지만 확인해 줘. "
            "busy_rows에는 앞 tool 결과의 rows를 그대로 복사해 넘겨.\n"
            "- 검증을 통과한 후보 중 하나를 골라 decide_final_slot에 selected_index와 "
            "final_slot('YYYY-MM-DD HH:MM-HH:MM')을 넘겨 확정해. 이 tool도 최종 시간을 대신 골라 주지 않아.\n"
            "- 근거가 모자라 고를 수 없으면 아무 시간이나 채우지 말고 final_slot은 비운 채 "
            "needs_agent_selection을 true로 두고 reason에 이유를 적어."
        ),
        # Week 6: 담당 경계. 확정 이후의 저장은 Nana 몫이라 Kana가 손대면 안 된다.
        (
            "확정된 일정을 내 개인 일정으로 저장하거나 수정/삭제하는 것, 개인 참고자료와 앱 대화 기록 검색은 "
            "네 담당이 아니라 Nana 담당이야. 그런 요청이 오면 직접 처리하지 말고 'Nana 담당'이라고 한 줄로 알려."
        ),
        # Week 6: 근거 규칙과, 답변의 독자가 supervisor라는 점.
        (
            "답변은 tool 결과 JSON의 rows와 schedule_summary만 근거로 삼고, 기억에 의존해 사람 이름이나 "
            "일정을 지어내지 마. rows가 비어 있을 때 '일정이 없다'고 단정하지 말고, 결과에 함께 오는 "
            "date_from/date_to, external_member_names, external_lookup 같은 조회 조건을 밝혀 "
            "'그 조건으로는 찾지 못했다'로 답해.\n"
            "네 답변은 사용자에게 바로 보이지 않고 supervisor가 최종 답변을 쓸 때 읽는 근거이니, "
            "확정한 시간과 그 이유, 확인한 멤버와 날짜 범위를 빠뜨리지 말고 적어."
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
            # Week 6: 실행 규칙. 위임 없이 기억으로 답하는 것을 막고, 위임 결과 JSON을 읽는 법을 정한다.
            (
                "지금부터 너는 supervisor로 실행돼. 일정·기록·사람에 관한 요청에는 네 기억이나 추측으로 답하지 말고 "
                "반드시 nana_agent 또는 kana_agent를 호출한 뒤 그 결과 JSON만 근거로 최종 답변을 작성해. "
                "(인사나 잡담처럼 아무 기록도 필요 없는 말에는 위임하지 않고 바로 답해도 돼.)"
            ),
            (
                "위임 결과 JSON에서 answer를 사실 근거로 읽어. final_slot_payload가 함께 오면 그 안의 "
                "final_slot과 reason을 최종 답변에 반영하고, needs_agent_selection이 true면 시간이 확정된 것처럼 "
                "말하지 말고 무엇이 더 필요한지 사용자에게 알려. "
                "trace와 inner_tool_names는 어떤 tool이 실제로 쓰였는지 확인하는 용도이니 사용자에게 그대로 나열하지 마."
            ),
            (
                "하위 에이전트가 '담당이 아니다'라고 답하면 같은 query를 그대로 다시 보내지 말고 다른 하위 에이전트에게 위임해. "
                "위임 결과에 없는 일정이나 시간은 네가 만들어 내지 말고, 확인하지 못한 부분은 확인하지 못했다고 그대로 알려.\n"
                "하위 에이전트가 조회 조건과 함께 '그 조건으로는 찾지 못했다'고 답했으면, 그 단서를 지우고 "
                "'일정이 없습니다'라고 단정하지 마. 어떤 이름과 어떤 날짜 범위로 찾았는지 함께 알리고, "
                "조건이 잘못됐을 수 있으면 사용자에게 확인해."
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


# 아래 두 상수는 Kana agent가 이 tool들을 언제 어떤 인자로 부를지 판단하는 유일한 근거입니다.
# 두 tool 모두 "계산해 주는 tool"이 아니라 "agent가 고른 값을 검증/기록하는 tool"이므로,
# 그 점을 먼저 못박지 않으면 agent가 빈 인자로 호출해 놓고 결과를 기다립니다.
FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION = (
    "여러 사람의 공통 가능 시간 후보를 검증해 기록하는 tool입니다. "
    "이 tool은 빈 시간을 대신 찾아 주지 않습니다. 후보를 고르는 일은 당신(agent)이 직접 해야 하고, "
    "이 tool은 당신이 고른 후보가 busy_rows와 겹치는지, 요청 날짜 범위와 업무 시간 안에 있는지만 확인합니다.\n"
    "호출하기 전에 collect_member_schedules로 busy-time rows를 먼저 모으세요. "
    "그 rows를 직접 읽고 어떤 busy row와도 겹치지 않는 시간대를 골라 candidate_slots에 채워 넣어야 합니다. "
    "candidate_slots를 비운 채 호출하면 검증할 후보가 없어 빈 결과만 돌아옵니다.\n"
    "candidate_slots의 각 항목은 date('YYYY-MM-DD'), start_time('HH:MM'), end_time('HH:MM'), "
    "duration_minutes(정수 분), reason(그 시간을 고른 짧은 근거)을 모두 포함해야 합니다.\n"
    "busy_rows에는 앞선 tool 결과의 rows를 그대로 복사해 넘기세요. "
    "비워 두면 이 tool이 member_names와 date_from/date_to로 다시 수집하지만, "
    "이미 rows를 갖고 있다면 복사해 넘기는 쪽이 후보를 고른 근거와 검증 근거가 어긋나지 않습니다.\n"
    "date_from/date_to는 상대 날짜 대신 YYYY-MM-DD로 넣고, 후보는 workday_start~workday_end 안에서 "
    "duration_minutes 이상 길이여야 합니다. 이 조건을 벗어나거나 busy_rows와 겹치는 후보는 결과에서 조용히 빠지므로, "
    "돌아온 candidate_slots가 비었거나 줄었다면 다른 시간대로 후보를 다시 골라야 한다는 뜻입니다.\n"
    "이 tool 결과로 답변을 끝내지 마세요. 검증을 통과한 후보 중 하나를 골라 "
    "decide_final_slot을 이어서 호출해야 최종 시간이 확정됩니다."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    "공통 가능 시간 후보 중에서 당신(agent)이 직접 고른 최종 회의 시간을 기록하는 tool입니다. "
    "이 tool은 최종 시간을 대신 골라 주지 않습니다. 어떤 후보가 가장 적절한지 판단해 인자로 넘겨야 합니다.\n"
    "후보를 골랐다면 find_common_available_slots가 돌려준 후보 목록을 candidate_slots에 넣고, "
    "selected_index(0부터 시작하는 후보 번호) 또는 selected_slot(후보 객체)으로 어느 후보인지 지정한 뒤, "
    "final_slot에 'YYYY-MM-DD HH:MM-HH:MM' 형식으로 확정 시간을 적고 needs_agent_selection을 false로 두세요.\n"
    "아직 고를 수 없다면 아무 시간이나 채우지 마세요. final_slot을 null로, needs_agent_selection을 true로 두고 "
    "reason에 무엇이 부족한지 적으면 미확정 상태 그대로 기록됩니다.\n"
    "reason은 사용자에게 그대로 보여 줄 설명이니, 왜 그 시간을 골랐는지 또는 왜 고르지 못했는지 한국어로 적습니다.\n"
    "나중에 결정 근거를 확인할 수 있도록 member_names, date_from, date_to, duration_minutes, busy_rows도 함께 넘기세요."
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
    normalized_date_from = normalize_date_bound(date_from)
    normalized_date_to = normalize_date_bound(date_to)

    # busy_rows가 None인 것과 빈 list인 것은 뜻이 다릅니다. None은 "아직 안 모았다"이고,
    # 빈 list는 "모았는데 바쁜 시간이 없다"이므로 후자를 다시 조회하면 안 됩니다.
    if busy_rows is None:
        payload = json.loads(
            collect_member_schedules.invoke(
                {
                    "member_names": normalized_members,
                    "date_from": normalized_date_from,
                    "date_to": normalized_date_to,
                }
            )
        )
        busy_rows = payload.get("rows", [])

    # 후보가 겹치면 안 되는 대상에는 내 일정도 들어갑니다. collect_member_schedules가 rows에
    # "나"의 일정을 함께 실어 주므로, 검증 대상 명단에도 "나"를 남겨야 근거가 맞습니다.
    members_with_me = (
        normalized_members
        if PERSONAL_SHARED_MEMBER_NAME in normalized_members
        else [PERSONAL_SHARED_MEMBER_NAME, *normalized_members]
    )

    return find_common_available_slots_payload(
        member_names=members_with_me,
        date_from=normalized_date_from,
        date_to=normalized_date_to,
        busy_rows=busy_rows,
        duration_minutes=duration_minutes,
        workday_start=workday_start,
        workday_end=workday_end,
        limit=limit,
        candidate_slots=candidate_slots,
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

    # 여기서 후보를 대신 고르면 tool description과 계약이 어긋납니다. 받은 인자를 그대로 넘기고,
    # final_slot/needs_agent_selection 정리는 fixed/schedule_decision.py에 맡깁니다.
    payload = decide_final_slot_payload(
        candidate_slots=candidate_slots,
        selected_slot=selected_slot,
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

    # 하위 agent는 supervisor 호출마다 새로 만들 이유가 없고, 매번 만들면 tool 바인딩 비용만 늘어납니다.
    global _NANA_SUBAGENT
    if _NANA_SUBAGENT is None:
        _NANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=week04_tools(),
            system_prompt=nana_system_prompt(),
        )

    # 하위 agent는 supervisor의 대화를 이어받지 않습니다. query 한 건만 새 대화로 처리합니다.
    result = _NANA_SUBAGENT.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)
    return json.dumps(
        {
            "selected_agent": "nana_agent",
            "answer": extract_final_text(result),
            "trace": events,
            # supervisor trace가 하위 tool 호출 순서를 그대로 볼 수 있게 이름만 따로 올려 둡니다.
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

    # 최종 시간 결정은 하위 agent의 tool 결과 안에만 남습니다. supervisor는 하위 trace를 뒤지지 않고
    # extract_langchain_trace(...)가 읽는 위치를 보므로, 여기서 payload를 한 단계 끌어올립니다.
    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None
    for event in events:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        # decide_final_slot payload는 top-level에 final_slot을 두는 것이 course repo 계약입니다.
        # 확정 못 한 경우에도 final_slot=None으로 키가 남으므로 값이 아니라 키 유무로 봅니다.
        if "final_slot" in content:
            # 같은 요청에서 여러 번 결정했다면 마지막 호출이 최종입니다.
            final_slot_payload = content
        # propose_group_schedule 호환 경로는 final_decision 아래에 결과를 넣습니다.
        if content.get("final_decision"):
            final_decision_payload = content["final_decision"]

    return json.dumps(
        {
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
