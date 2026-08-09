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

    # 번호는 5주차의 ⑱에 이어 ⑲부터 쓴다.
    return [
        *week05_prompt_parts(),
        (
            "너는 6주차부터 카나메이트의 supervisor다. 앞선 주차 안내에 나오는 개별 업무 tool은 이제 네가 "
            "가지고 있지 않다. 너에게 있는 tool은 nana_agent와 kana_agent 둘뿐이고 실제 작업은 두 하위 "
            "에이전트가 한다. "
            "⑲ Nana 담당은 내 개인 일정 조회·생성·수정·삭제, todo와 reminder 저장, 개인 참고자료와 "
            "앱에 저장된 내 지난 대화 검색이다. "
            "⑳ Kana 담당은 다른 멤버의 지난 대화와 일정, 공유 일정 조회, 여러 사람이 함께 가능한 시간 "
            "찾기와 최종 회의 시간 결정이다. "
            "㉑ 나 말고 다른 사람이 등장하거나 여러 사람의 시간을 맞춰야 하면 Kana, 나에 관한 것만이면 "
            "Nana에게 위임한다. "
            "㉒ Kana에는 일정을 저장하는 tool이 없다. 사용자가 저장·등록·확정을 함께 요청한 경우에만 "
            "kana_agent 결과를 받은 뒤 nana_agent를 한 번 더 호출해 확정된 날짜·시간·참석자를 query에 적어 "
            "넘긴다. 시간을 찾아 달라고만 한 요청은 저장하지 말고 Kana가 고른 시간과 근거를 그대로 전달한다. "
            "저장은 기록을 남기는 동작이므로 요청하지 않은 저장을 먼저 하지 않는다."
        ),
    ]


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    # 번호는 4주차의 ①~⑧에 이어 ⑨부터 쓴다. Nana는 5주차 조각을 누적하지 않아 겹치지 않는다.
    return [
        *week04_prompt_parts(),
        (
            "너는 6주차부터 supervisor가 넘긴 요청 하나를 처리하는 개인 담당 하위 에이전트 Nana다. "
            "사용자와 직접 대화하지 않고, 받은 요청 범위 안에서 처리한 결과와 근거를 돌려준다. "
            "⑨ 여러 사람이 함께 가능한 시간을 찾거나 회의 시간을 조율하는 일은 Kana 담당이다. "
            "아직 시간이 정해지지 않은 조율 요청은 처리하려 하지 말고 Kana 담당이라고 짧게 알린다. "
            "이때 일정을 만들거나 저장하지 않는다. 시간이 이미 정해진 일정을 저장하는 것은 네 담당이다."
        ),
    ]


