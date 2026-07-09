# Nana 프로젝트 — Week 02

## 실행 명령어
```
./run.sh --week2
```

## 주요 파일
- 구현 파일: `student_parts/week02_structure_natural_language_requests.py`
- 의존 파일: `student_parts/week01_wake_up_nana.py (week01_prompt_parts(), week01_tools() 상속)`
- 세션 헬퍼: `fixed/llm.py` → `chat_model()` 사용
- 에이전트 빌더: `build_week02_agent()` 및 런타임 엔트리 포인트 `build_week_agent()`

## 아키텍처
week2의 핵심 목적은 자연어 요청 또는 week1의 Tool이 뱉은 JSON을 분석하여 일정 앱이 이해할 수 있는 구조화된 객체(StructuredRequestBatch)로 변환하는 것이다. 이 단계에서는 데이터를 절대 저장하지 않는다.

```
[사용자 자연어 입력 / Week 1 Tool JSON] 
       ↓
[Week 2 Agent (with week02_tools)]
       ↓ (자연어 구조화 및 분석)
[StructuredRequestBatch (최종 변환 결과값)]
```
Week 2 에이전트는 response_format=StructuredRequestBatch가 설정된 LangChain 에이전트(langchain.agents.create_agent)로 빌드된다.

## 구조화 스키마 키 규격 (Hard constraint)
1. StructuredRequest
LLM이 자연어에서 추출한 개별 요청을 정의하는 중심 스키마이다. 각 필드에는 LLM Structured Output이 정확히 이해할 수 있도록 명확한 한국어 description을 필수로 부착해야 한다.

필드명 | 타입 | 설명 / 제약 조건
kind | Literal | "personal_schedule, group_schedule, todo, reminder, unknown 중 하나만 허용 (RequestKind)"
title| Optional[str] | None | 일정/할 일의 제목
date | Optional[str] | "확실할 때만 YYYY-MM-DD 형식, 불확실하면 None"
start_time | Optional[str] | "확실할 때만 HH:MM 형식, 불확실하면 None"
end_time | Optional[str] | "확실할 때만 HH:MM 형식, 불확실하면 None"
members | list[str] | 참석자/관련 멤버 목록. 모르면 빈 리스트([])
priority | Optional[str] | 할 일 등의 우선순위
reason | Optional[str] | 이 종류와 필드로 판단한 LLM의 근거
original_text | str | 구조화 대상이 된 원문 텍스트 보존

2. StructuredRequestBatch
최종 structured_response로 반환될 스키마 규격이다.
- requests: list[StructuredRequest] (요청이 단 하나여도 List 형태를 유지한다.)
- base_date: str(상대 날짜 해석의 기준일인 current_app_date_iso 값을 담는다.)

## 절대 금지 / 반드시 지킬 것 (Known gotchas)
- 데이터 저장 금지: 구조화된 결과를 DB나 메모리에 저장하려는 시도를 하지 않는다. 오직 구조화된 Batch 객체를 반환하는 것이 목적이다.
- 모르는 값 억지로 생성 금지: 날짜나 시간 등이 확실하지 않다면 억지로 추측하지 않고 None 또는 []로 안전하게 처리한다.
- week1 자산의 누적 상속: week02_tools()는 week01_tools()를 그대로 가져와 노출해야 한다.개인 일정 생성 요청 시 personal_create_schedule이 반환한 created_schedule JSON 내부 페이로드를 LLM이 읽고 구조화 근거로 사용하기 위함이다.
- week02_prompt_parts()는 기존 week01_prompt_parts()의 지시 사항 위에 Week2 구조화 지시를 누적(Append)해야 한다. 기존 프롬프트를 덮어써서 날려버리지 않는다. 
- 포맷 연결: build_week02_agent() 구현 시 반드시 response_format = StructuredRequestBatch를 명시적으로 연결하여 체인이 최종 답변 클래스 형식으로 정상 출력되도록 한다. 
