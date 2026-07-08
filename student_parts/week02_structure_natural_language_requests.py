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
#        payload란? → "데이터 묶음". tool이 처리를 마친 후 반환하는 JSON 결과물.
#                    예: personal_create_schedule이 돌려주는 {"ok": True, "created_schedule": {...}} 전체가 payload.
#      - kind는 RequestKind Literal에 들어 있는 값만 허용합니다.
#        Literal이란? → "이 값들 중 하나만 허용"을 표현하는 Python 타입 힌트.
#                      Literal["a", "b", "c"]라고 쓰면 세 문자열 외에는 타입 오류로 잡힘.
#                      런타임엔 강제 안 하지만 IDE·타입 체커가 틀린 값을 바로 경고해 줌.
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

    # Pydantic BaseModel 필드는 "이름: 타입 = Field(기본값, description="...")" 형태로 선언해
    # LLM이 structured output을 생성할 때 description을 보고 어떤 값을 채울지 판단함
    #
    # Pydantic이란?
    #   Python 데이터 검증 라이브러리. BaseModel을 상속해 클래스를 만들면:
    #   - 필드 타입이 맞지 않으면 자동으로 오류를 냄 (예: str 자리에 int를 넣으면 거부)
    #   - JSON ↔ Python 객체 변환을 자동으로 처리
    #   - LLM에게 "이 스키마 형태의 JSON으로 답해"를 강제할 때 response_format=으로 연결함

    kind: RequestKind = Field("unknown", description="요청 종류 (personal_schedule/group_schedule/todo/reminder/unknown 중 하나)")
    #   → RequestKind는 파일 위쪽에 Literal 타입으로 정의되어 있어 (5개 문자열만 허용)
    #   → 기본값을 "unknown"으로 두면 LLM이 분류 못 할 때 안전하게 fallback

    title: str | None = Field(None, description="일정/할 일 제목. 명확하지 않으면 None")
    date: str | None = Field(None, description="날짜 (YYYY-MM-DD 형식). 확실할 때만 채움")
    start_time: str | None = Field(None, description="시작 시간 (HH:MM 형식). 확실할 때만 채움")
    end_time: str | None = Field(None, description="종료 시간 (HH:MM 형식). 확실할 때만 채움")
    #   → 모두 str | None 타입, 기본값 None
    #   → "확실할 때만 채운다"는 게 핵심 — 모르면 억지로 만들지 않음

    members: list[str] = Field(default_factory=list, description="참석자/관련 멤버 목록. 모르면 빈 리스트")
    #   → list는 mutable 객체라 기본값을 [] 직접 쓰면 안 됨
    #   → default_factory=list : 인스턴스 생성 때마다 list()를 호출해 새 [] 만듦
    #
    # mutable이란?
    #   "변경 가능한" 객체. 만든 뒤 내부 값을 바꿀 수 있음.
    #   - mutable 예시: list([1,2,3]) → append/remove로 내용 변경 가능
    #   - immutable 예시: str, int, tuple → 값을 바꾸면 새 객체가 만들어지고 원본은 그대로
    #
    #   기본값으로 [] 직접 쓰면 위험한 이유:
    #   Python은 함수/클래스 정의 시점에 기본값을 딱 한 번만 만듦.
    #   모든 인스턴스가 그 하나의 [] 를 공유 → A.members.append("철수")하면 B.members에도 "철수"가 생김.
    #   default_factory=list를 쓰면 인스턴스마다 list()를 새로 호출하므로 각자 독립된 [] 가짐.

    priority: str | None = Field(None, description="우선순위 (예: 높음/보통/낮음). todo kind에서 주로 사용")
    reason: str | None = Field(None, description="이 kind와 필드값으로 판단한 LLM의 근거")
    original_text: str = Field("", description="사용자가 입력한 원문 텍스트. 가공하지 않고 그대로 보존")
    #   → priority/reason은 str | None, 기본값 None
    #   → original_text는 str, 기본값 "" (None 아님 — 항상 원문은 있으니까)


