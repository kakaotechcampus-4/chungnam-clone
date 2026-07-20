from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.tools import tool
from pydantic import BaseModel, Field, ValidationError,model_validator,field_validator
from langchain.agents.structured_output import ToolStrategy
from fixed.config import CONFIG
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso,app_started_at_iso
from student_parts.week01_wake_up_nana import join_system_prompt, week01_prompt_parts, week01_tools
from datetime import datetime,date,timedelta


RequestKind = Literal["personal_schedule", "group_schedule", "todo", "reminder", "unknown"]
LLMKind = Literal["schedule", "todo", "reminder", "unknown"]

PriorityKind=Literal["low","mid","high"]
# 우선 순위 또한, low mid high 셋 중 하나로 제한해, 이 후 일정 목록으로 확장한다면, 정렬이 쉽게 가능하도록 한다.

timeRegular = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")   
dateRegular = re.compile(r"^\d{4}-\d{2}-\d{2}$") 

_WEEK02_AGENT: Any | None = None

def what_is_time():
    now = datetime.fromisoformat(app_started_at_iso())
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return now.strftime("%Y-%m-%d %H:%M:%S") + f" {weekdays[now.weekday()]}요일"

class StructuredRequest(BaseModel):
    """LLM structured output으로 추출되는 2주차 요청 스키마입니다."""
    
    kind: LLMKind = Field(
        description="""
        요청의 종류를 의미하며
        reminder: 특정 시점에 알려달라는 요청
        todo: 사용자가 어떤 일을 해야한다는 요청`
        schedule: 회의, 약속처럼 특정 시점에 진행되는 일정
        unknown: 위 기준으로 판단할 수 없는 요청
        """)
    title:str|None=Field(default=None,description="""
                         요청의 제목, 일정의 핵심 내용을 짧게 요약한것을 의미함, 
                         알 수 없으면 None으로 둠
                         """)
    date:str|None=Field(default=None,description="""
                        요청 날짜를 의미하며, 확실할 때만 YYYY-MM-DD 형식으로 채워야 함,
                        ~까지,~안에 처럼 범위를 뜻하는 표현은 그 범위안의 날짜로 채운다.
                        기준 시각보다 과거인 날짜는 절대 만들지마라.
                        알 수 없으면 None으로 둠
                        """)
    start_time:str|None=Field(default=None,description="""
                              일정의 시작 시간을 의미 하며, 
                              사용자의 입력에 요청한 일정의 시작 시각이 있을때만 HH:MM 형식으로 채워야 함, 
                              시작 시간을 알 수 없으면 None으로 둠""")
    end_time:str|None=Field(default=None,description="""
                              일정의 종료 시간을 의미 하며, 
                              사용자의 입력에 요청한 일정의 종료 시각이 있을때만 HH:MM 형식으로 채워야 함, 
                              종료 시간을 알 수 없으면 None으로 둠""")
    members:list[str]=Field(default_factory=list,description="""
                            요청한 일정에 참석하는 멤버의 이름 목록을 의미하며
                            사람 이름이 명시되어있고, 그 사람이 일정에 참여하는 경우에 포함한다.
                            사용자 본인은 제외해라.
                            """)
    priority:PriorityKind|None=Field(default=None,description="""
                            요청의 우선순위를 의미한다.
                            타인과의 약속같이 사용자 또는 타인에게 손해가 발생할 가능성을 기준으로 판단하며,
                            중요도를 파악할 수 없는 의미없는 요청은 None으로 둔다.
                            """)
    reason:str|None=Field(default=None,description="요청 종류(kind), 날짜/시간, 우선순위를 그렇게 판단한 근거를 짧게 적는 필드")
    original_text:str=Field(default="",description="사용자가 입력한 원문 요청을 그대로 보존하는 필드")
    
    @model_validator(mode="after")
    def apply_urgency(self):
        if self.date is None:
            return self
        
        request_date=date.fromisoformat(self.date)
        today=date.fromisoformat(current_app_date_iso())
        
        if request_date in(today,today+timedelta(days=1)):
            self.priority="high"
        return self
    
    @model_validator(mode="after")
    def normalize_schedule_kind_member(self):
        if self.kind=="schedule":
            if len(self.members)==0:
                self.kind="personal_schedule"
            else:
                self.kind="group_schedule"
        return self
    
    @model_validator(mode="after")
    def validate_time_order(self):
        if(
            self.start_time is not None
            and self.end_time is not None
            and self.end_time<self.start_time
        ):
            raise ValueError(
                "end_time은 start_time보다 빠를 수 없습니다."
            )
        return self
    
    @field_validator("date", mode="before")
    @classmethod
    def validate_date_or_none(cls, v):
        if v is None:
            return None

        if not isinstance(v, str):
            raise ValueError("날짜는 문자열이어야 합니다.")

        if not dateRegular.fullmatch(v):
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")

        try:
            date.fromisoformat(v)
            return v
        except ValueError as error:
            raise ValueError("존재하지 않는 날짜입니다.") from error
    
    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_time_or_none(cls,v):
        if v is None:
            return None

        if not isinstance(v, str):
            raise ValueError("시간은 문자열이어야 합니다.")

        if not timeRegular.fullmatch(v):
            raise ValueError("시간은 HH:MM 형식이어야 합니다.")

        return v
    
    @field_validator("members", mode="before")
    @classmethod
    def normalize_members(cls, v):
        if v is None:
            return []

        if isinstance(v, str):
            v = [v]

        if not isinstance(v, list):
            raise ValueError("멤버는 문자열 또는 문자열 목록이어야 합니다.")

        normalized: list[str] = []

        for member in v:
            if not isinstance(member, str):
                raise ValueError(
                    "멤버 목록의 모든 값은 문자열이어야 합니다."
                )

            member = member.strip()

            if not member:
                raise ValueError(
                    "멤버 목록에 빈 문자열을 넣을 수 없습니다."
                )

            normalized.append(member)

        return normalized
    
