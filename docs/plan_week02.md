# Week 2 자연어 요청 구조화 구현 Plan

## 1. 목표

사용자의 한국어 자연어 요청("다음 주 화요일 오후 3시에 철수랑 회의 잡아줘")이나 Week 1 tool이 반환한 JSON payload를, 일정 앱이 읽을 수 있는 `StructuredRequest`/`StructuredRequestBatch` pydantic 스키마로 구조화하는 LangChain agent를 완성한다. 구조화 결과는 아직 저장하지 않으며(Week 3 범위), `create_agent(...)`에 `response_format=StructuredRequestBatch`를 연결한 structured output이 핵심이다.

## 2. 수정 범위

- 구현 파일: `student_parts/week02_structure_natural_language_requests.py` **단 하나**.
- 검증 파일: `test/test_week02_structured_request.py`를 새로 생성한다.
- 구현 대상: `StructuredRequest`, `StructuredRequestBatch`, `week02_tools()`, `week02_prompt_parts()`, `week02_system_prompt()`, `build_week02_agent()`.
- `_coerce_structured_request()`, `extract_structured_request()`, `extract_schedule_request()`는 이후 회차 예약 함수이므로 수정하지 않는다.
- `fixed/`, `mcp_server/`, `app.py`, `static/`는 건드리지 않는다.
- `student_parts/week01_wake_up_nana.py`는 prompt 조각에 `WEEK 1:` 표시를 붙이는 문자열 수정만 허용하고, tool/agent 로직은 건드리지 않는다.
- `AGENTS.md`는 Week 2 범위와 멘토 리뷰 피드백 컨벤션을 반영해 갱신한다.

## 3. 사용할 헬퍼

이미 구현되어 있어 그대로 호출한다.

| 헬퍼                       | 위치                                 | 역할                                        |
| -------------------------- | ------------------------------------ | ------------------------------------------- |
| `join_system_prompt(parts)` | `student_parts/week01_wake_up_nana.py` | 주차별 prompt 조각을 누적 system prompt로 합침 |
| `week01_prompt_parts()`    | `student_parts/week01_wake_up_nana.py` | Week 1 prompt 조각 (Week 2 지시를 뒤에 누적)  |
| `week01_tools()`           | `student_parts/week01_wake_up_nana.py` | 개인 일정 CRUD tool 3개 목록                 |
| `chat_model()`             | `fixed/llm.py`                       | LLM 인스턴스 생성                            |
| `current_app_date_iso()`   | `fixed/runtime_clock.py`             | 오늘 날짜 `YYYY-MM-DD` (base_date 기본값)    |
| `CONFIG.has_openai_key`    | `fixed/config.py`                    | PROXY_TOKEN 존재 검사                        |

규약:

- 모든 스키마 필드에 LLM structured output이 이해할 수 있는 한국어 `description`을 단다.
- 모르는 값을 억지로 만들지 않는다. 확실하지 않으면 None 또는 빈 list로 둔다.
- `date`/`start_time`/`end_time`은 확실할 때만 `YYYY-MM-DD`, `HH:MM` 형식으로 채운다.
- agent 명칭은 `Kanana Schedule Agent`, 일정의 주체는 사용자로 표현한다.

