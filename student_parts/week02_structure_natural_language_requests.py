from __future__ import annotations
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
Priority = Literal["high", "medium", "low"]
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
    """사용자의 자연어 요청을 구조화한 결과입니다."""

    kind: RequestKind = Field(default="unknown", description="요청 종류. personal_schedule=개인 일정,group_schedule=단체, 여러 명이 함께하는 일정,todo=할일, reminder=알림, 확실하지 않으면 unknown을 사용한다.")
    title: str | None = Field(default=None, description="schedule, todo의 제목, 이름")
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="시작 날짜 YYYY-MM-DD, 확실하지 않으면 None")
    start_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$", description="HH:MM, 확실하지 않으면 None")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="종료 날짜 YYYY-MM-DD. 시작한 날 끝나면 None, 자정을 넘기는 일정(예: 23시~1시)만 다음 날짜를 채운다.")
    end_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$", description="HH:MM, 확실하지 않으면 None")
    members: list[str] = Field(default_factory=list, description="참석자/멤버 이름 목록, 모르면 빈 list")
    priority: Priority = Field(default="low", description="할 일 우선순위, high/medium/low, 모르면 low")
    reason: str | None = Field(default=None, description="이렇게 분류한 기준, 이유 제시")
    original_text: str = Field(default="", description="어떤 경우에도 사용자가 직접 입력한 자연어 질문/ 문장 그대로 대입해야함.")

    @model_validator(mode="after")
    def _check_time_order(self) -> "StructuredRequest":
        if self.date and self.end_date and self.end_date < self.date:
            # YYYY-MM-DD 제로패딩 형식이라 문자열 비교가 곧 날짜 비교다.
            raise ValueError(
                "end_date가 date(시작 날짜)보다 앞설 수 없습니다. "
                "종료 날짜를 시작 날짜와 같거나 이후로 바로잡으세요."
            )

        same_day = self.end_date is None or self.end_date == self.date
        # end_date가 주어지지 않았거나, 주어진 end_date가 date와 같은 경우.

        if self.start_time and self.end_time and same_day:
            # HH:MM 제로패딩 형식이라 문자열 비교가 곧 시각 비교다.
            if self.end_time <= self.start_time:
                raise ValueError(
                    "같은 날짜인데 end_time이 start_time보다 빠르거나 같습니다. "
                    "자정을 넘기는 일정이면 end_date에 다음 날짜를 채우고, 아니면 시간을 바로잡으세요."
                )
        return self