class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 2차 과제 스키마입니다."""

    requests: list[StructuredRequest] = Field(default_factory=list, description="구조화된 요청 목록. 요청이 하나뿐이어도 list로 감쌈")
    #   -> 위와 같은 이유로 default_factory=list 사용

    base_date: str = Field(default_factory=current_app_date_iso, description="상대 날짜 해석 기준일 (YYYY-MM-DD). '내일', '다음 주' 같은 표현 해석에 사용")
    #   -> default_factory=current_app_date_iso : 인스턴스 생성 시 오늘 날짜를 자동으로 채움
    #   -> 함수 자체를 넘겨야 하므로 current_app_date_iso() 가 아니라 current_app_date_iso (괄호 없음)


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
    #   → week01_tools()는 위 import에서 가져온 Week 1 tool 목록 반환 함수
    #   → Week 2는 새 tool을 추가하지 않고 Week 1 tool을 그대로 씀
    #   → personal_create_schedule이 반환한 JSON을 LLM이 읽어 StructuredRequest를 채움


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt([
        *week02_prompt_parts(),
        """[Week 2 최종 출력 규칙 — Week 1 지시보다 우선]
        - 최종 답변은 반드시 StructuredRequestBatch JSON 하나만 출력해. 그 외 텍스트는 절대 붙이지 마.
        - 일정 ID 등 정보는 JSON 필드(original_text 등)에만 담고, 사용자에게 별도 텍스트로 알리지 마.
        - 요청이 하나뿐이어도 requests 리스트에 StructuredRequest 하나를 담아.
        - personal_create_schedule tool을 실행했다면, 그 결과 JSON의 created_schedule 필드를
          읽어서 StructuredRequest 필드(kind/title/date/start_time/end_time 등)를 채워.
        - 같은 tool을 두 번 호출하지 마.""",
    ])
    #   → join_system_prompt()는 week01에서 import한 함수 — 조각들을 합쳐 하나의 프롬프트 문자열로 만듦
    #   → *week02_prompt_parts() : 리스트를 펼쳐서 삽입 (스프레드 연산자)
    #   → 마지막 조각에서 Week 2만의 출력 규칙을 추가로 지시
    


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    return [
        *week01_prompt_parts(),
        f"""너는 자연어 요청을 구조화하는 AI야.
        오늘 날짜: {current_app_date_iso()}
        
        역할:
        - 사용자의 한국어 요청이나 Week 1 tool 결과 JSON을 StructuredRequestBatch 형식으로 변환해.
        - '내일', '다음 주 화요일' 같은 상대 날짜는 오늘 날짜를 기준으로 YYYY-MM-DD로 변환해.
        - kind는 personal_schedule / group_schedule / todo / reminder / unknown 중에서 골라.
        - Week 1 tool을 이미 실행해서 JSON 결과가 있다면, tool을 다시 호출하지 않고
          그 JSON의 created_schedule 필드를 읽어서 StructuredRequest를 만들어.
        
        Week 2에서 하지 않는 것:
        - SQLite 저장 (Week 3에서 함)
        - RAG 검색 (Week 4에서 함)
        - 외부 멤버 일정 조율 (Week 5에서 함)
        """
        #   → f-string이라 current_app_date_iso()를 중괄호 안에 넣어 실행 시점 날짜를 넣음
        #   → 이 조각이 week01_prompt_parts() 뒤에 추가되어 Week 2 역할을 덧씌움
    ]


def build_week02_agent() -> object:
    """Week 2 대화에서 structured_response를 직접 반환하는 단일 LangChain agent를 만듭니다."""

    global _WEEK02_AGENT
    #   → 함수 안에서 전역 변수에 값을 할당하려면 반드시 global 선언 필요
    #   → 없으면 Python이 "_WEEK02_AGENT ="를 지역 변수 선언으로 해석
    #   → 결과: 함수 호출이 끝나면 지역 변수가 사라져서 매번 agent를 새로 만들게 됨 (싱글톤 실패)

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    #   → 키 없이 LLM 호출하면 나중에 더 알기 어려운 에러가 남 → 미리 명확하게 차단

    if _WEEK02_AGENT is None:
        _WEEK02_AGENT = create_agent(
            model=chat_model(),
            tools=week02_tools(),
            response_format=StructuredRequestBatch,
            system_prompt=week02_system_prompt(),
        )
    #   → 싱글톤 패턴: 처음 한 번만 agent를 만들고 이후 호출에선 재사용
    #
    # 싱글톤 패턴이란?
    #   "프로그램 전체에서 인스턴스를 딱 하나만 유지"하는 디자인 패턴.
    #   LLM agent처럼 만드는 데 비용(시간, API 호출)이 드는 객체에 주로 씀.
    #   동작 원리: 전역 변수(_WEEK02_AGENT)를 None으로 초기화 → 함수 첫 호출 시 생성 후 저장
    #              → 이후 호출에선 None이 아니므로 분기를 건너뛰고 저장된 것을 바로 반환
    #
    #   → response_format=StructuredRequestBatch : LLM 출력을 이 Pydantic 스키마로 강제
    #   → model, tools, system_prompt는 Week 1 패턴과 동일하게 연결

    return _WEEK02_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()
