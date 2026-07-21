from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from pydantic import BaseModel, Field, model_validator

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
    """자연어 한 문장(또는 Week 1 tool이 반환한 JSON)에서 뽑아낸 개별 요청 하나를 표현합니다.
    kind로 요청 종류를 먼저 판단하고 그 근거를 reason에 남긴 뒤 나머지 세부 필드를 채웁니다.
    확실하지 않은 값은 억지로 추론하지 않고 None 또는 빈 list로 남겨도 됩니다."""

    original_text: str = Field(
        default="",
        description="사용자가 입력한 요청 원문(또는 Week 1 tool 결과 JSON)을 그대로 보존한 값입니다.",
    )
    reason: str | None = Field(
        default=None,
        description=(
            "kind 및 다른 필드 값을 그렇게 판단한 근거입니다. kind가 unknown이면 왜 분류할 수 없었는지"
            " 이유를 반드시 남깁니다. date가 None이면, 원문에 날짜 언급 자체가 없었는지 아니면 날짜를"
            " 언급했지만 특정할 수 없었는지를 여기에 남겨 두 경우를 구분할 수 있게 합니다. 그 외에는"
            " 확실하지 않으면 None으로 둡니다."
        ),
    )
    kind: RequestKind = Field(
        default="unknown",
        description=(
            "요청의 종류입니다. personal_schedule/group_schedule/todo/reminder/unknown 중 하나입니다. "
            "4개 분류 중 어디에도 명확히 속하지 않을 때만 unknown을 선택하고, 이때는 reason에 그 이유를 남깁니다."
        ),
    )
    title: str | None = Field(
        default=None,
        description="요청의 제목입니다. 알 수 없으면 None으로 둡니다.",
    )
    date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "요청과 관련된 날짜입니다. YYYY-MM-DD 형식이며, 알 수 없으면 None으로 둡니다. None으로"
            " 두는 경우 원문에 날짜 언급이 아예 없었는지, 아니면 날짜를 언급했지만 특정할 수 없었는지를"
            " reason에 남깁니다(전자면 start_time/end_time이 있을 때 base_date로 채워집니다)."
        ),
        examples=["2026-07-14"],
    )
    start_time: str | None = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}$",
        description="요청 시작 시각입니다. HH:MM 형식이며, 알 수 없으면 None으로 둡니다.",
        examples=["15:00"],
    )
    end_time: str | None = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}$",
        description="요청 종료 시각입니다. HH:MM 형식이며, 알 수 없으면 None으로 둡니다.",
        examples=["16:00"],
    )
    members: list[str] = Field(
        default_factory=list,
        description="요청에 관련된 참석자/멤버 목록입니다. 알 수 없으면 빈 list로 둡니다.",
    )
    priority: Literal["high", "medium", "low"] | None = Field(
        default=None,
        description="요청의 우선순위입니다. 알 수 없으면 None으로 둡니다.",
    )

    @model_validator(mode="after")
    def _validate_time_order(self) -> "StructuredRequest":
        if self.start_time is not None and self.end_time is not None:
            # start_time == end_time은 허용한다: 특정 시각 하나만 의미 있는 요청(알람/리마인더)을
            # 표현할 수 있어야 하므로, "더 빠르면 안 된다"만 막고 "같아도 된다"는 열어둔다.
            if self.end_time < self.start_time:
                raise ValueError("end_time은 start_time보다 빠를 수 없습니다.")
        return self

    @model_validator(mode="after")
    def _validate_unknown_kind_has_reason(self) -> "StructuredRequest":
        # kind="unknown"은 "분류 실패로 default가 그대로 남은 경우"와 "정말 4개 분류 중 어디에도
        # 안 속한다고 판단한 경우"를 구분할 수 없으면 의미가 없다. reason을 필수로 만들어 후자임을
        # 확인할 수 있게 한다.
        if self.kind == "unknown" and not self.reason:
            raise ValueError("kind가 unknown이면 reason에 판단 근거를 남겨야 합니다.")
        return self


