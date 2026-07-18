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


# [2주차 1회차 수강생 구현 가이드]
#
# 목표
#   Week 1 tool이 만든 JSON payload나 사용자의 한국어 자연어 요청을 일정 앱이 읽을 수 있는
#   StructuredRequest/StructuredRequestBatch로 바꿉니다. Week 1은 이미 정해진 인자를 받아
#   임시 일정을 만들었다면, Week 2는 그 tool 결과 JSON과 "내일 오후 3시" 같은 자연어를
#   날짜/시간/종류/멤버 필드로 구조화하는 단계입니다. 구조화 결과는 아직 저장하지 않습니다.
#
# 구현 위치와 사용할 코드
#   - 이 파일(student_parts/week02_structure_natural_language_requests.py)의 StructuredRequest 스키마와
#     StructuredRequestBatch, week02_tools(), week02_prompt_parts(), week02_system_prompt(),
#     build_week02_agent()를 확인합니다.
#   - build_week02_agent()는 langchain.agents.create_agent, fixed/llm.py의 chat_model(),
#     week02_system_prompt(), response_format=StructuredRequestBatch를 사용해 Week 2 agent를 만듭니다.
#   - week02_tools()는 Week 1 도구 목록을 그대로 가져옵니다. Week 2 agent는 개인 일정 생성 요청에서
#     personal_create_schedule이 반환한 created_schedule JSON payload를 읽고
#     response_format=StructuredRequestBatch로 최종 구조화 결과를 확인합니다.
#   - week02_prompt_parts()는 student_parts/week01_wake_up_nana.py의 week01_prompt_parts() 위에
#     Week 2 구조화 지시를 추가합니다.
#
# 구현 대상
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
# StructuredRequest 읽는 법
#   - kind: personal_schedule, group_schedule, todo, reminder, unknown 중 하나입니다.
#   - title/date/start_time/end_time: 일정 앱이 실제 저장이나 생성에 사용할 핵심 필드입니다.
#   - members: 참석자/관련 멤버 list입니다. 모르면 빈 list로 둡니다.
#   - priority/reason/original_text: 할 일 우선순위, 판단 근거, 원문 보존용 필드입니다.
#   - 모르는 값을 억지로 만들지 않는 것이 중요합니다. 확실하지 않으면 None 또는 빈 list가 안전합니다.
#   - date/start_time/end_time은 확실할 때만 YYYY-MM-DD, HH:MM 형식으로 채웁니다.
#
# 참고 코드
#   - week01_prompt_parts()
#      Week 1 system prompt를 이어받아 Week 2 구조화 지시를 누적할 때 사용합니다.
#   - week01_tools()
#      Week 1 개인 일정 tool 목록입니다. Week 2 agent는 이 tool 결과 JSON을 구조화 근거로 씁니다.
#
# 검증 방법
#   ./run.sh --week2로 실행한 뒤 "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘" 같은 문장을 입력합니다.
#   최종 답변이 StructuredRequestBatch class 형식의 structured_response로 나오는지 확인합니다.
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


