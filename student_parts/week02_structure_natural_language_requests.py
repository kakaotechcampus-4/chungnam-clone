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
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 메인과제 스키마입니다."""

    requests: list[StructuredRequest] = Field(default_factory=list, description="구조화된 요청 목록. 요청이 하나뿐이어도 list로 감쌈")
    #   -> 위와 같은 이유로 default_factory=list 사용

    base_date: str = Field(default_factory=current_app_date_iso, description="상대 날짜 해석 기준일 (YYYY-MM-DD). '내일', '다음 주' 같은 표현 해석에 사용")
    #   -> default_factory=current_app_date_iso : 인스턴스 생성 시 오늘 날짜를 자동으로 채움
    #   -> 함수 자체를 넘겨야 하므로 current_app_date_iso() 가 아니라 current_app_date_iso (괄호 없음)


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """LangChain structured output 결과를 StructuredRequest로 정규화합니다."""

    # LangChain이 structured output을 반환할 때 항상 StructuredRequest 객체가 오는 게 보장되지 않음
    # → 이미 올바른 타입이면 그대로, dict이면 검증 후 변환, 그 외엔 오류를 내서 조용히 통과 방지

    if isinstance(value, StructuredRequest):
        return value
    #  이미 StructuredRequest 인스턴스면 그대로 반환 (변환 불필요)

    if isinstance(value, dict):
        return StructuredRequest.model_validate(value)
    #  model_validate() : Pydantic v2의 dict → 모델 변환 + 타입 검증 메서드
    #  필드 타입이 맞지 않으면 ValidationError를 내줌

    raise RuntimeError(f"StructuredRequest로 변환할 수 없는 타입: {type(value)}")
    #  isinstance(value, StructuredRequest)도 아니고 dict도 아닌 경우
    #  조용히 None을 반환하거나 예외를 삼키면 나중에 디버깅이 매우 어려워짐
    #  명시적으로 RuntimeError를 내서 어디서 잘못됐는지 즉시 알 수 있게 함


def extract_structured_request(text: str) -> StructuredRequest:
    """Week 3 이상에서 agent를 새로 띄우지 않고 자연어를 StructuredRequest로 바꿉니다."""

    # build_week02_agent()처럼 agent loop 전체를 만들지 않고, LLM 단일 호출만으로 구조화
    # → tool 호출 없이 자연어 text를 StructuredRequest 하나로 즉시 변환하는 경량 함수

    llm = chat_model().with_structured_output(StructuredRequest, method="function_calling")
    #  chat_model() : fixed/llm.py에서 가져온 ChatOpenAI 인스턴스
    #  .with_structured_output(스키마, method="function_calling") :
    #     LLM이 반환할 때 스키마 형태로 강제하는 LangChain 체인 구성 메서드
    #     "function_calling" = OpenAI function call 방식으로 JSON 출력 강제
    #   결과: llm.invoke(messages)가 StructuredRequest 객체를 직접 반환하는 체인

    messages = [
        {"role": "system", "content": join_system_prompt(week02_prompt_parts())},
        {"role": "user", "content": text},
    ]
    #   role: LLM 대화 역할. "system"은 지시사항, "user"는 사용자 입력
    #   content: 각 메시지의 텍스트 내용
    #   week02_prompt_parts()로 week02 구조화 지시를 시스템 메시지에 넣음

    result = llm.invoke(messages)
    #   invoke() : LangChain 체인/LLM을 동기 호출하는 메서드
    #   structured_output 체인이라 result가 이미 StructuredRequest (또는 dict) 형태로 옴

    return _coerce_structured_request(result)
    #   result 타입이 StructuredRequest이든 dict이든 안전하게 StructuredRequest로 통일
    ...


@tool
def extract_schedule_request(query: str) -> str:
    """Week 3 이상 agent가 저장/조율 전에 호출하는 구조화 bridge tool입니다."""

    # Week 1의 personal_create_schedule처럼 @tool이 붙어 있어
    # Week 3 agent가 "이 tool을 호출하면 구조화된 JSON을 받을 수 있다"는 걸 LangChain이 인식

    structured_req = extract_structured_request(query)
    #   위에서 만든 함수 호출: query(자연어 또는 Week 1 JSON)를 StructuredRequest 하나로 변환

    payload = {
        "ok": True,
        "tool_name": "extract_schedule_request",
        "base_date": current_app_date_iso(),
        "structured_request": structured_req.model_dump(),
    }
    #   ok/tool_name 패턴: Week 1 tool들과 같은 반환 형태 (일관성)
    #   base_date: 상대 날짜 해석 기준일 (오늘 날짜). Week 3에서 저장할 때 참고
    #   structured_request: model_dump()로 StructuredRequest → dict 변환
    #   model_dump() : Pydantic v2의 모델 → dict 변환 메서드 (v1의 .dict()에 해당)

    return json.dumps(payload, ensure_ascii=False)
    #   ensure_ascii=False : 한국어가 \uXXXX 로 이스케이프되지 않고 그대로 출력
    #   tool은 항상 str을 반환해야 하므로 dict → JSON 문자열로 변환
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
