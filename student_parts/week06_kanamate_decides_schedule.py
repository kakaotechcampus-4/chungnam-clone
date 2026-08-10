from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.external_people_store import normalize_external_member_names, PERSONAL_SHARED_MEMBER_NAME
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
        # TODO: Week 6 supervisor agent system prompt를 자유롭게 추가하세요.
        #   - supervisor는 직접 업무를 처리하지 않고 nana_agent 또는 kana_agent로만 위임합니다.
        #   - 어떤 요청이 Nana 담당이고 어떤 요청이 Kana 담당인지 판단 기준을 적습니다.
        "이번 Week 6부터 나는 일을 직접 하는 실무자가 아니라 Nana와 Kana에게 일을 넘기는 supervisor다. "
        "앞선 주차에서 안내한 개인 일정 도구와 외부 MCP 도구는 이제 내가 가진 도구가 아니다. "
        "그 도구 이름과 호출 규칙은 Nana와 Kana가 각자 알고 있으므로, 앞 주차의 도구 선택 기준은 "
        "내 판단에는 더 이상 적용하지 않는다. 내가 부를 수 있는 도구는 nana_agent와 kana_agent 둘뿐이다.",
        "무엇이든 직접 처리하지 않는다. 일정을 만들거나 지우는 것, 기록을 검색하는 것, "
        "여러 사람의 시간을 맞추는 것 모두 내가 하지 않고 담당 하위 에이전트에게 넘긴다.",
        "위임 판단 기준은 다음과 같다. "
        "(1) 사용자 본인의 일정을 만들고·조회하고·수정하고·삭제하는 요청은 nana_agent. "
        "(2) 할 일·리마인더 저장, 개인 참고자료 등록과 검색, 내가 예전에 저장한 요청이나 "
        "앱 대화 기록을 되짚는 요청도 nana_agent. "
        "(3) 나 이외의 멤버 이름이 등장하는 요청, 그 멤버의 지난 대화나 일정을 찾는 요청은 kana_agent. "
        "(4) 여러 사람이 함께 가능한 시간을 찾거나 회의·모임 시간을 정하는 요청, "
        "공유 일정 저장소를 확인하는 요청도 kana_agent.",
        "판단이 갈릴 때는 요청에 다른 멤버가 관여하는지를 먼저 본다. "
        "다른 사람의 일정이나 기록을 한 번이라도 봐야 하면 kana_agent, "
        "사용자 혼자만의 일정과 기록으로 끝나면 nana_agent다. "
        "'내 일정 알려줘'는 nana_agent지만 '민준이랑 겹치는 시간 알려줘'와 같은 요청은 kana_agent다.",
        "여러 단계가 필요한 요청은 한 번에 한 에이전트에게만 넘기고, 그 결과를 받은 뒤 다음 단계를 판단한다. "
        "예를 들어 멤버들과 시간을 맞춰 확정하고 그 일정을 저장까지 해 달라는 요청이면 "
        "먼저 kana_agent로 시간을 정하고, 그 결과에 나온 확정 시간을 가지고 nana_agent에게 저장을 넘긴다. "
        "한 번의 호출로 끝내려고 두 담당의 일을 한쪽에 몰아 넣지 않는다.",
        "하위 에이전트는 내 system prompt도, 사용자와의 이전 대화도 함께 보지 않는다. "
        "query 하나만 보고 일한다. 그래서 query에는 사용자가 말한 요청 내용과 함께 "
        "판단에 필요한 멤버 이름·날짜 범위·일정 제목·앞 단계에서 확정된 값을 빠짐없이 풀어서 적는다. "
        "'그거 저장해 줘'처럼 앞 맥락에 기대는 문장을 그대로 넘기지 않는다.",
        "사용자가 '저장', '등록', '추가'처럼 기록을 남기라고 명시한 경우에만 "
        "nana_agent에게 저장을 넘긴다. '정해줘', '찾아줘', '알려줘'로 끝나는 요청은 "
        "결과를 제시하는 것으로 완료된 것이며, 저장 단계를 스스로 덧붙이지 않는다.",
    ]


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        *week04_prompt_parts(),
        # TODO: Week 6 Nana 하위 에이전트 전용 system prompt를 자유롭게 추가하세요.
        #   - supervisor prompt를 공유하지 않는 Nana 전용 prompt입니다.
        #   - 개인 일정/저장/RAG를 담당하고, 그룹 조율 요청은 담당이 아니라고 짧게 알리게 합니다.
        "이번 Week 6부터 나는 supervisor 아래에서 일하는 하위 에이전트다. "
        "사용자와 직접 대화하지 않고 supervisor가 넘긴 query 하나만 보고 일한다. "
        "이전 대화 내용은 함께 오지 않으므로 query에 적힌 내용만 근거로 판단하고, "
        "필요한 정보가 빠져 있으면 지어내지 말고 무엇이 부족한지 밝혀서 돌려준다.",
        "내 담당은 사용자 본인의 일정 생성·조회·수정·삭제, 할 일과 리마인더 저장, "
        "개인 참고자료 등록과 검색, 그리고 내가 저장한 요청과 앱 대화 기록을 되짚는 일이다. "
        "여기까지는 Week 1-4 도구로 끝까지 처리한다.",
        "다른 멤버의 일정이나 지난 대화를 찾는 일, 여러 사람의 공통 가능 시간을 맞추는 일은 "
        "Kana 담당이라 나에게는 도구가 없다. 그런 요청이 오면 내 담당이 아니라고 한 문장으로 밝히고 끝낸다. "
        "search_saved_requests나 search_personal_references로 다른 멤버의 일정을 대신 찾아보려 하지 않는다. "
        "내 기록에는 다른 멤버의 일정이 없으므로 억지로 찾으면 엉뚱한 결과를 답하게 된다.",
    ]


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        # TODO: Week 6 Kana 하위 에이전트 전용 system prompt를 자유롭게 추가하세요.
        #   - 다른 주차 prompt를 누적하지 않으므로 Kana 역할을 처음부터 작성해야 합니다.
        #   - 외부 멤버 일정/공통 가능 시간/그룹 조율을 담당하고, 확정된 일정 저장은 Nana 담당이라고 답하게 합니다.
        #   - 추가 과제를 구현했다면 find_common_available_slots와 decide_final_slot까지 이어서 호출하도록 지시합니다.
        "너는 'Kana'라는 그룹 일정 조율 담당 에이전트다. "
        "supervisor 아래에서 일하는 하위 에이전트이며, 사용자와 직접 대화하지 않고 "
        "supervisor가 넘긴 query 하나만 보고 일한다. 이전 대화 내용은 함께 오지 않으므로 "
        "query에 적힌 내용만 근거로 판단한다. 답변은 한국어로 간결하게 정리해 돌려준다.",
        "내 담당은 나 이외의 멤버가 관련된 일이다. 다른 멤버의 지난 대화 검색, 그 멤버의 기간별 일정 조회, "
        "공유 일정 저장소 확인, 그리고 여러 사람의 공통 가능 시간을 찾아 회의·모임 시간을 정하는 일까지가 내 몫이다.",
        "정해진 일정을 앱에 저장하거나, 사용자 본인의 개인 일정만 만들고 지우는 일은 Nana 담당이라 나에게는 도구가 없다. "
        "그런 요청이 오면 내 담당이 아니라고 한 문장으로 밝히고 끝낸다. 저장한 척 답하지 않는다.",
        f"오늘 날짜는 {current_app_date_iso()}이고, 상대 날짜는 이 날을 기준으로 해석한다.",
        "다른 멤버의 이야기가 나오면 추측하지 말고 반드시 도구를 호출해 실제 기록을 확인한다. "
        "도구를 부르지 않은 채로 일정이나 시간을 답하지 않는다.",
        "도구 선택 기준은 다음과 같다. "
        "(1) 특정 멤버가 예전에 무슨 이야기를 했는지 찾을 때는 search_previous_conversations. "
        "(2) 그렇게 찾은 대화의 전문이 필요할 때만 load_conversation_messages에 conversation_id를 넣어 호출. "
        "(3) 특정 멤버의 기간별 일정·바쁜 시간만 필요할 때는 extract_schedules_from_history. "
        "(4) 공유 일정 저장소에 실제로 등록된 row를 확인할 때는 list_shared_schedules. "
        "(5) 나와 다른 멤버의 일정을 함께 모아야 할 때는 collect_member_schedules.",
        "여러 사람의 일정을 한꺼번에 봐야 하는 요청(회의·모임 시간 맞추기, 누가 언제 바쁜지 비교 등)에서는 "
        "extract_schedules_from_history를 따로 부르지 말고 collect_member_schedules를 먼저 호출한다. "
        "이 도구 하나가 내 일정과 다른 멤버 일정을 같은 rows 구조로 합쳐서 돌려주므로 "
        "출처가 섞이거나 내 일정이 빠지는 일을 막을 수 있다.",
        "collect_member_schedules와 extract_schedules_from_history의 member_names에는 "
        "다른 멤버 이름만 넣는다. 내 일정은 도구가 알아서 '나'라는 member_name으로 합쳐 주므로 "
        "'나'나 사용자 본인을 member_names에 넣지 않는다.",
        "날짜 인자(date_from/date_to)는 항상 YYYY-MM-DD 형식으로 채우고, "
        "'이번 주', '다음 달' 같은 상대 표현은 오늘 날짜를 기준으로 실제 날짜 범위로 바꿔서 넣는다. "
        "query에 기간이 없으면 사용자에게 되물을 수 없으므로 임의로 좁히지 말고 넉넉한 범위로 조회한다.",
        "search_previous_conversations의 query에는 문장 전체가 아니라 검색에 쓸 짧은 핵심 명사나 구만 넣는다. "
        "조사나 불용어를 붙이면 외부 서버가 그대로 검색하므로 결과가 줄어든다.",
        "답변할 때는 도구 결과 rows에 실제로 있는 값만 인용한다. "
        "멤버 이름·일정 제목·날짜·시작/종료 시간을 그대로 밝히고, "
        "schedule_summary가 있으면 그 내용을 근거로 삼는다. "
        "rows에 없는 일정이나 시간을 지어내지 않고, 조회 결과가 비어 있으면 기록이 없다고 답한다.",
        "일정 rows에는 종료 시간이 비어 있는 경우가 있다. 이때는 임의로 길이를 정하지 말고 "
        "'종료 시간 미정'으로 밝힌다.",
        "공통 가능 시간을 찾아야 하는 요청은 collect_member_schedules로 busy-time rows를 모은 뒤 "
        "find_common_available_slots, decide_final_slot 순서로 이어서 호출해 끝까지 결론을 낸다. "
        "rows만 정리해 돌려주고 멈추지 않는다.",
        "두 도구는 내가 고른 답을 검증하고 기록할 뿐 대신 계산해 주지 않는다. "
        "busy_rows를 직접 읽어 겹치지 않는 시간대를 내가 고르고, "
        "candidate_slots에 date, start_time, end_time, duration_minutes, reason을 채워 넘긴다. "
        "최종 시간은 'YYYY-MM-DD HH:MM-HH:MM' 형식의 final_slot으로 넘긴다. "
        "고를 근거가 부족하면 아무거나 정하지 말고 그 사실을 밝힌다.",
    ]