class StructuredRequestBatch(BaseModel):
    """Week 2 agent의 최종 structured_response(response_format) 스키마입니다.
    requests는 StructuredRequest의 list이며, 요청이 하나뿐이어도 list 형태를 유지합니다.
    base_date는 requests 안 각 StructuredRequest.date의 상대 날짜 표현(예: "내일") 해석 기준일입니다.
    requests 안 항목의 date가 비어 있는데 start_time/end_time 중 하나라도 있으면 base_date로 보정합니다."""

    base_date: str = Field(
        default_factory=current_app_date_iso,
        description="상대 날짜 해석 기준일입니다. requests 안 각 StructuredRequest.date 해석에 사용됩니다.",
    )
    requests: list[StructuredRequest] = Field(
        default_factory=list,
        description="구조화된 요청 목록입니다. 요청이 하나뿐이어도 StructuredRequest 하나가 든 list로 채웁니다.",
    )

    @model_validator(mode="after")
    def _backfill_date_from_base_date(self) -> "StructuredRequestBatch":
        # date는 start_time/end_time과 독립적인 필드가 아니다: 시간이 정해졌다는 것은 이미 "언제"가
        # 암묵적으로 정해졌다는 뜻이므로, date만 비어 있는 채로 두지 않고 base_date로 보정한다.
        for request in self.requests:
            if request.date is None and (request.start_time is not None or request.end_time is not None):
                request.date = self.base_date
        return self


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """LangChain structured output 결과를 StructuredRequest로 정규화합니다."""

    if isinstance(value, StructuredRequest):
        return value
    if isinstance(value, dict):
        return StructuredRequest.model_validate(value)
    raise RuntimeError(
        "StructuredRequest 또는 dict 형태의 structured output이 필요합니다. "
        f"현재 타입: {type(value).__name__}"
    )


def extract_structured_request(text: str) -> StructuredRequest:
    """Week 3 이상에서 agent를 새로 띄우지 않고 자연어를 StructuredRequest로 바꿉니다."""

    structured_model = chat_model().with_structured_output(
        StructuredRequest,
        method="function_calling",
    )
    result = structured_model.invoke(
        [
            {"role": "system", "content": join_system_prompt(week02_prompt_parts())},
            {"role": "user", "content": text},
        ]
    )
    return _coerce_structured_request(result)


@tool
def extract_schedule_request(query: str) -> str:
    """Week 3 이상 agent가 저장/조율 전에 호출하는 구조화 bridge tool입니다."""

    structured = extract_structured_request(query)
    return json.dumps(
        {
            "ok": True,
            "tool_name": "extract_schedule_request",
            "base_date": current_app_date_iso(),
            "structured_request": structured.model_dump(),
        },
        ensure_ascii=False,
    )


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""

    return week01_tools()


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    return [
        *week01_prompt_parts(),
        f"""너는 이제 사용자의 자연어 요청이나 Week 1 tool 호출 결과를, 일정 앱이 읽을 수 있는
StructuredRequest 필드(kind/title/date/start_time/end_time/members 등)로 구조화하는 역할도 겸한다.
오늘 날짜는 {current_app_date_iso()}이다. 사용자가 "내일", "다음 주 화요일"처럼 상대적인 날짜로
말하면 이 날짜를 기준으로 계산해서 date/start_time/end_time을 'YYYY-MM-DD', 'HH:MM' 형식으로 채운다.
확실하지 않은 값은 억지로 추론하지 않고 None 또는 빈 list로 둔다.""",
        """Week 1 tool(personal_create_schedule 등) 호출 결과로 JSON을 이미 받았다면, 같은 정보를
다시 얻기 위해 tool을 재호출하지 않는다. 그 payload를 그대로 읽어 structured_response의 필드를
채우는 근거로 사용한다.""",
        """Week 2에서는 아직 SQLite 저장, RAG 검색, 외부 멤버 일정 조율을 하지 않는다. 구조화 결과를
DB에 저장하거나 다른 사람 일정과 조율하는 동작은 이번 주차 범위 밖이다.""",
    ]


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(
        [
            *week02_prompt_parts(),
            """최종 답변(structured_response) 규칙: 개인 일정 생성처럼 구조화가 필요한 요청은
StructuredRequest 단독이 아니라 항상 StructuredRequestBatch 형태로 반환한다. 요청이 하나뿐이어도
그 StructuredRequest 하나를 requests 목록에 담아 StructuredRequestBatch로 채운다. 개인 일정 생성
요청에서는 personal_create_schedule tool 결과 JSON의 created_schedule 필드를 읽어
title/date/start_time/end_time/members 등을 채운다.
반면 personal_list_schedules(조회)나 personal_delete_schedule(삭제) 요청은 structured_response를
만드는 대상이 아니다. 그 tool 호출 결과는 자연스러운 한국어 문장으로 요약해서 답한다.""",
        ]
    )


def build_week02_agent() -> object:
    """Week 2 대화에서 structured_response를 직접 반환하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK02_AGENT
    if _WEEK02_AGENT is None:
        _WEEK02_AGENT = create_agent(
            model=chat_model(),
            tools=week02_tools(),
            system_prompt=week02_system_prompt(),
            # response_format=StructuredRequestBatch(그대로 넘기면 자동으로 provider native
            # structured output 전략이 선택됨)를 쓰면, 이 프록시 모델이 최종 답변 텍스트를
            # 그대로 두 번 반복해 내보내는 경우가 있어 json.loads가 "Extra data"로 깨진다.
            # ToolStrategy는 구조화 출력을 tool-call 인수로 받아 이 문제를 피한다.
            response_format=ToolStrategy(StructuredRequestBatch),
        )
    return _WEEK02_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()