class StructuredRequest(BaseModel):
    """LLM structured output으로 추출되는 2주차 요청 스키마입니다."""

    # TODO: kind 필드를 RequestKind 타입으로 선언하고 Field(description=...)를 붙이세요.
    # TODO: title/date/start_time/end_time 필드를 str | None 타입으로 선언하고 기본값은 None으로 두세요.
    # TODO: members 필드를 list[str] 타입으로 선언하고 default_factory=list를 사용하세요.
    # TODO: priority/reason 필드를 str | None 타입으로 선언하고 기본값은 None으로 두세요.
    # TODO: original_text 필드를 str 타입으로 선언하고 기본값은 ""로 두세요.
    # TODO: 각 필드에는 LLM structured output이 이해할 수 있도록 한국어 description을 달아주세요.
    kind : RequestKind = Field(description = "사용자 요청 종류를 의미합니다. 요청 종류에는 personal_schedule=혼자 또는 개인 일정, group_schedule=본인 외에 한 명 이상의 다른 사람들과 함께하는 모임/약속/활동, todo=완료 여부가 있는 할 일 , reminder=기억해야 할 알림성 메시지, unknown=위 네 가지로 분류하기 애매한 요청")
    title : str | None = Field(default=None,description = "일정 또는 할 일 제목")
    date : str | None = Field(default=None,description = "일정 또는 할 일의 날짜 YYYY-MM-DD")
    start_time : str | None = Field(default=None,description = "시작 시간 HH:MM")
    end_time : str | None = Field(default=None,description = "종료 시간 HH:MM")


    members : list[str] = Field(default_factory=list, description = "참석자 목록")

    priority : str | None = Field(default= None, description = "일정이나 할 일의 우선순위")
    reason : str | None = Field(default= None, description = "LLM이 이 요청을 해당 kind와 필드값으로 판단한 근거") 

    original_text : str = Field(default = "", description = "사용자가 입력한 원문 텍스트")