def kana_prompt_parts() -> list[str]:
    """Week 6 Kana 하위 에이전트 전용 system prompt 조각입니다."""

    today = current_app_date_iso()

    # 다른 주차 조각을 누적하지 않으므로 오늘 날짜와 tool 사용 규칙을 여기서 직접 준다.
    return [
        (
            f"너는 카나메이트의 그룹 조율 담당 Kana다. 오늘은 {today}이다. "
            "너는 사용자와 직접 대화하지 않고 supervisor가 넘긴 요청 하나를 처리해 결과와 근거를 돌려주는 "
            "하위 에이전트다. 받은 요청 범위 안에서만 답하고, 판단 근거를 답변에 함께 적는다. "
            "① 네 담당은 다른 멤버의 지난 대화와 일정, 공유 일정 조회, 여러 사람의 공통 가능 시간 후보, "
            "그리고 최종 회의 시간 결정이다. 개인 일정을 만들거나 고치거나 삭제하는 일, 확정된 일정을 "
            "저장하는 일, 개인 참고자료와 앱 대화 검색은 Nana 담당이라 너에게는 tool이 없다. "
            "그런 요청을 받으면 처리하려 하지 말고 Nana 담당이라고 밝힌다. "
            "② 다른 멤버의 대화와 일정은 외부 MCP 서버에 있어서 tool을 호출해야만 볼 수 있다. "
            "호출하지 않고 아는 것처럼 답하지 않는다. "
            "③ 여러 사람이 함께 가능한 시간을 다루는 요청은 collect_member_schedules → "
            "find_common_available_slots → decide_final_slot을 이 순서로 모두 호출해 마무리한다. "
            "'잡아줘'처럼 지시한 경우뿐 아니라 '언제 가능해?', '시간 있을까?'처럼 물어본 경우도 같다. "
            "중간에서 멈추고 답하지 않는다. "
            "요청 문장을 해석하려고 다른 tool을 먼저 부르지 말고 collect_member_schedules부터 호출한다. "
            "④ collect_member_schedules의 member_names에는 외부 멤버 이름만 넣는다. 내 일정은 자동으로 "
            "포함되므로 '나'를 넣지 않는다. 반환된 rows에는 나와 외부 멤버가 같은 형태로 들어 있고, "
            "내 일정도 겹치면 안 되는 시간이므로 후보를 고를 때 함께 본다. "
            "⑤ find_common_available_slots는 후보를 대신 계산해 주지 않는다. collect_member_schedules 결과를 "
            "받으면 rows를 읽고 아무도 바쁘지 않은 시간대를 먼저 직접 고른 다음, 그 후보를 candidate_slots에 "
            "담고 busy_rows도 그대로 복사해서 find_common_available_slots를 한 번에 부른다. "
            "candidate_slots를 비운 채로 먼저 불러 보지 않는다. rows가 0건이면 그 구간에 바쁜 사람이 없다는 "
            "뜻이므로, 가능한 시간이 없다고 답하지 말고 업무 시간 안에서 후보를 직접 만들어 넘긴다. "
            "돌아온 candidate_slots가 비어 있으면 다른 시간대로 후보를 다시 골라 호출하고, "
            "그래도 없으면 가능한 시간을 찾지 못했다고 답한다. 후보를 지어내지 않는다. "
            "⑥ 조회 구간은 사용자가 말한 날짜를 그대로 쓰고, 상대 표현이면 오늘 기준으로 환산해 어떤 구간을 "
            "확인했는지 답변에 적는다. 날짜를 알 수 없으면 추측하지 말고 무엇이 필요한지 밝힌다. "
            "⑦ 특정 대화의 원문이 필요하면 search_previous_conversations로 conversation_id를 찾은 뒤 그 id로 "
            "load_conversation_messages를 부른다. conversation_id를 지어내지 않는다. "
            "⑧ 지난 대화에서 누군가 한 말은 그 시점의 진술이지 확정된 일정이 아니다. 확정 여부를 확인해야 하면 "
            "list_shared_schedules로 공유 저장소를 조회한 뒤 답하고, 근거가 어느 출처에서 나왔는지 적는다."
        ),
    ]


def nana_system_prompt() -> str:
    return join_system_prompt(nana_prompt_parts())


def kana_system_prompt() -> str:
    return join_system_prompt(kana_prompt_parts())