class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 2차 과제 스키마입니다."""

    requests:list[StructuredRequest]=Field(default_factory=list,description="자연어 요청을 구조화한 StructuredRequest list, 요청이 하나뿐이어도 list 형태를 유지")
    base_date:str=Field(default_factory=current_app_date_iso,description="상대 날짜 해석 기준일, 현재 날짜를 YYYY-MM-DD 형식으로 담음")


def _coerce_structured_request(value: Any) -> StructuredRequest:
    if isinstance(value, StructuredRequest):
        return value
    if isinstance(value, dict):
        return StructuredRequest.model_validate(value)

    raise RuntimeError(
        f"StructuredRequest 또는 dict가 필요합니다: {type(value).__name__}"
    )


def extract_structured_request(text: str) -> StructuredRequestBatch:
    structured_model = chat_model().with_structured_output(
        StructuredRequestBatch,
        method="function_calling",
    )

    result = structured_model.invoke([
        {"role": "system", "content": join_system_prompt(week02_prompt_parts())},
        {"role": "user", "content": text},
    ])

    return StructuredRequestBatch.model_validate(result)


def _validation_error_payload(
    tool_name: str,
    error: ValidationError,
) -> str:
    return json.dumps(
        {
            "ok": False,
            "tool_name": tool_name,
            "error": "validation_failed",
            "validation_errors": [
                {
                    "field": ".".join(
                        str(part)
                        for part in item["loc"]
                    ),
                    "message": item["msg"],
                    "type": item["type"],
                }
                for item in error.errors()
            ],
        },
        ensure_ascii=False,
    )


@tool
def extract_schedule_request(query: str) -> str:
    """자연어 일정 요청을 구조화된 일정 요청으로 변환합니다."""

    try:
        structured = extract_structured_request(query)
    except ValidationError as error:
        return _validation_error_payload(
            "extract_schedule_request",
            error,
        )

    return json.dumps({
        "ok": True,
        "tool_name": "extract_schedule_request",
        "base_date": current_app_date_iso(),
        "structured_request": structured.model_dump(),
    }, ensure_ascii=False)


extract_schedule_request.handle_validation_error = (
    lambda error: _validation_error_payload(
        "extract_schedule_request",
        error,
    )
)


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""

    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""
    
    return join_system_prompt([*week02_prompt_parts(), """
                              Week1의 "도구를 사용한 뒤 자연어로 답한다" 규칙은 Week 2에서는 적용하지 않는다.
                              StructuredRequestBatch에는 요청이 하나뿐이어도 requests 목록에 StructuredRequest 하나를 담도록 해.
                              personal_create_schedule tool 결과 JSON의 created_schedule을 읽어 StructuredRequest 필드를 채워.
                              도구 호출 메시지의 content에는 StructuredRequestBatch JSON을 절대 작성하지 마라.
                              """])
    


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    return [
        *week01_prompt_parts(),
    f"""
    너는 자연어를 해석해서 구조화 하는 Agent이다.
    지금 시각은 {what_is_time()} 이다.
    
    사용자의 자연어 요청을 StructuredRequestBatch를 이용하여 StructuredRequest로 구성된 List를 만들어.
    입력이 Week 1 tool JSON이라면, 다시 tool을 호출하지 않고 payload를 읽어 StructuredRequestBatch.requests 안의 StructuredRequest로 만들어.
    SQLite 저장, RAG, 외부 멤버 일정 조율은 하지 않는다.
    오직 구조화된 StructuredRequestBatch를 반환하는 것만이 이번 주차에서 너의 역할이다.
    """
    ]


def build_week02_agent() -> object:
    """Week 2 대화에서 structured_response를 직접 반환하는 단일 LangChain agent를 만듭니다."""

    global _WEEK02_AGENT
    
    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")    
    
    if  _WEEK02_AGENT is None:
        _WEEK02_AGENT = create_agent(
            model=chat_model(),
            tools=week02_tools(),
            response_format=ToolStrategy(StructuredRequestBatch),
            system_prompt=week02_system_prompt()
        )
    
    
    return _WEEK02_AGENT
    


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()

#bash ./run.sh --week2
#uv run python batch_test.py
