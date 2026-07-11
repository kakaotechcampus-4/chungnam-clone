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

## 심화 과제 — 구조화 Bridge 함수 규격 (Hard constraint)

주요 타겟 함수: `_coerce_structured_request`, `extract_structured_request`, `extract_schedule_request`

### 아키텍처
에이전트 전체 루프(`create_agent`)를 새로 돌리지 않고, 입력된 자연어 또는 JSON을 단 하나의 `StructuredRequest`로 빠르게 강제 변환·구조화하여 차기 주차(Week 3 이상)의 저장 Tool로 넘겨주는 **다리(bridge)** 역할만 수행한다.

### 변환 규격

| 함수/도구 | 필수 반환 타입 / 포함 키 | 비고 |
|---|---|---|
| `_coerce_structured_request` | `StructuredRequest` 객체 | 실패 시 `RuntimeError` |
| `extract_structured_request` | `StructuredRequest` 객체 | `with_structured_output` 활용 |
| `extract_schedule_request` | `str` (JSON 문자열) | `ok`, `tool_name`, `base_date`, `structured_request` 포함 |

- `extract_schedule_request`의 `structured_request` 필드에는 `model_dump()` 결과(dict)가 들어가야 한다.
- 최종 반환 문자열은 반드시 `json.dumps(..., ensure_ascii=False)` 처리를 거칠 것.

### 절대 금지 / 반드시 지킬 것

1. **`_coerce_structured_request(value)`**
   - 타입이 이미 `StructuredRequest`이면 그대로 반환(pass-through).
   - `dict`이면 `StructuredRequest.model_validate(value)`로 검증.
   - 그 외 예상치 못한 타입일 경우 조용히 넘기지 말고 반드시 `RuntimeError`를 발생시킬 것.

2. **`extract_structured_request(text)`**
   - 에이전트 루프(`create_agent`)를 새로 만들거나 호출하지 말 것.
   - 오직 `chat_model().with_structured_output(StructuredRequest, method="function_calling")`만 단독 사용하여 호출할 것.
   - System 메시지: `join_system_prompt(week02_prompt_parts())` 결합 사용.
   - User 메시지: 인자로 받은 `text`를 그대로 주입.

3. **`extract_schedule_request(query)`**
   - 내부적으로 `extract_structured_request(query)`를 호출하여 싱글 구조화 객체를 확보할 것.
   - 반환하는 JSON 텍스트에 `ok`(성공 여부), `tool_name`, `base_date`(`current_app_date_iso` 등 기반)가 정확히 매핑되어야 함.