class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 2차 과제 스키마입니다."""
    
    requests: list[StructuredRequest] = Field(default_factory=list, description="구조화된 요청 목록")
    base_date: str = Field(default_factory=current_app_date_iso, description="상대 날짜 기준일 YYYY-MM-DD")

#default와 default_factory 차이.
#1. default: 해당 값으로 고정.
#   수정 가능한 객체(list, dict 등)에 default를 설정하면 모든 인스턴스가 같은 객체를 공유함.
#2. default_factory: 호출할 때 마다 새로운 객체가 생성됨.
#   수정 가능한 객체(list, dict 등)에 default_factory를 사용해야 함.


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """이후 회차에서 사용할 StructuredRequest 정규화 예약 함수입니다."""

    ...


def extract_structured_request(text: str) -> StructuredRequest:
    """이후 회차에서 사용할 단건 구조화 예약 함수입니다."""

    ...


@tool
def extract_schedule_request(query: str) -> str:
    """이후 회차에서 저장 흐름과 연결할 예약 tool입니다."""

    ...


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""
    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    week02_response_rules = """
    반드시 최종 결과물은 StructuredRequestBatch 포맷으로 구조화하여 출력해야 합니다.
    사용자의 일정 생성/변경 요청이 1개만 있더라도 requests 목록에 StructuredRequest 하나를 반드시 담도록 하세요.
    """

    return join_system_prompt([*week02_prompt_parts(), week02_response_rules])


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    current_date = current_app_date_iso()

    return [
        *week01_prompt_parts(),
        f"""당신은 사용자의 자연어 요청을 분석하여 일정을 구조화하고, 최종적으로 StructuredRequestBatch 포맷으로 반환하는 구조화 전용 어시스턴트입니다.
        
        - 상대적인 날짜(예: 내일, 다음주 화요일, 모레 등)를 정확한 날짜로 변환하기 위한 기준일(오늘)은 [{current_date}] 입니다. 
        이 날짜를 기준으로 상대적인 날짜를 계산해 YYYY-MM-DD 형식으로 도출해내세요.
        
        [구조화 매핑 가이드]
        - Kind 규칙
            아래 판별 절차를 1번부터 순서대로 검사하여, 처음 해당하는 종류로 분류하세요.
            사용자가 시스템에 시키는 행동(동사)이 판단 기준이며, 날짜/시간의 유무는 기준이 아닙니다.
            1. reminder: 시키는 행동이 '통지'인 경우. ("알려줘", "리마인드해줘", "까먹지 않게" 등)
                일정 이야기가 섞여 있어도 시키는 행동이 알림이면 reminder입니다.
                (예: "철수랑 회의 있는 거 잊지 않게 알려줘" -> reminder)
            2. todo: 특정 시각에 참석하는 일이 아니라, 완료해야 하는 작업인 경우.
                "~까지"는 발생 시각이 아니라 마감을 뜻하므로 todo의 신호입니다.
                (예: "금요일까지 보고서 제출해야 해" -> todo)
            3. group_schedule: 특정 시점에 발생하는 일정이면서, 사용자 본인 외 참석자가 있는 경우.
            4. personal_schedule: 특정 시점에 발생하는 일정이면서, 참석자 언급이 없는 경우.
            5. unknown: 위 어디에도 확신 있게 넣을 수 없는 경우. (인사, 잡담, 일정과 무관한 질문 등)
                억지로 1~4에 끼워 넣지 마세요.
        - title/date/start_time/end_time:
            이 필드들은 사용자가 명시적으로 언급했을 때만 채웁니다. 확실하지 않거나 제공되지 않은 정보는 None으로 처리하세요.
        - end_date:
            일정이 시작한 날에 끝나면 None으로 둡니다. 자정을 넘기는 일정(예: 23시부터 다음날 1시까지)일 때만
            종료 날짜를 YYYY-MM-DD 형식으로 채우세요.
        - members:
            참석자가 있다면 이름 리스트 형태로 만들고, 없다면 빈 리스트([])로 두세요.
        - priority:
            중요도를 high, medium, low로 분류하고, 정보가 없다면 low로 둡니다.
        - reason:
            이 요청을 이렇게 구조화한 논리적인 근거/이유를 한글로 간단하게 정리해 기록하세요.
            특히 kind는 판별 절차의 몇 번 규칙에 해당했는지 해당 규칙을 인용하여 적으세요.
        - original_text:
            어떤 경우에도 사용자가 직접 입력한 자연어 질문/문장 그대로 대입해야 합니다.

        [tool 중복 호출 금지]
        - 대화 기록 상에 이미 1주차 도구(personal_create_schedule 등)를 실행하여 반환된 JSON 결과가 존재한다면, 
        절대로 도구를 다시 실행(재호출)하지 마십시오.
        - 이미 도구가 실행된 경우, 그 결과(created_schedule 등)를 활용하여 
        `StructuredRequestBatch`의 `requests` 항목을 채워 즉시 최종 답변을 완성해야 합니다.
        - 오로지 입력 데이터의 포맷 구조화(Structuring) 및 StructuredRequestBatch 형식의 최종 응답 빌드에만 집중하십시오.
        """
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
            # ToolStrategy로 감싸야 Pydantic 검증 실패 시 에러 메시지가 LLM에 피드백되어 재시도된다.
            # (클래스를 그대로 넘기면 ProviderStrategy로 풀려 검증 실패가 곧바로 예외가 된다.)
            response_format=ToolStrategy(StructuredRequestBatch),
            system_prompt=week02_system_prompt(),
        )
    return _WEEK02_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()