class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 2차 과제 스키마입니다."""

    # TODO: requests 필드를 list[StructuredRequest] 타입으로 선언하고 default_factory=list를 사용하세요.
    requests: list[StructuredRequest] = Field(default_factory=list , description="사용자 입력에서 추출한 StructuredRequest 목록. 요청이 하나여도 리스트 형태를 유지한다.")

    # TODO: base_date 필드를 str 타입으로 선언하고 default_factory=current_app_date_iso를 사용하세요.
    base_date: str = Field(default_factory=current_app_date_iso, description="내일, 모레 등 을 실제 날짜로 계산할 때 기준이 되는 오늘 날짜(YYYY-MM-DD)") # current_app_date_iso : 필요할 때마다 함수를 호출해서 그 결과를 기본값으로 쓴다.

    # TODO: 각 필드에는 Week 2 구조화 결과와 상대 날짜 기준일을 설명하는 한국어 description을 달아주세요.
    


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """이후 회차에서 사용할 StructuredRequest 정규화 예약 함수입니다."""
    if isinstance(value, StructuredRequest):
        return value
    if isinstance(value, dict):
        return StructuredRequest.model_validate(value)
    raise RuntimeError(
        "StructuredRequest 또는 dict 형태의 structured output이 필요합니다. "
        f"현재 타입: {type(value).__name__}"
    )
    


def extract_structured_request(text: str) -> StructuredRequest:
    """이후 회차에서 사용할 단건 구조화 예약 함수입니다."""
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
    """이후 회차에서 저장 흐름과 연결할 예약 tool입니다."""
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

    # TODO: Week 1에서 구현한 tool 목록을 그대로 반환하세요.
    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    # TODO: join_system_prompt(...)로 week02_prompt_parts()와 Week 2 structured_response 최종 답변 규칙을 합치세요.
    return join_system_prompt(week02_prompt_parts())

    # TODO: StructuredRequestBatch에는 요청이 하나뿐이어도 requests 목록에 StructuredRequest 하나를 담도록 지시하세요.
    # -> Structured Request Batch 에서 description에 "요청이 하나여도 리스트 형태를 유지한다." 를 추가했다.
   
    # TODO: personal_create_schedule tool 결과 JSON의 created_schedule을 읽어 필드를 채우도록 지시하세요.
    # week02_prompt_parts() 에 추가해서 넣을것이다 


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    return [
        *week01_prompt_parts(),
        # TODO: Week 2 요청 구조화 agent 역할과 현재 날짜(current_app_date_iso()) 기준을 추가하세요.
        # TODO: 자연어를 StructuredRequest 필드(kind/title/date/start_time/end_time/members 등)로 구조화하도록 지시하세요.
        # TODO: Week 1 tool JSON을 받은 경우 다시 tool을 호출하지 않고 payload를 읽어 structured_response로 만들도록 지시하세요.
        # TODO: Week 2에서는 SQLite 저장, RAG, 외부 멤버 일정 조율을 하지 않는다고 명시하세요.
        f"너는 사용자의 자연어 요청을 StructuredRequest 형식으로 구조화하는 2주차 agent다. 오늘 날짜는 {current_app_date_iso()}이다.",
        "사용자 입력을 kind/title/date/start_time/end_time/members 등 StructuredRequest 필드로 구조화한다.",
        "요청이 하나여도 StructuredRequestBatch의 requests 리스트 안에 하나로 담는다.",  
        "Week 1 tool의 결과 JSON(personal_create_schedule의 created_schedule)을 이미 받았다면 같은 tool을 다시 호출하지 않고, 그 payload 값을 읽어 구조화된 결과를 만든다.", 
        "이번 주차에서는 SQLite 저장, RAG 검색, 외부 멤버 일정 조율은 하지 않는다.",
        
        "조회나 삭제 요청이 오면 해당 tool은 호출하되, structured_response의 requests는 빈 리스트로 둔다.",
        "사용자가 현재 메시지에서 명시적으로 요청한 작업 외에는 어떤 tool도 호출하지 않는다. 과거에 이미 완료된 요청을 임의로 수정, 삭제, 재생성하지 않는다.",
        "structured_response는 오직 현재 턴에서 새로 호출한 tool 결과와 현재 사용자 메시지만을 근거로 만든다. 이전 턴에서 이미 답변을 완료한 tool 결과를 재사용하지 않는다.",  
        "original_text에는 항상 현재 턴의 사용자 원문 메시지를 그대로 넣는다.",
        "personal_schedule, group_schedule, todo, reminder, unknown로 판단되는 요청은 tool 호출 없이도 반드시 kind에 StructuredRequest를 채워서 반환해야 한다. tool을 호출하지 않았다는 이유로 requests를 비워두지 않는다.",
        
        # kind
        "kind 분류 기준: 본인 혼자 하는 일이나 연인/가족처럼 밀접한 관계와의 1:1 약속은 personal_schedule, 여러 명이 함께 모이는 사교 활동이나 모임은 group_schedule로 분류한다.",
        "마감 기한이 있거나 '~해야 한다', '~까지 제출/완료' 처럼 완료 여부를 확인해야 하는 요청은 todo로 분류한다.",
        "특정 시점에 알려줘야하는 요청(예: '약 먹을 시간 알려줘', '생일 잊지 않게 해줘')은 reminder로 분류한다.",
        "위 기준으로도 분류가 애매하면 unknown으로 분류한다.",

        # reason
        "reason 필드는 항상 채운다. 어떤 표현을 근거로 각 필드값을 뽑았는지 한 문장으로 간단히 적는다.",
        
        # priority
        "priority 필드는 항상 low/medium/high 중 하나로 채운다. 마감이 임박하거나(당일, 긴급 표현) 중요하다는 표현이 있으면 high, 특별한 긴급함이 없으면 medium, 여유 있거나 사소한 내용이면 low로 판단한다.",
    

    ]


def build_week02_agent() -> object:
    """Week 2 대화에서 structured_response를 직접 반환하는 단일 LangChain agent를 만듭니다."""

    # TODO: CONFIG.has_openai_key가 없으면 RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")를 발생시키세요.
    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    # TODO: 전역 _WEEK02_AGENT를 재사용하고, 
    global _WEEK02_AGENT
    # 아직 없을 때만 create_agent(...)로 새 agent를 만드세요.
    # TODO: create_agent에는 model=chat_model(), tools=week02_tools(), response_format=StructuredRequestBatch,
    #       system_prompt=week02_system_prompt()를 연결하세요.
    if _WEEK02_AGENT is None:
        _WEEK02_AGENT = create_agent(
            model=chat_model(), 
            tools=week02_tools(), 
            response_format=StructuredRequestBatch,
            system_prompt=week02_system_prompt(),
        )
    # TODO: 생성 또는 재사용한 _WEEK02_AGENT를 반환하세요.
    return _WEEK02_AGENT
# 1주차 내용을 참고해서 작성했습니다 ! 
    


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()
