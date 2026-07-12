from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
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

    # kind는 RequestKind(Literal 5종)만 허용 → LLM이 다른 종류를 지어내지 못한다.
    kind: RequestKind = Field(
        description="요청 종류. personal_schedule(개인 일정), group_schedule(여럿이 함께하는 일정), "
        "todo(할 일), reminder(알림), unknown(판단 불가) 중 하나."
    )
    # 아래 4개는 문장에 없을 수 있으므로 str | None. 확실하지 않으면 억지로 만들지 말고 None으로 둔다.
    title: str | None = Field(default=None, description="요청의 제목이나 핵심 내용 요약.")
    date: str | None = Field(default=None, description="날짜. 확실할 때만 YYYY-MM-DD 형식으로 채운다.")
    start_time: str | None = Field(default=None, description="시작 시간. 확실할 때만 24시간제 HH:MM 형식으로 채운다.")
    end_time: str | None = Field(default=None, description="종료 시간. 확실할 때만 24시간제 HH:MM 형식으로 채운다.")
    # 참석자/관련 멤버. 언급이 없으면 빈 list — default_factory로 인스턴스마다 새 list를 만든다.
    members: list[str] = Field(default_factory=list, description="참석자나 관련 멤버 이름 목록. 언급이 없으면 빈 목록.")
    priority: str | None = Field(default=None, description="할 일(todo)의 우선순위. 예: low, medium, high. 언급이 없으면 None.")
    reason: str | None = Field(default=None, description="이 kind로 분류한 판단 근거를 짧은 한국어 문장으로.")
    # 원문은 항상 보존한다(디버깅/이후 주차 저장용). 없을 수 없으므로 str이고 기본은 빈 문자열.
    original_text: str = Field(default="", description="사용자가 입력한 원문 그대로.")


class StructuredRequestBatch(BaseModel):
    """여러 자연어 의도를 StructuredRequest 목록으로 나누는 메인과제 스키마입니다."""

    # 한 문장에 여러 의도("회의 잡고 보고서도")가 섞여도 요청 단위로 나눠 담는다.
    # 요청이 하나뿐이어도 list 형태를 유지해야 읽는 쪽 코드가 단순해진다.
    requests: list[StructuredRequest] = Field(
        default_factory=list,
        description="자연어에서 추출한 구조화 요청 목록. 요청이 하나여도 목록에 하나를 담는다.",
    )
    # "내일" 같은 상대 날짜를 어떤 날짜 기준으로 해석했는지 결과에 함께 기록한다.
    base_date: str = Field(
        default_factory=current_app_date_iso,
        description="상대 날짜(내일, 다음 주 화요일 등)를 해석한 기준일. YYYY-MM-DD 형식.",
    )


def _coerce_structured_request(value: Any) -> StructuredRequest:
    """LangChain structured output 결과를 StructuredRequest로 정규화합니다."""

    # with_structured_output 결과는 모델/버전에 따라 Pydantic 객체 또는 dict로 올 수 있다.
    # ① 이미 검증된 객체면 그대로 쓴다.
    if isinstance(value, StructuredRequest):
        return value
    # ② dict면 Pydantic 검증을 거쳐 객체로 바꾼다. (필드/타입이 어긋나면 ValidationError)
    if isinstance(value, dict):
        return StructuredRequest.model_validate(value)
    # ③ 둘 다 아니면 잘못된 LLM 응답 — 조용히 통과시키지 않고 즉시 실패시킨다(fail fast).
    raise RuntimeError(f"StructuredRequest로 변환할 수 없는 LLM 응답입니다: {type(value).__name__}")


def extract_structured_request(text: str) -> StructuredRequest:
    """Week 3 이상에서 agent를 새로 띄우지 않고 자연어를 StructuredRequest로 바꿉니다."""

    # agent 없이 모델에 직접 구조화 계약을 건다. function_calling 방식은
    # 프록시 환경에서도 안정적으로 동작한다(ToolStrategy를 쓴 것과 같은 이유).
    structured_llm = chat_model().with_structured_output(StructuredRequest, method="function_calling")

    # 메인과제와 같은 프롬프트 조각을 재사용해 bridge의 분류 기준을 agent와 일치시킨다.
    # (멘토님 피드백으로 추가한 경계 규칙도 여기서 그대로 적용되도록.)
    result = structured_llm.invoke(
        [
            {"role": "system", "content": join_system_prompt(week02_prompt_parts())},
            {"role": "user", "content": text},
        ]
    )

    # 결과가 Pydantic 객체든 dict든 검증된 StructuredRequest 하나로 정규화한다.
    return _coerce_structured_request(result)


