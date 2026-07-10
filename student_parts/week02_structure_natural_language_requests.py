from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field

from fixed.config import CONFIG
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from student_parts.week01_wake_up_nana import join_system_prompt, week01_prompt_parts, week01_tools


RequestKind = Literal["personal_schedule", "group_schedule", "todo", "reminder", "unknown"]
_WEEK02_AGENT: Any | None = None


# [2주차 수강생 구현 가이드]
#
# 목표
#   Week 2의 핵심은 사용자의 한국어 자연어 요청이나 Week 1 tool이 만든 JSON payload를
#   일정 앱이 읽을 수 있는 StructuredRequest/StructuredRequestBatch로 바꾸는 것입니다.
#   Week 1이 이미 정해진 인자를 받아 임시 일정을 만들었다면, Week 2는 "내일 오후 3시" 같은
#   자연어와 created_schedule JSON을 날짜/시간/종류/멤버 필드로 구조화합니다.
#   구조화 결과는 아직 SQLite, RAG, 외부 멤버 일정 조율 흐름에 저장하지 않습니다.
#
# 과제 구성
#   - 메인과제: Week 2 agent가 자연어 또는 Week 1 tool JSON을 StructuredRequestBatch로
#     최종 반환하는 세로 슬라이스를 완성합니다.
#   - 추가 과제: 메인과제에서 만든 StructuredRequest 스키마를 Week 3 이상 저장/조율 흐름에서
#     재사용할 수 있도록 bridge 함수를 완성합니다.
#
# 구현 위치와 사용할 코드
#   - 이 파일(student_parts/week02_structure_natural_language_requests.py)의
#     StructuredRequest, StructuredRequestBatch, week02_tools(), week02_prompt_parts(),
#     week02_system_prompt(), build_week02_agent()를 확인합니다.
#   - build_week02_agent()는 langchain.agents.create_agent, fixed/llm.py의 chat_model(),
#     week02_system_prompt(), response_format=StructuredRequestBatch를 사용해 Week 2 agent를 만듭니다.
#   - week02_tools()는 Week 1 도구 목록을 그대로 가져옵니다. Week 2 agent는 개인 일정 생성 요청에서
#     personal_create_schedule이 반환한 created_schedule JSON payload를 읽고
#     response_format=StructuredRequestBatch로 최종 구조화 결과를 확인합니다.
#   - week02_prompt_parts()는 student_parts/week01_wake_up_nana.py의 week01_prompt_parts() 위에
#     Week 2 구조화 지시를 추가합니다.
#   - _coerce_structured_request(), extract_structured_request(), extract_schedule_request()는
#     Week 3 이상에서 재사용되는 구조화 bridge입니다. Week 2 파일에 있지만 Week 2 agent에
#     공개되는 tool은 아닙니다.
#
# 메인과제 구현 대상
#   1. StructuredRequest 스키마
#      - kind/title/date/start_time/end_time/members/priority/reason/original_text 필드가
#        이후 Week 3 저장 payload의 기준이 됩니다.
#      - kind는 RequestKind Literal에 들어 있는 값만 허용합니다.
#      - 각 필드에는 LLM structured output이 이해할 수 있도록 한국어 description을 붙입니다.
#
#   2. StructuredRequestBatch 스키마
#      - requests에는 StructuredRequest 목록을 담고, 요청이 하나뿐이어도 list 형태를 유지합니다.
#      - base_date에는 상대 날짜 해석 기준일(current_app_date_iso)을 담습니다.
#
#   3. Week 2 agent 세로 슬라이스
#      - week02_tools()는 Week 1 tool 목록을 그대로 반환합니다.
#      - week02_prompt_parts()와 week02_system_prompt()에는 자연어/Week 1 tool JSON을
#        StructuredRequestBatch로 구조화하라는 지시를 넣습니다.
#      - build_week02_agent()에 response_format=StructuredRequestBatch를 연결해
#        ./run.sh --week2가 동작하게 합니다.
#      - 개인 일정 생성 요청에서는 Week 1 personal_create_schedule tool 결과의 created_schedule JSON을
#        LLM이 읽어 StructuredRequestBatch로 최종 변환하는 흐름을 확인합니다.
#
# 추가 과제 구현 대상
#   1. _coerce_structured_request
#      - LangChain structured output 결과가 이미 StructuredRequest이면 그대로 반환합니다.
#      - dict이면 StructuredRequest.model_validate(...)로 검증해 반환합니다.
#      - 예상한 형태가 아니면 RuntimeError를 발생시켜 잘못된 LLM 응답을 조용히 통과시키지 않습니다.
#
#   2. extract_structured_request
#      - chat_model().with_structured_output(StructuredRequest, method="function_calling")를 사용합니다.
#      - system 메시지에는 join_system_prompt(week02_prompt_parts())를 넣고,
#        user 메시지에는 text를 넣어 structured LLM을 호출합니다.
#      - 자연어 또는 JSON 문자열을 StructuredRequest 하나로 검증/구조화합니다.
#
#   3. extract_schedule_request
#      - extract_structured_request(query) 결과에 ok/tool_name/base_date를 붙입니다.
#      - structured_request에는 model_dump() 결과를 넣고, json.dumps(..., ensure_ascii=False)로 반환합니다.
#      - Week 3 이상 저장 tool이 structured_request 필드를 그대로 받을 수 있게 만듭니다.
#
# StructuredRequest 읽는 법
#   - kind: personal_schedule, group_schedule, todo, reminder, unknown 중 하나입니다.
#   - title/date/start_time/end_time: 일정 앱이 실제 저장이나 생성에 사용할 핵심 필드입니다.
#   - members: 참석자/관련 멤버 list입니다. 모르면 빈 list로 둡니다.
#   - priority/reason/original_text: 할 일 우선순위, 판단 근거, 원문 보존용 필드입니다.
#   - 모르는 값을 억지로 만들지 않는 것이 중요합니다. 확실하지 않으면 None 또는 빈 list가 안전합니다.
#   - date/start_time/end_time은 확실할 때만 YYYY-MM-DD, HH:MM 형식으로 채웁니다.
#
# bridge 동작 기준
#   - 요청이 하나뿐이어도 Week 2 agent의 structured_response에는 StructuredRequest 하나를 담습니다.
#   - 여러 일정/할 일/알림 의도가 한 문장에 섞이면 Week 2 agent에서는 여러 StructuredRequest로 나눕니다.
#   - extract_structured_request()는 bridge 용도라 StructuredRequest 하나만 반환합니다.
#   - Week 1 personal_create_schedule은 이미 분해된 인자로 임시 일정을 생성하고,
#     Week 2 agent와 bridge는 그 JSON payload를 읽어 저장 가능한 구조로 최종 변환한다는 차이를 비교합니다.
#
# 참고 코드
#   - week01_prompt_parts()
#      Week 1 system prompt를 이어받아 Week 2 구조화 지시를 누적할 때 사용합니다.
#   - week01_tools()
#      Week 1 개인 일정 tool 목록입니다. Week 2 agent는 이 tool 결과 JSON을 구조화 근거로 씁니다.
#   - extract_structured_request / extract_schedule_request
#      Week 3 이상에서 DB 저장/조율 tool chain에 쓰는 bridge 코드입니다.
#      query 문자열이 자연어든 Week 1 tool JSON이든, Python rule/parser로 매핑하지 않고
#      structured LLM 호출로 구조화한 뒤 JSON tool payload로 감쌉니다.
#
# 검증 방법
#   - 메인과제: ./run.sh --week2로 실행한 뒤 "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘" 같은
#     문장을 입력합니다. 최종 답변이 StructuredRequestBatch class 형식의 structured_response로
#     나오는지 확인합니다.
#   - 추가 과제: Week 3을 실행한 뒤 trace에서 extract_schedule_request 이후
#     save_structured_request가 호출되는지 봅니다. extract_schedule_request의 반환 JSON에
#     ok/tool_name/base_date/structured_request가 들어 있는지 확인합니다.
#
# 함수별 동작 설명
#   - StructuredRequest
#     Week 2 structured output의 중심 스키마입니다. LLM이 자연어에서 뽑은 요청 종류, 제목, 날짜, 시간,
#     멤버, 우선순위, 근거, 원문을 이 class 필드에 맞춰 반환합니다.
#
#   - StructuredRequestBatch
#     StructuredRequest 여러 개와 base_date를 함께 담는 최종 structured_response 스키마입니다.
#     요청이 하나뿐이어도 requests list 안에 StructuredRequest 하나를 담습니다.
#
#   - week02_tools()
#     Week 1 개인 일정 tool을 그대로 노출합니다. Week 2 agent는 개인 일정 생성 요청에서
#     created_schedule JSON을 structured_response의 근거로 사용할 수 있습니다.
#
#   - week02_system_prompt() / week02_prompt_parts()
#     Week 1 prompt 위에 "자연어를 StructuredRequestBatch로 출력한다"는 Week 2 지시를 누적합니다.
#
#   - build_week02_agent() / build_week_agent()
#     response_format=StructuredRequestBatch가 설정된 agent를 만들고 재사용합니다.
#     build_week_agent()는 실행기가 찾는 표준 entry point입니다.
#
#   - _coerce_structured_request(value)
#     LangChain structured output 결과가 이미 StructuredRequest이면 그대로 쓰고, dict이면 Pydantic 검증을 거쳐
#     StructuredRequest로 바꿉니다. 예상한 형태가 아니면 오류를 내서 잘못된 LLM 응답을 조용히 통과시키지 않습니다.
#
#   - extract_structured_request(text)
#     agent loop를 새로 만들지 않고 chat_model().with_structured_output(...)만 사용해 자연어 또는 JSON 문자열을
#     StructuredRequest로 검증/구조화합니다. Week 3 이상에서 저장/조율 직전 입력을 구조화해야 할 때 재사용하는 bridge 함수입니다.
#
#   - extract_schedule_request(query)
#     Week 3 이상 agent가 저장/조율 전에 호출하는 LangChain bridge tool입니다.
#     extract_structured_request(...) 결과에 ok/tool_name/base_date를 붙여 JSON 문자열로 반환하므로,
#     이후 저장 tool이 structured_request 필드를 그대로 받을 수 있습니다.


