from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field, ValidationError

from fixed.config import CONFIG
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from student_parts.week01_wake_up_nana import join_system_prompt, week01_prompt_parts, week01_tools


RequestKind = Literal["personal_schedule", "group_schedule", "todo", "reminder", "unknown"]
_WEEK02_AGENT: Any | None = None


class StructuredRequest(BaseModel):
    """LLM structured output으로 추출되는 2주차 요청 스키마입니다."""
    kind: RequestKind = Field(
        default="unknown",
        description="요청 종류: personal_schedule, group_schedule, todo, reminder, unknown 중 하나",
    )
    title: str | None = Field(default=None, description="일정 또는 할 일의 제목. 확실하지 않으면 None")
    date: str | None = Field(
        default=None, description="YYYY-MM-DD 형식의 날짜. 확실할 때만 채웁니다.")
    start_time: str | None = Field(
        default=None, description="HH:MM 형식의 시작 시간. 확실할 때만 채웁니다.")
    end_time: str | None = Field(
        default=None, description="HH:MM 형식의 종료 시간. 확실할 때만 채웁니다.")
    members: list[str] = Field(default_factory=list, description="참석자 또는 관련 멤버 목록. 모르면 빈 리스트")
    priority: str | None = Field(default=None, description="할 일 우선순위. 모르면 None")
    reason: str | None = Field(default=None, description="구조화 판단 근거나 메모. 모르면 None")
    original_text: str = Field(default="", description="원문 보존용 필드입니다.")


class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 메인과제 스키마입니다."""
    requests: list[StructuredRequest] = Field(
        default_factory=list,
        description="구조화된 요청 목록. 요청이 하나뿐이어도 리스트 형태로 유지합니다.",
    )
    base_date: str = Field(
        default_factory=current_app_date_iso,
        description="상대 날짜 해석을 위한 기준일(YYYY-MM-DD). 기본값은 현재 앱 날짜입니다.",
    )


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """LangChain structured output 결과를 StructuredRequest로 정규화합니다."""

    # 이미 StructuredRequest이면 그대로 반환한다.
    if isinstance(value, StructuredRequest):
        return value

    # dict이면 StructuredRequest 스키마로 검증해 변환한다.
    if isinstance(value, dict):
        try:
            return StructuredRequest.model_validate(value)
        except ValidationError as exc:
            raise RuntimeError(
                f"dict를 StructuredRequest로 검증할 수 없습니다: {exc}"
            ) from exc

    # 그 외 타입은 잘못된 LLM 응답으로 처리한다.
    raise RuntimeError(
        "StructuredRequest 또는 dict 형태의 structured output이 필요합니다. "
        f"실제 타입: {type(value).__name__}"
    )


def extract_structured_request(text: str) -> StructuredRequest:
    """Week 3 이상에서 agent를 새로 띄우지 않고 자연어를 StructuredRequest로 바꿉니다."""

    # 키워드를 직접 비교하는 규칙 기반 파서 대신, LLM이 StructuredRequest 스키마에 맞춰
    # 날짜·시간·멤버 등을 추출하도록 function calling 방식의 structured output을 사용합니다.
    structured_llm = chat_model().with_structured_output(
        StructuredRequest,
        method="function_calling",
    )

    # Week 2 프롬프트에는 현재 기준일과 필드 작성 규칙이 들어 있습니다.
    # text는 자연어 문장뿐 아니라 Week 1 tool이 반환한 JSON 문자열일 수도 있습니다.
    result = structured_llm.invoke(
        [
            ("system", join_system_prompt(week02_prompt_parts())),
            ("user", text),
        ]
    )

    # 모델의 반환 형태가 Pydantic 객체든 dict든 최종적으로 StructuredRequest 하나로 통일합니다.
    return _coerce_structured_request(result)


@tool
def extract_schedule_request(query: str) -> str:
    """Week 3 이상 agent가 저장/조율 전에 호출하는 구조화 bridge tool입니다."""

    # 먼저 자연어 또는 Week 1 JSON payload를 저장 가능한 공통 스키마로 구조화합니다.
    structured = extract_structured_request(query)

    # base_date를 함께 남겨 "내일", "다음 주" 같은 상대 날짜를 어느 날짜를 기준으로
    # 해석했는지 이후 Week 3 이상의 저장/조율 흐름에서도 확인할 수 있게 합니다.
    payload = {
        "ok": True,
        "tool_name": "extract_schedule_request",
        "base_date": current_app_date_iso(),
        "structured_request": structured.model_dump(),
    }

    # LangChain tool의 결과는 다음 tool이나 agent가 읽을 수 있도록 한글을 보존한 JSON 문자열로 반환합니다.
    return json.dumps(payload, ensure_ascii=False)


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""
    # Week1에서 만든 개인 일정 CRUD 도구들을 그대로 노출합니다.
    # (LLM이 Week1 tool 결과 JSON을 참고할 수 있도록 하기 위함)
    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""
    # week02_prompt_parts()로 만든 조각들을 합쳐서 최종 시스템 프롬프트를 만듭니다.
    return join_system_prompt(week02_prompt_parts())


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    return [
        *week01_prompt_parts(),
        (
            "당신은 Week 2 구조화 에이전트입니다. "
            f"현재 앱 기준일은 {current_app_date_iso()} 입니다. "
            "사용자 자연어 문장 또는 Week 1 도구(personal_create_schedule 등)의 JSON 결과를 읽고, "
            "이를 StructuredRequestBatch 형태로 정확히 변환하세요."
        ),
        (
            "출력 형식은 반드시 StructuredRequestBatch 입니다. "
            "요청이 하나뿐이어도 반드시 'requests' 필드에 StructuredRequest 하나를 담으세요. "
            "StructuredRequest 필드 및 형식:\n"
            "- kind: personal_schedule, group_schedule, todo, reminder, unknown 중 하나\n"
            "- title: 일정 또는 할 일의 제목\n"
            "- date: YYYY-MM-DD\n"
            "- start_time, end_time: HH:MM\n"
            "- members: 참석자 목록\n"
            "- priority, reason, original_text"
        ),
        (
            "만약 Week 1 tool의 JSON payload(예: personal_create_schedule의 created_schedule)를 받으면, "
            "다시 tool을 호출하지 말고 그 payload를 파싱하여 StructuredRequest의 각 필드를 채우세요. "
            "알 수 없는 kind는 'unknown', members는 [], nullable 필드는 None으로 두세요. "
            "날짜와 시간은 확실한 경우에만 채우세요."
        ),
        (
            "이번 주차 에이전트는 오직 구조화만 수행합니다. SQLite 저장, RAG 검색, 외부 멤버 일정 조율은 하지 마세요. "
            "구조화 판단 근거는 'reason' 필드에 간단히 남기고, 원문은 'original_text'에 보존하세요."
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