@tool
def extract_schedule_request(query: str) -> str:
    """Week 3 이상 agent가 저장/조율 전에 호출하는 구조화 bridge tool입니다."""

    # ① 자연어든 Week 1 tool JSON payload든 StructuredRequest 하나로 구조화한다.
    structured = extract_structured_request(query)

    # ② Week 1 tool들과 같은 규약(ok/tool_name)으로 감싸고, 상대 날짜 해석 기준일을 함께 남긴다.
    payload = {
        "ok": True,
        "tool_name": "extract_schedule_request",
        "base_date": current_app_date_iso(), # 해석 기준일을 데이터에 동봉. "내일"이 어느 날 기준인지 저장 시점에도 추적 가능.
        "structured_request": structured.model_dump(),
    }
    #    [model_dump()가 필요한 이유 — 같은 데이터의 세 가지 형태]
    #      형태 1. Pydantic 객체  StructuredRequest(...)   → 파이썬 안에서만 사용. obj.title로 접근, 타입 검증 보장.
    #      형태 2. dict          {"kind": "todo", ...}    → 검증 없는 기본 자료구조. d["title"]로 접근.
    #      형태 3. JSON 문자열    '{"kind": "todo", ...}'  → 그냥 글자. 파이썬 밖(tool 반환/저장/전송)으로 나갈 수 있는 유일한 형태.
    #
    #    json.dumps(포장)는 dict/list/str/숫자/None 같은 "기본 재료"만 문자열로 바꿀 수 있다.
    #    StructuredRequest는 우리가 만든 커스텀 클래스라 그대로 넣으면
    #    TypeError: Object of type StructuredRequest is not JSON serializable 로 죽는다.
    #    → 그래서 model_dump()(분해: 객체→dict)로 기본 재료로 바꾼 뒤 payload에 담는다.
    #    참고: model_dump(객체→dict)와 model_validate(dict→객체, _coerce에서 사용)는 서로 역방향 변환이다.

    # ③ tool은 문자열 반환이 안정적 → 위 "형태 3"으로 포장해 내보낸다.
    #    ensure_ascii=False는 한글이 \uXXXX로 깨지지 않게 하는 옵션.
    return json.dumps(payload, ensure_ascii=False)


def week02_tools() -> list[Any]:
    """Week 2 agent에 Week 1 도구를 노출해 tool JSON을 structured_response 근거로 씁니다."""

    # Week 1에서 만든 개인 일정 CRUD tool을 그대로 재사용한다.
    # 일정 생성 요청이면 tool이 먼저 실행되고, 그 결과 JSON이 구조화의 근거가 된다.
    return week01_tools()


def week02_system_prompt() -> str:
    """2주차 agent가 따르는 시스템 프롬프트입니다."""

    # 주차별 prompt 조각 위에 "최종 답변은 구조화 출력" 규칙을 마지막에 얹는다.
    # join_system_prompt의 헤더 규칙에 따라 뒤에 오는 지시가 우선한다.
    return join_system_prompt(
        [
            *week02_prompt_parts(),
            (
                "최종 답변은 반드시 StructuredRequestBatch 형식의 structured output으로 낸다. "
                "요청이 하나뿐이어도 requests 목록에 StructuredRequest 하나를 담는다. "
                "personal_create_schedule tool을 호출했다면 결과 JSON의 created_schedule 값을 읽어 "
                "title/date/start_time/end_time/members 필드를 그대로 채운다."
            ),
        ]
    )