class StructuredRequest(BaseModel):
    """사용자 자연어 요청(또는 Week 1 tool의 created_schedule) 하나를 구조화한 2주차 스키마입니다.

    kind로 요청 종류를 분류하고 title/date/start_time/end_time/members 등 세부 필드를 채웁니다.
    확실하지 않은 값은 None이나 빈 리스트로 두고 임의로 지어내지 않습니다.
    """

    kind: RequestKind = Field(
        description="요청 종류: personal_schedule/group_schedule/todo/reminder/unknown 중 하나"
    )
    title: str | None = Field(default=None, description="일정/할 일 제목, 모르면 None")
    date: str | None = Field(default=None, description="확실할 때만 YYYY-MM-DD, 모르면 None")
    start_time: str | None = Field(default=None, description="확실할 때만 HH:MM, 모르면 None")
    end_time: str | None = Field(default=None, description="확실할 때만 HH:MM, 모르면 None")
    members: list[str] = Field(
        default_factory=list, description="참석자/관련 멤버 목록, 모르면 빈 리스트"
    )
    priority: str | None = Field(default=None, description="할 일 우선순위, 모르면 None")
    reason: str | None = Field(default=None, description="이 분류/구조화 판단의 근거, 없으면 None")
    original_text: str = Field(default="", description="구조화 근거가 된 사용자 원문")