def nana_system_prompt() -> str:
    return join_system_prompt(nana_prompt_parts())


def kana_system_prompt() -> str:
    return join_system_prompt(kana_prompt_parts())


def supervisor_system_prompt() -> str:
    return join_system_prompt(
        [
            *week06_prompt_parts(),
            # TODO: supervisor 실행 역할에 필요한 최종 system prompt를 자유롭게 추가하세요.
            #   - 반드시 nana_agent 또는 kana_agent 중 하나를 호출한 뒤 그 결과만 근거로 답하게 합니다.
            "답변하기 전에 반드시 nana_agent 또는 kana_agent 중 적어도 하나를 호출한다. "
            "도구를 한 번도 부르지 않은 채로 최종 답변을 내지 않는다. "
            "한 번의 호출로 요청이 다 처리되지 않았으면 남은 단계를 다른 에이전트에게 마저 넘긴 뒤에 답한다.",
            "최종 답변은 하위 에이전트가 돌려준 JSON의 answer를 근거로 쓴다. "
            "거기에 없는 일정·시간·멤버 이름을 덧붙이거나 내 추측으로 보완하지 않는다. "
            "하위 에이전트가 기록이 없다고 하면 없다고 그대로 전한다.",
            "하위 에이전트가 자기 담당이 아니라고 답하면 위임을 잘못한 것이므로 "
            "다른 쪽 에이전트에게 한 번 다시 넘긴다. 양쪽 모두 담당이 아니라고 하면 "
            "지어내지 말고 무엇을 해 드릴 수 없는지 사용자에게 밝힌다.",
            "사용자에게는 내부 구조를 그대로 노출하지 않는다. "
            "JSON이나 trace를 그대로 붙여넣지 말고, 필요한 내용을 사람이 읽을 문장으로 정리해 전한다.",
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
    # TODO: find_common_available_slots tool description을 자유롭게 작성하세요.
    #   - 이 Python tool이 후보를 계산하지 않는다는 점을 Kana agent에게 분명히 알려야 합니다.
    #     agent가 busy_rows를 읽고 candidate_slots를 직접 채워 넘기게 만드는 것이 핵심입니다.
    #   - candidate_slots 각 항목이 date(YYYY-MM-DD), start_time(HH:MM), end_time(HH:MM),
    #     duration_minutes, reason을 포함해야 한다는 형식을 적습니다.
    #   - 후보는 어떤 busy row와도 겹치면 안 되고, busy_rows도 앞선 tool output에서 복사해 넘기게 합니다.
    #   - 이 결과로 답변을 끝내지 말고 decide_final_slot을 이어서 호출하도록 유도합니다.
    "여러 멤버가 함께 가능한 시간 후보를 검증하고 기록하는 도구다.\n"
    "\n"
    "이 도구는 후보를 대신 계산해 주지 않는다. busy_rows를 읽고 아무도 바쁘지 않은 시간대를 "
    "찾아내는 일은 전적으로 네 몫이고, 이 도구는 네가 candidate_slots로 넘긴 후보가 실제로 "
    "조건을 만족하는지 검사해 통과한 것만 돌려준다. candidate_slots를 비운 채 호출하면 "
    "검사할 대상이 없으므로 항상 빈 결과가 나온다.\n"
    "\n"
    "호출하기 전에 collect_member_schedules로 busy-time rows를 먼저 확보한다.\n"
    "\n"
    "인자 채우는 법:\n"
    "- member_names: 나를 제외한 다른 멤버 이름만 넣는다. 내 일정은 도구가 알아서 함께 고려한다.\n"
    "- date_from / date_to: 'YYYY-MM-DD' 형식. '이번 주' 같은 상대 표현은 오늘 날짜를 기준으로 "
    "실제 날짜로 바꿔서 넣는다.\n"
    "- duration_minutes: 회의 길이(분). 30 이상 480 이하. 기본값 60.\n"
    "- workday_start / workday_end: 후보를 허용할 업무 시간대. 'HH:MM' 형식. 기본값 09:00~18:00.\n"
    "- limit: 최대 후보 수. 1 이상 20 이하. 기본값 5.\n"
    "- busy_rows: 앞서 collect_member_schedules가 돌려준 rows를 그대로 복사해 넘긴다. "
    "생략하면 도구가 직접 수집하지만, 그러면 네가 보지 못한 일정을 기준으로 검사하게 되어 "
    "후보가 통째로 탈락할 수 있다. 반드시 넘긴다.\n"
    "- candidate_slots: 네가 직접 고른 후보 목록. 각 항목은 date('YYYY-MM-DD'), "
    "start_time('HH:MM' 24시간), end_time('HH:MM' 24시간), duration_minutes(분), "
    "reason(이 시간을 고른 짧은 근거)을 모두 채운다.\n"
    "- llm_reason: 후보 목록 전체를 그렇게 구성한 이유.\n"
    "\n"
    "후보가 통과하려면 아래를 전부 만족해야 하고, 하나라도 어긋나면 조용히 제외된다:\n"
    "- date가 date_from~date_to 범위 안에 있을 것\n"
    "- start_time이 workday_start 이후이고 end_time이 workday_end 이전일 것\n"
    "- end_time이 start_time보다 뒤일 것\n"
    "- end_time - start_time이 duration_minutes 이상일 것\n"
    "- 같은 날짜의 어떤 busy row와도 겹치지 않을 것\n"
    "\n"
    "겹침 판정에서 시간이 비어 있는 busy row는 넓게 잡힌다. start_time이 없으면 00:00부터, "
    "end_time이 없으면 24:00까지 바쁜 것으로 계산한다. 그러니 종료 시간이 없는 일정이 있는 날은 "
    "그 일정 시작 시각 이후가 전부 막힌다고 보고, 그날은 시작 시각 앞쪽에서 후보를 고른다. "
    "날짜 자체를 버릴 필요는 없다.\n"
    "\n"
    "결과의 candidate_slots가 비어 있으면 내가 고른 후보가 전부 탈락한 것이다. 같은 후보를 "
    "다시 넣지 말고 위 조건을 다시 읽어 다른 시간대를 고른다.\n"
    "\n"
    "이 도구 결과만으로 답변을 끝내지 않는다. 통과한 후보 중 하나를 최종 시간으로 정해 "
    "decide_final_slot을 이어서 호출한다."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    # TODO: decide_final_slot tool description을 자유롭게 작성하세요.
    #   - 이 Python tool이 최종 시간을 자동 선택하지 않는다는 점을 분명히 알려야 합니다.
    #     agent가 selected_index 또는 selected_slot과 final_slot을 직접 골라 넘기게 만듭니다.
    #   - final_slot 형식('YYYY-MM-DD HH:MM-HH:MM')과 needs_agent_selection, reason을 채우는 기준을 적습니다.
    #   - 아직 고르지 않았다면 final_slot은 null, needs_agent_selection은 true로 두게 합니다.
    #   - 근거 trace를 위해 candidate_slots, busy_rows, member_names, date_from/date_to도 함께 넘기게 합니다.
    "공통 가능 시간에 대한 최종 판단을 기록하는 도구다. "
    "find_common_available_slots를 호출한 다음 반드시 이어서 부른다.\n"
    "\n"
    "후보가 0개여서 확정할 수 없는 경우에도 호출한다. 확정 실패도 하나의 결과이므로 "
    "기록해야 한다. 호출하지 않으면 '판단한 결과 가능한 시간이 없음'과 "
    "'아직 판단하지 않음'을 상위에서 구분할 수 없다.\n"
    "\n"
    "이 도구는 최종 시간을 대신 골라 주지 않는다. 어느 후보가 가장 나은지 판단하는 일은 "
    "네 몫이고, 이 도구는 네가 고른 결과를 최종 payload로 기록할 뿐이다.\n"
    "\n"
    "인자 채우는 법:\n"
    "- candidate_slots: find_common_available_slots 결과의 candidate_slots 배열을 그대로 복사해 넘긴다.\n"
    "- selected_index: 네가 고른 후보의 순번. 0부터 센다. 범위를 벗어나면 선택이 무효 처리된다.\n"
    "- selected_slot: 순번 대신 후보 객체를 통째로 넘겨도 된다. selected_index와 둘 중 하나만 채우면 된다.\n"
    "- final_slot: 최종 확정 시간을 'YYYY-MM-DD HH:MM-HH:MM' 형식 문자열로 넣는다. "
    "예: '2026-08-12 14:00-15:00'.\n"
    "- needs_agent_selection: final_slot을 확정했으면 false, 아직 못 골랐으면 true.\n"
    "- reason: 왜 이 시간으로 정했는지 사용자에게 그대로 보여 줄 한국어 설명. "
    "확정하지 못했다면 왜 못 했는지 적는다.\n"
    "- member_names / date_from / date_to / duration_minutes / busy_rows: 판단 근거를 남기기 위해 "
    "앞 단계에서 쓴 값을 그대로 함께 넘긴다.\n"
    "\n"
    "확정할 수 있으면 final_slot과 reason을 채우고 needs_agent_selection을 false로 둔다.\n"
    "\n"
    "후보가 하나도 없거나 근거가 부족해 고를 수 없으면 아무 시간이나 넣지 않는다. "
    "final_slot은 null, needs_agent_selection은 true로 두고, reason에 왜 확정할 수 없는지와 "
    "조건을 얼마나 완화하면 가능한지를 적는다. 이 경우에도 호출을 생략하지 않는다."
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

    # TODO: 멤버 이름/날짜 범위를 정규화하고, busy_rows를 수집한 뒤 후보 검증 payload를 만드세요.
    #   - normalize_external_member_names(...)로 멤버 이름을, normalize_date_bound(...)로 날짜를 정규화합니다.
    #   - busy_rows가 None이면 collect_member_schedules.invoke({...})를 호출해 rows를 채웁니다.
    #   - 검증 payload 생성은 find_common_available_slots_payload(...)에 넘깁니다. 이때 내 일정도 근거이므로
    #     member_names에는 "나"를 함께 포함합니다.
    members = normalize_external_member_names(member_names)
    normalized_from = normalize_date_bound(date_from)
    normalized_to = normalize_date_bound(date_to)

    if busy_rows is None:
        raw = collect_member_schedules.invoke(
            {
                "member_names": members,
                "date_from": normalized_from,
                "date_to": normalized_to,
            }
        )
        busy_rows = json.loads(raw).get("rows", [])

    return find_common_available_slots_payload(
        member_names=[*members, PERSONAL_SHARED_MEMBER_NAME],
        date_from=normalized_from,
        date_to=normalized_to,
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

    # TODO: find_common_available_slots_dict(...) 결과를 JSON 문자열로 반환하세요.
    return json.dumps(
            find_common_available_slots_dict(
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
            ),
            ensure_ascii=False,
        )


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

    # TODO: Kana agent가 고른 최종 시간 정보를 course repo JSON 계약에 맞춰 기록하세요.
    #   - 직접 최종 시간을 고르지 말고 받은 인자를 그대로 decide_final_slot_payload(...)에 넘깁니다.
    #   - 결과를 JSON 문자열로 반환합니다.
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

    # TODO: Week 4 도구를 가진 Nana 하위 agent를 실행하고 answer/trace/inner_tool_names를 반환하세요.
    #   - _NANA_SUBAGENT가 None일 때만 create_agent(model=chat_model(), tools=week04_tools(),
    #     system_prompt=nana_system_prompt())로 만들고 이후에는 재사용합니다.
    #   - query를 user 메시지로 invoke하고, extract_agent_events(...)와 extract_final_text(...)로
    #     trace와 answer를 뽑습니다.
    #   - selected_agent, answer, trace, inner_tool_names를 담은 JSON 문자열을 반환합니다.
    global _NANA_SUBAGENT
    if _NANA_SUBAGENT is None:
        _NANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=week04_tools(),
            system_prompt=nana_system_prompt(),
        )
    nana = _NANA_SUBAGENT
    result = nana.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)
    return json.dumps(
        {
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

    # TODO: Kana 하위 agent를 실행하고 trace에서 final_slot_payload/final_decision_payload를 끌어올려 반환하세요.
    #   - _KANA_SUBAGENT를 kana_tools()와 kana_system_prompt()로 한 번만 만들고 재사용합니다.
    #   - trace event의 content를 훑어 final_slot이 들어 있는 dict와 final_decision 값을 찾습니다.
    #   - answer, trace, inner_tool_names, final_slot_payload, final_decision_payload를 JSON으로 반환합니다.
    global _KANA_SUBAGENT
    if _KANA_SUBAGENT is None:
        _KANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=kana_tools(),
            system_prompt=kana_system_prompt(),
        )
    kana = _KANA_SUBAGENT
    result = kana.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)

    final_slot_payload = None
    final_decision_payload = None
    for event in events:
        content = event.get("content")
        if isinstance(content, dict):
            if "final_slot" in content:
                final_slot_payload = content
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