Week 1 PR(#24) 멘토 리뷰 피드백에서 이어지는 컨벤션:

- 필터/변환 로직은 `if` 나열 대신 list comprehension과 논리 연산자로 작성하고, None 안전 조건을 comprehension 안에 포함한다.
- 필수 필드는 `.get()` 대신 직접 인덱싱(`obj["key"]`)으로 접근하고, 불필요한 방어 코드를 넣지 않는다.

## 4. 구현 설계

### 4.1 `StructuredRequest`

- `kind: RequestKind` — 요청 종류. Literal 값(personal_schedule/group_schedule/todo/reminder/unknown)만 허용.
- `title/date/start_time/end_time: str | None = None` — 일정 핵심 필드. 확실할 때만 형식에 맞춰 채움.
- `members: list[str]` — `Field(default_factory=list)`. 참석자/관련 멤버.
- `priority/reason: str | None = None` — 할 일 우선순위, 판단 근거.
- `original_text: str = ""` — 요청 원문 보존.

### 4.2 `StructuredRequestBatch`

- `requests: list[StructuredRequest]` — `Field(default_factory=list)`. 요청이 하나뿐이어도 list 유지.
- `base_date: str` — `Field(default_factory=current_app_date_iso)`. 상대 날짜 해석 기준일.

### 4.3 `week02_tools()`

Week 1 tool 목록을 그대로 반환한다: `return week01_tools()`.

### 4.4 `week02_prompt_parts()`

`*week01_prompt_parts()` 뒤에 Week 2 지시 조각을 추가한다. 각 주차 prompt 조각의 맨 위에는 `WEEK N:` 표시를 붙여 누적 프롬프트에서 어느 주차 지시인지 구분한다.

1. Week 2 요청 구조화 agent 역할과 오늘 날짜(`current_app_date_iso()`) 기준 상대 날짜 해석.
2. 자연어를 StructuredRequest 필드(kind/title/date/start_time/end_time/members/priority/reason/original_text)로 구조화하라는 지시.
   `response_format` 스키마가 필드 구성을 이미 전달하므로 기술적으로는 중복이지만, 과제 가이드 TODO가 명시한 지시라 유지한다.
3. 모르는 값은 None/빈 list로 두고, date/시간은 확실할 때만 형식에 맞춰 채우는 규칙.
4. Week 1 tool 결과 JSON을 받은 경우 tool을 재호출하지 않고 payload를 읽어 structured_response로 구성.
5. Week 2에서는 SQLite 저장, RAG, 외부 멤버 일정 조율을 하지 않음을 명시.

### 4.5 `week02_system_prompt()`

`join_system_prompt([...week02_prompt_parts(), 최종 답변 규칙])`으로 합친다. 최종 답변 규칙에는 StructuredRequestBatch structured_response 반환, 단건 요청도 requests 목록 유지, `personal_create_schedule` 결과의 `created_schedule` payload로 필드 채우기를 담는다.

### 4.6 `build_week02_agent()`

`build_week01_agent()` 패턴을 따르되 `response_format=StructuredRequestBatch`를 추가한다.

1. `CONFIG.has_openai_key`가 없으면 `RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")`.
2. 전역 `_WEEK02_AGENT`가 없을 때만 `create_agent(model=chat_model(), tools=week02_tools(), response_format=StructuredRequestBatch, system_prompt=week02_system_prompt())`로 생성.
3. 생성 또는 재사용한 `_WEEK02_AGENT` 반환.

## 5. 검증 시나리오

```text
Scenario: StructuredRequest 기본값이 안전하다
When StructuredRequest is created with kind only
Then title/date/start_time/end_time/priority/reason are None
And members equals []
And original_text equals ""
```

```text
Scenario: RequestKind 밖의 값은 거부한다
When StructuredRequest is created with kind "lunch_menu"
Then pydantic ValidationError is raised
```

```text
Scenario: StructuredRequestBatch 기본값이 기준일을 담는다
When StructuredRequestBatch is created with no arguments
Then requests equals []
And base_date equals current_app_date_iso()
```

```text
Scenario: Week 2 tool 목록은 Week 1과 동일하다
When week02_tools is invoked
Then the result equals week01_tools()
And tool names are personal_create_schedule, personal_list_schedules, personal_delete_schedule
```

```text
Scenario: system prompt에 구조화 규칙이 담긴다
When week02_system_prompt is invoked
Then the prompt contains StructuredRequestBatch, structured_response, created_schedule
And the prompt contains current_app_date_iso()
```

```text
Scenario: LLM이 자연어를 structured_response로 구조화한다 (PROXY_TOKEN 필요)
Given PROXY_TOKEN is configured
When user asks "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"
Then result["structured_response"] is a StructuredRequestBatch instance
And requests is not empty
And requests[0].kind is "personal_schedule"
And requests[0].members contains "철수"
```

PROXY_TOKEN이 없는 환경에서는 LLM 연동 검증을 건너뛰고 스키마 검증만 수행한다.

## 6. 검증 명령

```bash
python -m compileall -q app.py fixed student_parts mcp_server
python test/test_week02_structured_request.py
./run.sh --week2   # 수동: "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘" 입력 → structured_response 확인
```

## 7. 완료 기준

- [ ] StructuredRequest/StructuredRequestBatch 스키마가 한국어 description과 함께 완성된다.
- [ ] week02_tools/week02_prompt_parts/week02_system_prompt/build_week02_agent가 구현된다.
- [ ] `./run.sh --week2` 실행 시 최종 답변이 StructuredRequestBatch structured_response로 반환된다.
- [ ] `python -m compileall -q app.py fixed student_parts mcp_server`가 성공한다.
- [ ] `python test/test_week02_structured_request.py`가 성공하고, week01 테스트 회귀가 없다.
- [ ] 구현 변경은 `student_parts/week02_structure_natural_language_requests.py`에 있고, 그 외 변경은 week01 prompt 문자열(`WEEK 1:` 표시)과 `AGENTS.md` 갱신뿐이다. 검증 코드는 `test/test_week02_structured_request.py`에만 있다.