class StructuredRequestBatch(BaseModel):
    """한 요청에서 추출한 StructuredRequest들을 담는 최종 structured_response 스키마입니다.

    요청이 하나뿐이어도 requests 리스트에 StructuredRequest 하나를 담고,
    base_date에는 상대 날짜 해석 기준일(오늘)을 둡니다.
    """

    requests: list[StructuredRequest] = Field(
        default_factory=list,
        description="구조화된 요청 목록, 요청이 하나여도 리스트로 담는다",
    )
    base_date: str = Field(
        default_factory=current_app_date_iso, description="상대 날짜 해석 기준일(오늘)"
    )


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """LangChain structured output 결과를 StructuredRequest로 정규화합니다."""

    # TODO: value가 이미 StructuredRequest이면 그대로 반환하세요.
    # TODO: value가 dict이면 StructuredRequest.model_validate(...)로 검증해 반환하세요.
    # TODO: 예상한 형태가 아니면 RuntimeError를 발생시켜 잘못된 LLM 응답을 조용히 통과시키지 마세요.
    ...


def extract_structured_request(text: str) -> StructuredRequest:
    """Week 3 이상에서 agent를 새로 띄우지 않고 자연어를 StructuredRequest로 바꿉니다."""

    # TODO: chat_model().with_structured_output(StructuredRequest, method="function_calling")로 structured LLM을 만드세요.
    # TODO: system 메시지에는 join_system_prompt(week02_prompt_parts())를 넣고, user 메시지에는 text를 넣어 invoke하세요.
    # TODO: LLM 결과를 _coerce_structured_request(...)로 정규화해 StructuredRequest 하나로 반환하세요.
    ...


