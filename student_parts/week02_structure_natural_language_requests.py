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
    """LLM structured output으로 추출되는 2주차 요청 스키마입니다."""

    kind: RequestKind = Field(
        description=(
            "요청의 종류를 다음 중 하나로 분류한다. "
            "personal_schedule=혼자 하는 개인 일정, "
            "group_schedule=여러 사람이 함께하는 회의·모임 일정, "
            "todo=마감 시각이 꼭 필요하지 않은 할 일, "
            "reminder=특정 시점에 알려 달라는 알림, "
            "unknown=위 어디에도 해당하지 않거나 판단이 어려운 경우."
        )
    )
    title: str | None = Field(
        default=None,
        description=(
            "일정·할 일·알림의 제목이나 핵심 내용. "
            "예: '팀 회의', '우유 사기'. 제목을 특정할 수 없으면 None."
        ),
    )
    date: str | None = Field(
        default=None,
        description=(
            "해당 날짜. '내일'·'다음 주 화요일' 같은 상대 표현은 base_date(현재 날짜)를 기준으로 "
            "계산해 확실할 때만 YYYY-MM-DD 형식으로 채운다. 날짜를 확정할 수 없으면 None."
        ),
    )
    start_time: str | None = Field(
        default=None,
        description=(
            "시작 시각을 HH:MM(24시간) 형식으로 채운다. 예: '오후 3시'는 '15:00'. "
            "시각이 명시되지 않았거나 확실하지 않으면 None."
        ),
    )
    end_time: str | None = Field(
        default=None,
        description=(
            "종료 시각을 HH:MM(24시간) 형식으로 채운다. "
            "종료 시각이 언급되지 않았으면 None."
        ),
    )
    members: list[str] = Field(
        default_factory=list,
        description=(
            "일정·회의에 함께하는 참석자나 관련 인물의 이름 목록. 예: ['철수', '영희']. "
            "언급된 사람이 없으면 빈 목록으로 둔다."
        ),
    )
    priority: str | None = Field(
        default=None,
        description=(
            "할 일·일정의 우선순위(예: '높음', '보통', '낮음'). "
            "언급이 없으면 None."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "이 분류·판단을 내린 근거나 요청의 배경 설명. 필요 없으면 None."
        ),
    )
    original_text: str = Field(
        default="",
        description=(
            "구조화의 근거가 된 사용자 원문(또는 Week 1 도구가 반환한 JSON) 조각을 그대로 보존한다. "
            "기본값은 빈 문자열."
        ),
    )


class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 메인과제 스키마입니다."""

    requests: list[StructuredRequest] = Field(
        default_factory=list,
        description=(
            "사용자 입력에서 뽑아낸 StructuredRequest 목록. 요청이 하나뿐이어도 목록 안에 하나를 담고, "
            "한 문장에 여러 일정·할 일·알림 의도가 섞여 있으면 각 의도를 별도의 StructuredRequest로 나눈다."
        ),
    )
    base_date: str = Field(
        default_factory=current_app_date_iso,
        description=(
            "상대 날짜('내일', '다음 주 화요일' 등)를 해석하는 기준이 되는 오늘 날짜(YYYY-MM-DD). "
            "requests 안의 date 값은 이 기준일로부터 계산한다."
        ),
    )


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """LangChain structured output 결과를 StructuredRequest로 정규화합니다."""

    if isinstance(value, StructuredRequest):
        return value
    if isinstance(value, dict):
        return StructuredRequest.model_validate(value)
    raise RuntimeError(
        "StructuredRequest로 변환할 수 없는 structured output 응답입니다: "
        f"{type(value).__name__} -> {value!r}"
    )


def extract_structured_request(text: str) -> StructuredRequest:
    """Week 3 이상에서 agent를 새로 띄우지 않고 자연어를 StructuredRequest로 바꿉니다."""

    structured_llm = chat_model().with_structured_output(
        StructuredRequest, method="function_calling"
    )
    messages = [
        ("system", join_system_prompt(week02_prompt_parts())),
        ("user", text),
    ]
    return _coerce_structured_request(structured_llm.invoke(messages))


@tool
def extract_schedule_request(query: str) -> str:
    """Week 3 이상 agent가 저장/조율 전에 호출하는 구조화 bridge tool입니다."""

    structured_request = extract_structured_request(query)
    payload = {
        "ok": True,
        "tool_name": "extract_schedule_request",
        "base_date": current_app_date_iso(),
        "structured_request": structured_request.model_dump(),
    }
    return json.dumps(payload, ensure_ascii=False)


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""

    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(
        [
            *week02_prompt_parts(),
            (
                "최종 답변은 반드시 StructuredRequestBatch 형태의 structured_response로 반환한다.\n"
                "- 요청이 하나뿐이어도 requests 목록에 StructuredRequest 하나를 담는다.\n"
                "- 한 문장에 여러 일정·할 일·알림 의도가 섞여 있으면 각 의도를 별도의 StructuredRequest로 분리한다.\n"
                "- personal_create_schedule 등 Week 1 도구 결과 JSON을 받은 경우 그 안의 created_schedule 값을 읽어 각 필드를 채운다.\n"
                "- structured_response는 StructuredRequestBatch 객체 하나만 정확히 한 번 생성하고, 같은 내용을 반복하거나 JSON 뒤에 다른 텍스트를 덧붙이지 않는다."
            ),
        ]
    )


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    return [
        *week01_prompt_parts(),
        (
            "이제 너는 Week 2 요청 구조화 에이전트다. 사용자의 한국어 자연어 요청이나 "
            "Week 1 도구가 만든 일정 JSON을 앱이 저장·처리할 수 있는 구조화된 형태로 변환하는 것이 역할이다.\n"
            f"현재 날짜는 {current_app_date_iso()}이며, 모든 날짜 계산은 이 날짜를 기준으로 한다."
        ),
        (
            "자연어 요청을 StructuredRequest 필드로 구조화한다.\n"
            "- kind: personal_schedule / group_schedule / todo / reminder / unknown 중 하나\n"
            "- title: 일정·할 일·알림의 제목이나 핵심 내용\n"
            "- date: YYYY-MM-DD 형식의 날짜\n"
            "- start_time / end_time: HH:MM(24시간) 형식의 시각\n"
            "- members: 함께하는 참석자·관련 인물 이름 목록\n"
            "- priority: 우선순위, reason: 판단 근거, original_text: 근거가 된 원문"
        ),
        (
            "'내일', '다음 주 화요일', '오후 3시' 같은 상대 표현은 현재 날짜를 기준으로 "
            "YYYY-MM-DD와 HH:MM으로 변환한다. 값이 확실하지 않으면 억지로 지어내지 말고 "
            "None으로 두고, 언급된 멤버가 없으면 members는 빈 목록으로 둔다."
        ),
        (
            "personal_create_schedule 같은 Week 1 도구가 반환한 JSON(created_schedule)을 이미 입력으로 받은 경우에는 "
            "도구를 다시 호출하지 말고, 그 payload의 필드를 그대로 읽어 structured_response로 구조화한다."
        ),
        (
            "Week 2에서는 SQLite 저장, RAG 검색, 외부 멤버 일정 조율을 하지 않는다. "
            "요청을 StructuredRequest로 구조화하는 것까지만 담당한다."
        ),
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
