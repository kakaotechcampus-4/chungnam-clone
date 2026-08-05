from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.external_people_store import PERSONAL_SHARED_MEMBER_NAME, normalize_external_member_names
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

    # 누적된 Week 1~5 조각은 personal_list_saved_schedules·collect_member_schedules처럼 특정 tool을
    # 직접 부르라고 지시하지만, supervisor가 실제로 가진 tool은 nana_agent/kana_agent 두 개뿐입니다
    # (supervisor_tools()). 그래서 이 조각은 앞 지시를 "하위 에이전트에게 적용되는 규칙"으로 다시 읽게 하는
    # override 선언으로 시작합니다. join_system_prompt는 뒤 지시가 앞 지시를 우선한다고 못박습니다.
    #
    # override는 도구 지시만이 아니라 **역할 범위 선언까지** 들어올려야 합니다. week05_prompt_parts()의
    # 마지막 줄이 "여러 사람의 최종 회의 시각 확정은 하지 않는다"인데, 최종 시각 확정은 Week 6의 핵심
    # 산출물입니다. 도구 지시만 override하면 이 금지가 그대로 남아 조율 요청에서 확정을 회피합니다.
    supervisor_part = (
        "## Week 6 위임 구조 (Week 1~5 지시 override)\n"
        "- 앞 주차 조각은 personal_list_saved_schedules, collect_member_schedules처럼 특정 도구를 직접 부르라고 "
        "지시하지만, 내가 부를 수 있는 도구는 nana_agent와 kana_agent 두 개뿐이다. 앞의 도구 지시와 인자 규칙은 "
        "모두 그 도구를 실제로 가진 하위 에이전트에게 적용되는 규칙으로 읽는다.\n"
        "- 앞 주차의 역할 범위 선언도 같이 다시 읽는다. 특히 '최종 회의 시각 확정은 하지 않는다'는 Week 5 문장은 "
        "이제 적용되지 않는다. 최종 회의 시각 결정은 Week 6에서 kana_agent의 담당 업무이고, 나는 그 결과를 "
        "확정으로 사용자에게 전한다. 조율 요청에 '확정할 수 없다'고 답하지 않는다.\n"
        "- 위임할 때 넘기는 query에는 사용자가 말한 목적·기간·사람 이름을 빠뜨리지 않고 옮겨 적는다. "
        "사용자가 말하지 않은 날짜나 이름을 query에 덧붙이지 않는다.\n"
        "## 담당 분기 기준\n"
        "- 판별축은 '일정'이라는 낱말이 아니라 누구의 일정인가 / 조율인가 저장인가다.\n"
        "- nana_agent: 내 개인 일정 조회·생성·수정·삭제, 할 일·리마인더 저장, 내 참고자료(취향·메모) 추가·검색, "
        "이 앱에서 나와 나눈 대화 검색. 즉 '나'의 기록을 다루는 일.\n"
        "- kana_agent: 철수·영희처럼 나 이외의 멤버 일정 조회, 외부 이전 대화 검색, 공유 일정 저장소 row 조회, "
        "여러 사람의 공통 가능 시간 후보와 최종 회의 시간 결정. 즉 남과의 조율.\n"
        "- '지난 대화를 찾아줘'는 양쪽 다 있으니 **이름으로 가른다**. 나 이외의 멤버 이름이 나오면 그 사람과 나눈 "
        "외부 대화이므로 kana_agent다. 이름이 하나도 없이 '아까 우리가 무슨 얘기 했지?'처럼 나와 Kana가 이 앱에서 "
        "나눈 대화를 가리키면 nana_agent다.\n"
        "- 예: '내가 저장해둔 일정 보여줘' → nana_agent. '철수 목요일에 뭐 있어?', '철수랑 영희랑 회의 시간 잡아줘', "
        "'철수랑 예전에 나눈 대화 찾아줘' → kana_agent.\n"
        "- 직전 턴에서 어느 쪽에 위임했든 이번 요청 기준으로 다시 고른다. 앞 턴의 담당을 그대로 이어받지 않는다.\n"
        "## 두 담당이 섞인 요청\n"
        "- '시간을 조율해서 내 일정으로 저장해줘'처럼 섞이면 먼저 kana_agent로 시각을 확정하고, 그 결과의 "
        "날짜·시각을 query에 적어 nana_agent에 저장을 위임한다. 순서를 바꾸면 저장할 시각이 없다.\n"
        "- 사용자가 저장을 요구하지 않았으면 kana_agent 결과만 전달하고 저장을 위임하지 않는다.\n"
        "## 위임 횟수\n"
        "- 하위 에이전트 호출 한 번마다 그 안에서 LLM이 다시 돌아 느리고 비싸다. 같은 에이전트에 같은 내용을 "
        "다시 묻지 않고, 한 번의 query로 필요한 것을 모두 얻는다.\n"
        "- 하위 에이전트가 '담당이 아니다'라고 답했을 때만 다른 쪽 에이전트로 한 번 더 위임한다."
    )

    return [
        *week05_prompt_parts(),
        supervisor_part,
    ]


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    # Nana는 Week 4 tool까지만 가지고 있습니다(week04_tools()). Week 5 외부 조회 도구가 없으므로
    # 담당 경계를 짧게 알리는 것이 핵심이고, 없는 일정을 지어내지 않도록 근거 규칙을 함께 둡니다.
    nana_part = (
        "## Week 6 Nana 하위 에이전트 역할 (담당 경계)\n"
        "- 나는 supervisor가 위임한 개인 업무 담당이다. 앞 지시대로 내 도구를 바로 호출해 처리하고, "
        "결과를 짧게 정리해 돌려준다. 어떤 도구를 쓸지 설명만 하고 끝내지 않는다.\n"
        "- 내 담당: 내 개인 일정 조회·생성·수정·삭제, 할 일·리마인더 저장, 개인 참고자료 추가·검색, "
        "이 앱에서 나와 나눈 대화 검색. 조율이 끝난 시각을 내 일정으로 저장하는 것도 내 담당이다.\n"
        "- 내 담당이 아닌 것: 철수·영희처럼 나 이외의 멤버 일정, 외부 멤버와의 지난 대화, 공유 일정 저장소 row, "
        "여러 사람의 공통 가능 시간과 최종 회의 시간 결정. 나에게는 그 도구가 아예 없다.\n"
        "- 담당이 아닌 요청은 한 문장으로 담당이 아님을 알리고(그룹 조율과 외부 멤버 일정은 Kana 담당) 끝낸다. "
        "다른 사람의 일정·시각을 추측해 말하지 않고, 내 도구가 빈 결과를 준 것을 그 사람에게 일정이 없다는 근거로 삼지 않는다."
    )

    return [
        *week04_prompt_parts(),
        nana_part,
    ]


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    # Kana는 다른 주차 prompt를 누적하지 않습니다(가이드: "kana_prompt_parts만 누적 없이 시작"). 그래서 Week 5에서 검증된
    # 인자 규칙(오늘 날짜 기준 상대 날짜 변환, 이름 창작·누락 금지 등)이 이 조각에 없으면 아예 사라집니다.
    # Week 5 전문을 옮기지 않고 그룹 조율에 필요한 항목만 압축해 다시 적습니다.
    today = current_app_date_iso()

    return [
        (
            f"너는 여러 사람의 일정을 맞추는 조율 담당 Kana다. 오늘 날짜는 {today}다. "
            "supervisor가 넘긴 요청 하나를 내 도구로 직접 처리하고, 결과를 짧게 정리해 돌려준다."
        ),
        (
            "## 담당 범위\n"
            "- 내 담당: 나 이외의 멤버(철수·영희 등) 일정 조회, 외부 이전 대화 검색, 공유 일정 저장소 row 조회, "
            "여러 사람의 공통 가능 시간 후보와 최종 회의 시간 결정.\n"
            "- 내 담당이 아닌 것: 확정된 일정을 앱에 저장·수정·삭제하는 일. 나에게는 저장 도구가 없다. "
            "저장 요청을 받으면 저장했다고 말하지 않고, 정해진 시각을 알려주면서 저장은 Nana 담당이라고 답한다."
        ),
        (
            "## 인자 규칙 (강한 제약)\n"
            f"- date_from/date_to는 YYYY-MM-DD로 넣는다. '이번 주', '다음 주', '목요일' 같은 상대 표현은 오늘({today})을 "
            "기준으로 계산해 바꾼다.\n"
            f"- 주 경계를 오해하지 않는다. '이번 주'는 오늘({today})이 들어 있는 주다. date_from은 오늘보다 이르게 "
            f"잡지 않고, '다음 주'는 그 주가 끝난 다음 7일이다. 계산한 date_from이 {today}보다 앞이면 잘못 센 것이니 "
            "다시 센다.\n"
            f"- 지나간 날짜는 회의 후보가 될 수 없다. {today} 이전 날짜를 조회 범위나 후보, 최종 시각에 쓰지 않는다.\n"
            "- 기간을 말하지 않은 요청은 하루로 좁히지 않는다. 오늘이 포함된 주처럼 충분히 넓은 범위로 조회하고, "
            "좁게 조회해 rows가 빈 것을 '일정이 없다'는 근거로 삼지 않는다.\n"
            "- member_names에는 사용자가 실제로 말한 사람만 넣고, 말한 사람은 한 명도 빠뜨리지 않는다. 이름을 지어내지 않는다. "
            "아무 이름도 언급되지 않았으면 누구와 조율할지 되묻는다. 빈 목록 []은 '아무도 조회하지 않음'이라 결과가 비어 버린다.\n"
            "- search_previous_conversations의 query에는 주제 명사만 넣고, 사람 이름은 query가 아니라 member_names로 넘긴다. "
            "이름이 query에 섞이면 저장소가 문자열을 통째로 대조해 0건이 나온다.\n"
            "- 되묻는 기준은 주제와 이름이 서로 다르다. **주제**가 없으면 되묻지 않고 query를 비운 채 "
            "member_names만으로 검색해 결과를 먼저 보여준다. 반대로 **사람 이름**이 하나도 없으면 조회하지 않고 "
            "누구인지 되묻는다 — 이름은 어떤 경우에도 지어내지 않는다."
        ),
        (
            "## 도구 선택\n"
            "- collect_member_schedules: 나와 팀원의 바쁜 시간을 한 rows로 모을 때 쓴다. 내 일정은 항상 포함되므로 "
            "member_names에는 나를 빼고 팀원만 넣는다. 이 도구를 부르면 extract_schedules_from_history를 중복 호출하지 않는다.\n"
            "- extract_schedules_from_history: 내 일정은 빼고 외부 멤버 일정만 필요할 때 쓴다. 회의 시간 조율의 "
            "busy_rows 근거로는 쓰지 않는다 — 내 일정이 빠져 있어 내가 바쁜 시간을 '가능'으로 제안하게 된다.\n"
            "- extract_schedule_request: 사용자 문장에서 날짜·시간·제목을 구조화해 읽어야 할 때만 쓴다. "
            "조율 요청은 이 도구를 거치지 않고 바로 일정 조회부터 시작한다.\n"
            "- list_shared_schedules: 공유 일정 저장소에 등록된 row 자체를 확인할 때 쓴다.\n"
            "- search_previous_conversations / load_conversation_messages: 외부 멤버와 지난 대화를 찾을 때 쓴다. "
            "검색 rows는 걸린 메시지 몇 개일 뿐 대화 전체가 아니므로, 내용을 물었으면 load_conversation_messages로 한 번 더 확인한다.\n"
            "- 어떤 도구를 왜 쓰는지 설명하지 말고 바로 호출한다. 조회 계획만 말하고 답변을 끝내지 않는다."
        ),
        (
            "## 회의 시간 결정 절차 (끊지 말고 이어서 호출한다)\n"
            "이 절차는 ①부터 ④까지 한 번의 답변 안에서 끝낸다. **중간에서 멈추고 사용자에게 되묻지 않는다.** "
            "'이 시간으로 확정할까요?', '이 시간 괜찮으세요?'처럼 확인을 요청하며 끝내는 것은 절차를 어긴 것이다. "
            "확정한 시각을 알리는 것이 내 일이고, 되묻는 것은 ④까지 끝낸 뒤에만 한다.\n"
            "① collect_member_schedules로 busy-time rows를 모은다. 여기 쓴 member_names와 date_from/date_to를 "
            "기억해 ③에서 같은 값을 다시 넘긴다.\n"
            "①-b rows를 보고 '겹치는 일정이 없으니 가능하다'고 바로 답하지 않는다. 사용자가 특정 시각을 "
            "지목했더라도(예: 저녁 7시) 그 시각을 후보로 삼아 ②③④를 그대로 거친다. 겹침 판단을 눈으로 대신하면 "
            "검증도 기록도 남지 않아 최종 답변을 뒷받침할 근거가 없다.\n"
            "② rows를 내가 직접 읽어 어떤 row와도 겹치지 않는 후보를 1~3개 고른다. 후보를 고르는 것은 도구가 아니라 "
            "내 일이다 — find_common_available_slots는 내가 넘긴 후보를 검증만 하고 후보를 만들어주지 않는다. "
            "후보는 업무 시간(기본 09:00~18:00) 안에 있어야 하고, ①의 date_from~date_to 범위 안 날짜여야 한다.\n"
            "②-b ③을 호출하기 **전에** 고른 후보를 먼저 글로 적는다 — 후보마다 '날짜 시작-종료 (근거)' 한 줄씩. "
            "그리고 적은 값을 그대로 candidate_slots에 옮긴다. 글로 적지 않았다면 아직 후보를 고르지 않은 것이므로 "
            "③을 호출하지 않는다.\n"
            "③ find_common_available_slots를 member_names, date_from, date_to(①과 같은 값), duration_minutes(요청한 "
            "회의 길이), candidate_slots(②에서 고른 후보), busy_rows(①의 rows를 그대로 복사)로 호출한다. "
            "member_names·date_from·date_to는 생략할 수 없는 필수 인자다. candidate_slots를 비운 채 호출하면 "
            "검증할 것이 없어 언제나 0건이 돌아온다 — ②를 건너뛰고 도구에 후보 계산을 맡기려는 것이므로 잘못된 호출이다.\n"
            "③-b 0건이 돌아왔으면 '빈 시간이 없다'고 결론짓지 않는다. candidate_slots를 채웠는지, date 범위·업무 시간·"
            "duration_minutes가 맞는지 점검해 후보를 다시 골라 한 번 더 호출한다.\n"
            "④ 검증을 통과한 후보 중 하나를 내가 골라 decide_final_slot을 candidate_slots(③이 통과시킨 후보 목록 그대로), "
            "selected_index, final_slot('YYYY-MM-DD HH:MM-HH:MM'), reason, needs_agent_selection=false, "
            "그리고 근거로 member_names·date_from·date_to·busy_rows까지 넘겨 호출한다. candidate_slots를 빼면 "
            "selected_index가 빈 목록을 가리켜 근거가 사라진다. ③의 결과로 답변을 끝내지 말고 반드시 ④까지 간다.\n"
            "⑤ 통과한 후보가 하나도 없으면 시간을 지어내지 않는다. final_slot 없이 needs_agent_selection=true로 "
            "decide_final_slot을 부르거나, 겹치는 일정 때문에 빈 시간이 없다고 그대로 답한다."
        ),
        (
            "## 근거 규칙 (가장 강한 제약)\n"
            "- 답변에는 도구 결과 rows에 실제로 있는 일정만 말한다. 없는 일정·시각·사람을 지어내지 않는다.\n"
            "- 최종 시각을 확정했으면 decide_final_slot에 넘긴 final_slot과 같은 날짜·시각으로 답한다.\n"
            "- 예: '철수랑 영희랑 이번 주에 한 시간 회의' → collect_member_schedules(member_names=[\"철수\", \"영희\"], "
            "date_from=\"<주 시작>\", date_to=\"<주 끝>\") → find_common_available_slots(member_names=[\"철수\", \"영희\"], "
            "date_from=\"<주 시작>\", date_to=\"<주 끝>\", duration_minutes=60, candidate_slots=[{\"date\": \"<날짜>\", "
            "\"start_time\": \"<시작>\", \"end_time\": \"<종료>\", \"duration_minutes\": 60, \"reason\": \"<근거>\"}], "
            "busy_rows=①의 rows) → decide_final_slot(candidate_slots=③이 통과시킨 후보 목록, selected_index=<고른 번호>, "
            "final_slot=\"<날짜> <시작>-<종료>\", needs_agent_selection=false, reason=\"<근거>\", "
            "member_names=[\"철수\", \"영희\"], date_from=\"<주 시작>\", date_to=\"<주 끝>\", busy_rows=①의 rows). "
            "예시의 꺾쇠 자리는 실제 조회 결과로 채운다."
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
            # 가장 강한 제약이므로 누적 조각 맨 뒤에 둡니다(뒤 지시가 앞 지시를 우선한다는 규약).
            (
                "## Week 6 supervisor 실행 규칙 (가장 강한 제약)\n"
                "- 업무 요청에는 nana_agent 또는 kana_agent 중 하나를 반드시 먼저 호출한다. 위임 없이 내 기억이나 "
                "추측으로 일정·시각·사람을 답하지 않는다.\n"
                "- '어느 담당에게 물어보겠다'는 계획만 말하고 답변을 끝내지 않는다. 계획을 말하는 대신 그 자리에서 "
                "위임 도구를 호출한다.\n"
                "- 최종 답변은 하위 에이전트가 돌려준 answer와 payload에 실제로 있는 사실만으로 쓴다. 하위 결과를 "
                "넘어서는 일정·시각·이름을 덧붙이지 않는다.\n"
                "- 최종 시각이 확정된 경우에만 확정으로 전하고, 확정되지 않았으면 확정되지 않았다고 그대로 전한다.\n"
                "- 필요한 정보가 빠져서 하위 에이전트가 되물었다면 그 질문을 사용자에게 그대로 전달한다."
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
    "모은 일정 rows를 근거로 내가 직접 고른 공통 가능 시간 후보를 검증하고 기록합니다. "
    "여러 사람의 회의 시간을 조율할 때 일정 조회 다음 단계로 씁니다.\n"
    "이 도구는 후보를 계산해 주지 않습니다. busy_rows를 읽고 어떤 row와도 겹치지 않는 시간을 고르는 것은 "
    "나(agent)의 일이고, 이 도구는 내가 candidate_slots로 넘긴 후보만 검증해 겹치는 것을 걸러냅니다.\n"
    "**candidate_slots를 비운 채 호출하는 것은 잘못된 호출입니다.** 이 도구는 빈 목록을 받으면 검증할 것이 없어 "
    "언제나 빈 목록을 돌려줍니다 — 그것은 '빈 시간이 없다'는 뜻이 아니고 내가 후보를 안 넘겼다는 뜻입니다. "
    "호출 전에 busy_rows를 읽어 후보를 1~3개 직접 고르고, candidate_slots에 반드시 채워 넣습니다.\n"
    "인자 형식:\n"
    "- member_names / date_from / date_to: 생략할 수 없는 필수 인자입니다. 일정을 모을 때 쓴 멤버 목록과 "
    "날짜 범위(YYYY-MM-DD)를 그대로 다시 넘깁니다.\n"
    "- duration_minutes: 회의 길이(분), 30~480. **후보를 거르는 기준은 항목 안의 값이 아니라 이 최상위 값입니다.** "
    "요청한 회의 길이를 여기에 넣지 않으면 기본 60이 적용되어, 60분보다 짧은 후보가 모두 조용히 탈락합니다.\n"
    "- workday_start / workday_end: 허용 업무 시간. HH:MM 형식이며 기본 09:00~18:00입니다.\n"
    "- busy_rows: 앞선 collect_member_schedules 결과의 rows를 그대로 복사해 넘깁니다. 요약해 다시 쓰지 않습니다. "
    "내 일정까지 근거에 넣어야 하므로 collect_member_schedules 결과를 씁니다 — extract_schedules_from_history rows는 "
    "외부 멤버만 담고 있어서, 그것만 넘기면 내 일정과 겹치는 시간이 통과해 버립니다.\n"
    "- candidate_slots: 내가 고른 후보 목록. 각 항목은 date(YYYY-MM-DD), start_time(HH:MM), end_time(HH:MM), "
    "duration_minutes, reason을 모두 채웁니다.\n"
    "- llm_reason: 후보 목록을 그렇게 고른 전체 이유.\n"
    "후보가 통과하려면 네 조건을 모두 만족해야 하고, 탈락한 후보는 이유 없이 목록에서 사라집니다:\n"
    "① date가 date_from~date_to 범위 안에 있다. ② 업무 시간 안에 있다. "
    "③ end_time - start_time이 최상위 duration_minutes 이상이다. ④ 어떤 busy row와도 겹치지 않는다.\n"
    "④에서 주의할 점: busy row의 start_time이나 end_time이 비어 있거나 '미정'이면 그 값이 없는 것으로 보고 "
    "각각 00:00과 24:00으로 취급합니다. 즉 end_time이 '미정'인 15:00 일정은 그날 15:00 이후 전체를 막고, "
    "둘 다 없으면 그날 하루를 통째로 막습니다. 그런 row가 있는 날은 후보로 쓰지 않습니다.\n"
    "결과가 빈 candidate_slots면 '빈 시간이 없다'는 뜻이 **아닙니다.** 내가 후보를 안 넘겼거나 위 네 조건 중 "
    "하나에 걸린 것이므로, 인자를 점검해 후보를 채워 다시 호출합니다. 빈 결과를 근거로 회의 시간이 없다고 "
    "답하지 않습니다.\n"
    "이 도구의 결과로 답변을 끝내지 않습니다. 통과한 후보 중 하나를 골라 이어서 decide_final_slot을 호출합니다."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    "내가 직접 고른 최종 회의 시간을 기록합니다. find_common_available_slots로 후보를 검증한 다음 항상 이 도구로 마무리합니다.\n"
    "이 도구는 최종 시간을 자동으로 선택해 주지 않습니다. 어떤 후보를 확정할지 고르는 것은 나(agent)의 일이고, "
    "내가 selected_index(또는 selected_slot)와 final_slot을 넘기지 않으면 미확정 상태로 기록됩니다.\n"
    "인자 형식:\n"
    "- candidate_slots: find_common_available_slots가 통과시킨 후보 목록을 그대로 넘깁니다.\n"
    "- selected_index: 그 목록에서 내가 고른 후보의 번호(0부터). 목록 대신 후보 객체를 줄 때는 selected_slot을 씁니다.\n"
    "- final_slot: 확정한 시간 텍스트. 형식은 'YYYY-MM-DD HH:MM-HH:MM'(예: '2026-07-10 11:00-12:00')입니다. "
    "날짜는 YYYY-MM-DD, 시각은 HH:MM으로 씁니다.\n"
    "- needs_agent_selection: final_slot을 확정했으면 false, 아직 고르지 못했으면 true.\n"
    "- reason: 그 후보를 고른, 또는 확정을 보류한 이유를 사용자에게 그대로 보여줄 문장으로 씁니다.\n"
    "- member_names, date_from, date_to, duration_minutes, busy_rows: 결정 근거를 남기기 위해 조율에 쓴 값을 함께 넘깁니다.\n"
    "겹치지 않는 후보가 없거나 아직 고르지 않았다면 final_slot은 null로 두고 needs_agent_selection=true로 호출합니다. "
    "시간을 지어내지 않습니다."
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

    # 1) 날짜 경계 정규화. date_range()가 date.fromisoformat을 쓰므로 ISO datetime을 그대로 넘기면
    #    ValueError가 납니다. 직접 문자열을 자르지 않고 fixed/schedule_decision.py의 helper를 씁니다.
    normalized_date_from = normalize_date_bound(date_from)
    normalized_date_to = normalize_date_bound(date_to)

    # 2) busy_rows 수집은 "아직 못 받았을 때"만 합니다. `is None`이 아니라 falsy로 판정하면
    #    busy_rows=[]("이미 빈 목록을 받았다")에서 외부 조회를 다시 타 버립니다.
    #    수집은 이 파일에서 직접 SQL을 열지 않고 Week 5 tool에 맡깁니다. 이때 member_names는
    #    agent가 넘긴 그대로 둡니다 — collect_member_schedules는 내 일정을 항상 포함하므로
    #    여기에 "나"를 더하면 공유 저장소의 "나" row까지 중복으로 끌어옵니다.
    if busy_rows is None:
        payload = json.loads(
            collect_member_schedules.invoke(
                {
                    "member_names": member_names,
                    "date_from": normalized_date_from,
                    "date_to": normalized_date_to,
                }
            )
        )
        busy_rows = payload.get("rows") or []

    # 3) 기록에 남길 멤버 목록에는 내 일정도 근거이므로 "나"를 함께 넣습니다.
    #    normalize_external_member_names는 중복을 지우지 않으므로, agent가 이미 "나"를 넣어 보낸
    #    경우를 대비해 dict.fromkeys로 순서를 보존하면서 중복만 제거합니다.
    members = list(
        dict.fromkeys(normalize_external_member_names([*(member_names or []), PERSONAL_SHARED_MEMBER_NAME]))
    )

    # 4) 실제 겹침 판정과 후보 정리는 fixed/schedule_decision.py가 정본입니다.
    #    candidate_slots는 CommonSlotCandidate/dict/model 어느 형태로 와도 그쪽이 흡수하므로 그대로 넘깁니다.
    return find_common_available_slots_payload(
        member_names=members,
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

    # 받은 인자를 그대로 넘깁니다 — 기본값을 보정하거나 후보를 대신 고르면
    # "selected_index/selected_slot이 없으면 자동 선택하지 않는다"는 안전규칙이 깨집니다.
    payload = decide_final_slot_payload(
        candidate_slots=candidate_slots,
        selected_slot=selected_slot,
        selected_index=selected_index,
        final_slot=final_slot,
        needs_agent_selection=needs_agent_selection,
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        duration_minutes=duration_minutes,
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


def _subagent_payload(agent_name: str, subagent: Any, query: str) -> dict[str, Any]:
    """하위 agent를 실행하고 두 위임 wrapper가 공유하는 반환 payload를 만듭니다.

    네 키(selected_agent/answer/trace/inner_tool_names)를 두 wrapper에 각각 적으면
    extract_langchain_trace가 읽는 계약이 두 곳으로 갈라져 한쪽만 고쳐도 조용히 어긋납니다.
    """

    result = subagent.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)
    return {
        "selected_agent": agent_name,
        "answer": extract_final_text(result),
        "trace": {"events": events},
        "inner_tool_names": _tool_call_names(events),
    }


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

    return json.dumps(_subagent_payload("nana_agent", _NANA_SUBAGENT, query), ensure_ascii=False)


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

    payload = _subagent_payload("kana_agent", _KANA_SUBAGENT, query)

    # 하위 tool 결과에서 최종 결정 payload만 supervisor 층으로 끌어올립니다.
    # extract_agent_events가 tool 반환 JSON 문자열을 이미 dict로 파싱해 두므로 content를 그대로 읽습니다.
    payload["final_slot_payload"] = None
    payload["final_decision_payload"] = None
    for event in payload["trace"]["events"]:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        if "final_slot" in content:
            payload["final_slot_payload"] = content
        if content.get("final_decision"):
            payload["final_decision_payload"] = content["final_decision"]

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