@tool
def extract_schedule_request(query: str) -> str:
    """Week 3 이상 agent가 저장/조율 전에 호출하는 구조화 bridge tool입니다."""

    # TODO: extract_structured_request(query)를 호출해 자연어 또는 Week 1 JSON payload를 구조화하세요.
    # TODO: ok/tool_name/base_date/structured_request 키를 가진 dict를 만들고 structured_request에는 model_dump() 결과를 넣으세요.
    # TODO: json.dumps(..., ensure_ascii=False)로 JSON 문자열을 반환하세요.
    ...


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""

    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    structured_response_rule = (
        "## 최종 출력 (가장 강한 제약)\n"
        "- 최종 답변은 반드시 StructuredRequestBatch 형식의 structured_response로 낸다.\n"
        "- structured_response는 정확히 하나의 JSON 객체로만 낸다. "
        "그 앞뒤에 설명 문장이나 두 번째 JSON을 덧붙이지 않는다.\n"
        "- requests를 빈 리스트로 두지 않는다. 요청이 하나뿐이어도 StructuredRequest 하나를 담는다.\n"
        "- personal_create_schedule 결과 JSON의 created_schedule을 읽어 필드를 채운다.\n"
        "- 정보가 부족해도 사용자에게 되묻지 않는다. 모르는 필드는 null 또는 빈 리스트로 둔다. "
        "단, 삭제 대상이 특정되지 않으면 삭제 tool을 호출하지 않는다."
    )
    return join_system_prompt([*week02_prompt_parts(), structured_response_rule])


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    role_part = (
        "## 역할\n"
        "너는 Nana의 한국어 자연어 요청과 Week 1 tool 결과 JSON을 "
        "StructuredRequestBatch로 구조화하는 Week 2 agent다.\n"
        f"오늘 날짜는 {current_app_date_iso()}이며, 모든 상대 날짜는 이 날짜를 기준으로 해석한다."
    )

    core_rules_part = (
        "## 핵심 규칙\n"
        "- kind는 personal_schedule, group_schedule, todo, reminder, unknown "
        "다섯 값 중 하나로만 채운다. 그 밖의 값은 만들지 않는다.\n"
        "- 모르는 값은 지어내지 않는다. 확실하지 않으면 null로 두고, "
        "members 같은 목록 필드는 빈 리스트로 둔다.\n"
        "- date는 확실할 때만 YYYY-MM-DD, start_time과 end_time은 확실할 때만 HH:MM으로 채운다. "
        "원문에 시각이 없으면 start_time과 end_time은 null로 둔다. 00:00 같은 기본값을 채워 넣지 않는다.\n"
        "- Nana 본인 캘린더에 잡히는 일정은 참석자가 있어도 personal_schedule이고, "
        "팀 전체가 함께 시점을 정해야 하는 모임은 group_schedule이다.\n"
        "- 마감이나 기한이 있는 해야 할 일은 todo, 특정 시점에 알려 달라는 요청은 reminder다.\n"
        "- base_date에는 위에 제시된 오늘 날짜를 그대로 넣는다."
    )

    cot_part = (
        "## 구조화 절차\n"
        "아래 순서대로 판단하고, 근거가 없는 필드는 null(목록은 빈 리스트)로 둔 채 다음 단계로 넘어간다.\n"
        "1. kind 분류: 개인 일정·그룹 일정·할 일·리마인더 중 무엇인지 정한다. 판단할 근거가 없으면 unknown.\n"
        "2. date 정규화: '내일', '다음 주 화요일' 같은 상대 표현을 오늘 날짜 기준 YYYY-MM-DD로 바꾼다. "
        "'조만간'처럼 확정할 근거가 없으면 null.\n"
        "3. 시간 정규화: '오후 3시'는 15:00처럼 HH:MM으로 바꾼다.\n"
        "4. members 추출: 원문 또는 tool 결과에 실제로 등장한 사람만 넣는다.\n"
        "5. original_text에는 구조화 근거가 된 원문을 그대로 넣고, title·priority·reason은 확실할 때만 채운다."
    )

    requests_part = (
        "## requests 구성\n"
        "- 일정·할 일·리마인더를 새로 잡아달라는 요청이면 그 요청 자체를 StructuredRequest 하나로 담는다. "
        "한 문장에 요청이 여러 개면 요청 수만큼 담는다.\n"
        "- 일정을 보여달라는 조회 요청이면 personal_list_schedules가 돌려준 일정들을 각각 StructuredRequest로 담는다. "
        "조회 결과가 비어 있으면 kind를 unknown으로 둔 StructuredRequest 하나에 원문을 담는다.\n"
        "- 일정 삭제 요청, 그리고 다섯 kind 중 어디에도 맞지 않는 요청은 "
        "kind를 unknown으로 둔 StructuredRequest 하나에 원문을 담는다. "
        "삭제를 실제로 수행했더라도 kind는 unknown이다.\n"
        "- 어떤 경우에도 requests를 빈 리스트로 두지 않는다."
    )

    tool_result_part = (
        "## Week 1 tool 사용과 결과 처리\n"
        "- Week 2에서도 Week 1의 tool 호출 규칙을 그대로 지킨다. 일정을 새로 잡아달라는 요청이면 "
        "personal_create_schedule, 일정을 보여달라는 요청이면 personal_list_schedules, "
        "일정을 지워달라는 요청이면 personal_delete_schedule을 호출한다.\n"
        "- 구조화만 하면 된다는 이유로 tool 호출을 생략하지 않는다. tool 호출과 structured_response는 함께 수행한다.\n"
        "- 삭제 요청에서 schedule_id·제목·날짜 중 하나로 일정이 지목되면 특정된 것으로 본다"
        "(예: '치과 진료 일정 지워줘'는 제목으로 특정됨). schedule_id를 모르면 personal_list_schedules로 "
        "먼저 id를 찾은 뒤 personal_delete_schedule을 호출한다.\n"
        "- 삭제 안전장치: '그 일정', '아까 그거'처럼 아무것도 지목하지 못한 경우에만 "
        "personal_delete_schedule을 호출하지 않고 구조화만 한다. 여러 일정을 임의로 삭제하지 않는다.\n"
        "- 같은 tool을 반복 호출하지 않는다. 받은 tool JSON의 payload를 읽어 structured_response로 만든다.\n"
        "- created_schedule의 attendees는 members로 옮기고, "
        "end_time이 '미정'처럼 HH:MM 형식이 아니면 null로 둔다."
    )

    scope_part = (
        "## 범위 제한\n"
        "Week 2에서는 SQLite 저장, RAG, 외부 멤버 일정 조율을 하지 않는다. "
        "이는 Week 1 tool 호출을 막는 규칙이 아니다."
    )

    few_shot_part = "\n".join([
        "## 예시",
        "아래 날짜는 예시용 가정이며 실제 변환은 위에 제시된 오늘 날짜를 기준으로 한다.",
        "예시의 2026-05-11, 2026-05-19를 실제 출력에 그대로 복사하지 않는다.",
        "reason도 예시 문구를 베끼지 말고, 실제 입력에 근거해 직접 짧게 쓴다.",
        "",
        "### 예시 1 (정보가 충분한 경우) — 가정: 오늘이 2026-05-11(월)",
        '입력: "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"',
        "기대 출력:",
        "{",
        '  "requests": [',
        "    {",
        '      "kind": "personal_schedule",',
        '      "title": "철수와 회의",',
        '      "date": "2026-05-19",',
        '      "start_time": "15:00",',
        '      "end_time": null,',
        '      "members": ["철수"],',
        '      "priority": null,',
        '      "reason": "상대 날짜와 시간을 오늘 기준으로 환산. 종료 시각 미언급.",',
        '      "original_text": "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"',
        "    }",
        "  ],",
        '  "base_date": "2026-05-11"',
        "}",
        "",
        "### 예시 2 (정보가 불충분한 경우) — 가정: 오늘이 2026-05-11(월)",
        '입력: "조만간 팀 회고 한번 하자"',
        "기대 출력:",
        "{",
        '  "requests": [',
        "    {",
        '      "kind": "group_schedule",',
        '      "title": "팀 회고",',
        '      "date": null,',
        '      "start_time": null,',
        '      "end_time": null,',
        '      "members": [],',
        '      "priority": null,',
        '      "reason": "\'조만간\'은 날짜 확정 불가, 참석자 미언급.",',
        '      "original_text": "조만간 팀 회고 한번 하자"',
        "    }",
        "  ],",
        '  "base_date": "2026-05-11"',
        "}",
    ])

    return [
        *week01_prompt_parts(),
        role_part,
        core_rules_part,
        cot_part,
        requests_part,
        tool_result_part,
        scope_part,
        few_shot_part,
    ]


def build_week02_agent() -> object:
    """Week 2 대화에서 structured_response를 직접 반환하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK02_AGENT
    if _WEEK02_AGENT is None:
        _WEEK02_AGENT = create_agent(
            model=chat_model(),
            tools=week02_tools(),
            response_format=StructuredRequestBatch,
            system_prompt=week02_system_prompt(),
        )
    return _WEEK02_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()