def supervisor_system_prompt() -> str:
    # 이 조각은 반드시 맨 뒤에 둔다. 누적된 앞 주차 안내에는 supervisor가 갖고 있지 않은 tool 이름이
    # 그대로 남아 있는데, join_system_prompt 헤더가 "뒤에 있는 지시를 우선한다"고 알려 주기 때문이다.
    return join_system_prompt(
        [
            *week06_prompt_parts(),
            (
                "㉓ 일정·기록·검색과 관련된 요청에는 nana_agent 또는 kana_agent를 반드시 호출하고, "
                "돌려받은 결과만 근거로 답한다. 위임하지 않은 채 아는 것처럼 답하거나 하위 에이전트가 주지 "
                "않은 내용을 지어내지 않는다. "
                "앞선 주차 안내에 개별 업무 tool 이름이 나오더라도 너는 그 tool을 부를 수 없으므로 담당 "
                "하위 에이전트에게 위임한다. "
                "query에는 사용자 문장의 날짜·이름·조건을 빼먹지 말고 그대로 옮겨 넘긴다. 요약해서 넘기면 "
                "하위 에이전트가 조회 범위를 알 수 없다. "
                "하위 에이전트는 필요한 자료를 스스로 조회하므로 한쪽 결과를 다른 쪽 query에 옮겨 담지 "
                "않는다. 다만 ㉒처럼 확정된 일정을 저장하도록 위임할 때는 날짜·시간·참석자를 적어 넘긴다. "
                "인사나 잡담처럼 업무가 아닌 말에는 위임하지 않고 짧게 답한다."
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


# 하위 agent의 tool 결과 하나가 이 길이를 넘으면 잘라서 올린다. 대화 RAG는 원문 passage를 담아
# 한 번에 18KB를 넘겼는데, 그 전체가 supervisor의 tool 결과로 들어가 매 턴 다시 전송된다.
# supervisor가 답변에 쓰는 것은 하위 agent의 answer이므로 원문까지 올릴 이유가 없다.
DELEGATE_TRACE_CONTENT_LIMIT = 1200


def _trim_trace_field(event: dict[str, Any], field: str) -> dict[str, Any]:
    """event의 한 필드가 한도를 넘으면 잘라내고 얼마나 잘렸는지 남긴다."""

    if event.get(field) is None:
        return event
    text = json.dumps(event[field], ensure_ascii=False)
    if len(text) <= DELEGATE_TRACE_CONTENT_LIMIT:
        return event
    return {
        **event,
        field: text[:DELEGATE_TRACE_CONTENT_LIMIT],
        f"{field}_truncated_chars": len(text) - DELEGATE_TRACE_CONTENT_LIMIT,
    }


def _delegate_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """supervisor에게 올릴 하위 trace를 만든다. 큰 tool 결과와 인자는 잘라내고 잘렸다는 사실을 남긴다.

    tool 결과뿐 아니라 인자도 자른다. agent가 busy_rows와 candidate_slots를 인자에 그대로 복사해
    넘기기 때문에, 멤버나 일정이 늘어나면 인자 쪽이 결과만큼 커진다.
    """

    return [_trim_trace_field(_trim_trace_field(event, "content"), "arguments") for event in events]


FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION = (
    "네가 직접 고른 공통 가능 시간 후보가 정말로 아무도 바쁘지 않은 시간인지 검증한다. "
    "이 tool은 후보를 대신 계산해 주지 않는다. 앞선 일정 조회 결과의 busy_rows를 네가 읽고 "
    "비어 있는 시간대를 직접 골라 candidate_slots에 채워 넘겨야 한다. "
    "candidate_slots의 각 항목은 date('YYYY-MM-DD'), start_time('HH:MM'), end_time('HH:MM'), "
    "duration_minutes, reason을 모두 포함한다. "
    "후보는 어떤 busy row와도 겹치면 안 된다. 판단 근거가 남도록 busy_rows도 앞선 tool 결과에서 "
    "그대로 복사해 함께 넘긴다. busy_rows를 넘기지 않으면 이 tool이 일정을 다시 수집한다. "
    "busy_rows가 비어 있으면 아무도 바쁘지 않다는 뜻이므로 업무 시간 안에서 후보를 직접 만들어 넘긴다. "
    "candidate_slots 없이 부르면 검증할 후보가 없어 결과가 비고, 가능한 시간이 없다고 잘못 답하게 된다. "
    "겹치거나 업무 시간을 벗어나거나 요청한 회의 길이보다 짧은 후보는 결과에서 조용히 빠지므로, "
    "돌아온 candidate_slots가 비어 있으면 다른 시간대로 후보를 다시 골라 호출한다. "
    "이 결과로 답변을 끝내지 말고 이어서 decide_final_slot을 호출해 최종 시간을 확정한다."
)


DECIDE_FINAL_SLOT_DESCRIPTION = (
    "find_common_available_slots가 검증한 후보 중에서 네가 직접 고른 최종 회의 시간을 기록한다. "
    "이 tool은 후보를 비교하지 않고 최종 시간을 대신 고르지도 않는다. 네가 고른 결과를 받아 적기만 한다. "
    "고른 후보는 selected_index(candidate_slots의 0부터 시작하는 번호) 또는 selected_slot(후보 객체)으로 지목한다. "
    "확정했으면 final_slot에 'YYYY-MM-DD HH:MM-HH:MM' 형식으로 쓰고 needs_agent_selection은 false로 둔다. "
    "아직 고르지 못했으면 final_slot은 null, needs_agent_selection은 true로 두고 왜 확정하지 못했는지 reason에 쓴다. "
    "reason에는 그 시간을 고른 근거를 사용자가 그대로 읽을 수 있는 문장으로 쓴다. "
    "판단 근거를 남기려면 candidate_slots, busy_rows, member_names, date_from, date_to도 "
    "앞선 tool 결과에서 복사해 함께 넘긴다. "
    "후보를 만들고 겹침을 검증하는 일은 find_common_available_slots가 맡는다. 이 tool은 그 다음 단계다."
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

    # 정규화는 외부에 보낼 인자를 직접 조립하는 이 함수에서 한다. tool wrapper는 받은 값을 전달만 한다.
    # collect_member_schedules는 "나"를 받지 않는다(내 일정을 스스로 넣는다). LLM이 넣어 보내도 걸러낸다.
    external_members = [
        name for name in normalize_external_member_names(member_names) if name != PERSONAL_SHARED_MEMBER_NAME
    ]
    window_from = normalize_date_bound(date_from)
    window_to = normalize_date_bound(date_to)

    # busy_rows는 Kana가 앞선 tool 결과에서 복사해 넘기는 것이 정상 경로다. 빠뜨렸을 때만 직접 수집한다.
    rows = busy_rows
    if rows is None:
        collected = json.loads(
            collect_member_schedules.invoke(
                {"member_names": external_members, "date_from": window_from, "date_to": window_to}
            )
        )
        rows = collected.get("rows") or []

    # 겹침 검증은 날짜를 문자열 완전 일치로 비교하고 후보 쪽만 정규화한다. busy_rows는 LLM이
    # 복사해 넘기는 값이라 ISO datetime으로 옮겨 적히면 그 일정이 통째로 안 걸린다.
    # 못 걸린 일정은 오류가 아니라 "빈 시간"으로 보이므로, 이미 회의가 있는 시간을 추천하게 된다.
    rows = [{**row, "date": normalize_date_bound(str(row.get("date") or ""))} for row in rows]

    payload = find_common_available_slots_payload(
        # 겹침 판단 근거에 내 일정도 들어가므로 기록에는 "나"를 앞에 둔다. 중복은 위에서 이미 걸러냈다.
        member_names=[PERSONAL_SHARED_MEMBER_NAME, *external_members],
        date_from=window_from,
        date_to=window_to,
        busy_rows=rows,
        duration_minutes=duration_minutes,
        workday_start=workday_start,
        workday_end=workday_end,
        limit=limit,
        candidate_slots=candidate_slots,
        llm_reason=llm_reason,
    )

    # 빈 candidate_slots는 "후보가 전부 탈락했다"와 "후보를 아예 받지 못했다" 두 가지 뜻이 된다.
    # 구분해 주지 않으면 agent가 후보를 안 넘기고도 "가능한 시간이 없다"고 반대로 답한다.
    if not (candidate_slots or []):
        payload["needs_agent_candidates"] = True
        payload["message"] = (
            "검증할 후보를 받지 못했습니다. busy_rows와 겹치지 않는 시간대를 직접 골라 "
            "candidate_slots에 담아 다시 호출하세요. busy_rows가 비어 있으면 그 구간에 바쁜 사람이 "
            "없다는 뜻이므로 업무 시간 안에서 후보를 만들면 됩니다."
        )
    return payload


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


def _plain_slot(slot: Any) -> Any:
    """후보 slot이 Pydantic 객체면 dict로 바꾼다.

    decide_final_slot_payload는 후보를 정규화하지 않고 candidates 텍스트와 payload에 그대로 담는다.
    객체가 그대로 들어가면 candidates에 repr이 박히고 json.dumps가 직렬화하지 못한다.
    """

    return slot.model_dump() if hasattr(slot, "model_dump") else slot


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

    # 최종 시간은 agent가 고른 값만 쓴다. 여기서 후보를 비교하거나 고르지 않는다.
    payload = decide_final_slot_payload(
        candidate_slots=[_plain_slot(slot) for slot in candidate_slots or []],
        selected_slot=_plain_slot(selected_slot),
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
    # payload를 감싸지 않는다. extract_langchain_trace가 top-level final_slot으로 이 payload를 알아본다.
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

    # 하위 agent는 새 messages로 시작한다. supervisor 대화 내용은 넘어가지 않고 query만 전달된다.
    result = _NANA_SUBAGENT.invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)
    # supervisor는 하위 agent의 messages를 볼 수 없다. 필요한 근거를 이 payload에 직접 담아 올린다.
    return json.dumps(
        {
            "selected_agent": "nana_agent",
            "answer": extract_final_text(result),
            "trace": _delegate_trace(events),
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

    # 최종 시간은 하위 tool 결과 안에 있는데 supervisor는 그 messages를 볼 수 없다. 꺼내서 올려 준다.
    # 자르기 전 원본에서 찾는다. trace는 부피 때문에 잘리지만 이 값은 온전해야 한다.
    # 후보를 다시 골라 두 번 결정하면 마지막 것이 실제 결론이므로 덮어쓴다.
    final_slot_payload: dict[str, Any] | None = None
    final_decision_payload: dict[str, Any] | None = None
    for event in events:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        # 미확정이면 final_slot이 None이라 .get()으로는 못 찾는다. 그 상태도 supervisor가 알아야 한다.
        if "final_slot" in content:
            final_slot_payload = content
        if content.get("final_decision"):
            final_decision_payload = content["final_decision"]

    return json.dumps(
        {
            "selected_agent": "kana_agent",
            "answer": extract_final_text(result),
            "trace": _delegate_trace(events),
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