def week02_prompt_parts() -> list[str]:
    """2주차 structured output agent가 따르는 system prompt 조각입니다."""

    # 기준일은 하드코딩하지 않고 앱 시작 시각 기준으로 주입한다(1주차와 같은 방식).
    today = current_app_date_iso()

    return [
        *week01_prompt_parts(),
        (
            f"너는 2주차 요청 구조화 담당이기도 하다. 오늘은 {today}이다. "
            "사용자의 자연어 요청을 StructuredRequest 필드"
            "(kind/title/date/start_time/end_time/members/priority/reason/original_text)로 구조화한다. "
            "상대 날짜는 오늘 기준 YYYY-MM-DD로, 시간은 24시간제 HH:MM으로 바꾸고, "
            "확실하지 않은 값은 억지로 만들지 말고 비워 둔다. "
            "Week 1 tool 결과 JSON을 이미 받았다면 같은 tool을 다시 호출하지 말고 "
            "그 payload를 읽어 구조화 결과를 만든다. "
            "이번 주에는 SQLite 저장, RAG 검색, 외부 멤버 일정 조율을 하지 않는다."
        ),
        # 멘토님 피드백 반영: 경계 케이스 분류 규칙 (지어내지 않기 / kind 경계 / 과거·감정 표현)
        (
            "분류 경계 규칙. "
            "① 현재 시각은 알 수 없다. '20분 뒤'처럼 지금 시각 기준의 상대 시간은 "
            "start_time을 계산할 수 없으므로 지어내지 말고 비워 둔다. "
            "② 특정 인물과의 1:1 약속처럼 내 일정에 추가하는 요청은 personal_schedule, "
            "팀·여러 사람의 일정을 함께 조율해야 하는 요청은 group_schedule로 분류한다. "
            "members에는 문장에 언급된 사람 표현을 그대로 담는다. "
            "③ 이미 지난 일의 회고나 감정 표현처럼 일정을 만들거나 조회하려는 의도가 없는 문장은 "
            "unknown으로 분류하고 날짜/시간을 채우지 않는다. "
            
            # 경계 케이스 추가 강화 (형식·조회·시간대·todo 경계 규칙)
            "④ 어떤 kind든 reason에는 분류 근거를 항상 짧은 한국어로 채운다. "
            "⑤ date/start_time/end_time에는 형식에 맞는 값 또는 null만 넣는다. "
            "빈 문자열이나 '미정' 같은 자리표시 문자열을 넣지 않는다. "
            "tool 결과 JSON에 '미정'이 있어도 구조화 결과에서는 null로 바꾼다. "
            "⑥ 새 일정/할 일/알림을 등록하려는 의도가 아닌 조회·삭제·수정 요청은 unknown으로 분류하고 "
            "reason에 어떤 요청인지 적는다. 조회 결과의 일정들을 requests에 담지 않는다. "
            "⑦ '아침/점심/저녁/밤'처럼 시간대 단어만 있고 정확한 시각이 없으면 start_time을 지어내지 말고 비워 둔다. "
            "⑧ 특정 날짜/시각에 하는 활동은 personal_schedule/group_schedule, "
            "기한만 있거나 완료해야 하는 작업('~해야 해')은 todo로 분류한다."
        ),
    ]


def build_week02_agent() -> object:
    """Week 2 대화에서 structured_response를 직접 반환하는 단일 LangChain agent를 만듭니다."""

    # LLM 프록시 토큰 없이는 agent를 만들 수 없으므로 명확한 에러로 안내한다.
    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK02_AGENT
    if _WEEK02_AGENT is None:
        _WEEK02_AGENT = create_agent(
            model=chat_model(),
            tools=week02_tools(),
            # Week 1과의 유일한 차이: 최종 답변을 StructuredRequestBatch 스키마로 검증해
            # 결과 dict의 structured_response 키로 받는다.
            # ToolStrategy로 감싸는 이유: 프록시 경유 시 provider 네이티브 JSON 모드가
            # 강제되지 않아 모델이 JSON 뒤에 추가 텍스트를 붙이는 파싱 오류가 났다.
            # tool 호출 방식은 프록시에서 안정적으로 동작하며, 파싱 실패 시 재시도도 지원한다.
            response_format=ToolStrategy(StructuredRequestBatch),
            system_prompt=week02_system_prompt(),
        )
    return _WEEK02_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week02_agent()
