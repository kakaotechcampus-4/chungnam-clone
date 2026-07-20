# Week 03 구현 계획 — 나나의 기록장 (SQLite 영속 저장)

> 이전 Week 02 계획(메인과제 + 심화 bridge + 버그 수정 로그)은 git 히스토리에 보존되어 있다
> (`git log -- plan.md` 또는 이전 커밋 참조). 이 문서는 Week 03 계획으로 전면 교체되었다.

---

## Context — 왜 이 작업을 하는가

- **목표**: Week 2가 만든 `StructuredRequest`를 Pydantic 입력 스키마로 검증 → `AppSQLiteStore`(SQLite)에 **저장**하고, 저장된 요청/일정을 **조회/수정/삭제**하는 세로 슬라이스를 완성한다. 이 단계부터 나나는 Week 1의 임시 메모리 대신 **앱 DB에 남는 "기록장"**(영속 메모리)을 갖는다.
- **핵심 사실**: 대상 파일 `student_parts/week03_build_nanas_logbook.py`는 **이미 481줄 스캐폴드**다. 모든 시그니처·Pydantic 스키마·`@tool(args_schema=...)` 배선·`week03_tools()`·에이전트 구조가 존재하고, **함수 본문(`...`)과 프롬프트 상수 2개만 비어 있다.** 따라서 이 과제는 "설계"가 아니라 **"이미 존재하는 store 메서드에 배선하는 본문 채우기"**다.
- **DB 계층은 이미 완성**: `fixed/app_store.py`의 `AppSQLiteStore`가 테이블 생성·FK 순서·shared_sync·삭제 안전 가드를 전부 구현해 둠. 우리 tool들은 **얇은 입구**로서 store 메서드를 호출하고 JSON을 반환할 뿐이다.

---

## 1. 대상 파일 파싱 결과 (`[3주차 수강생 구현 가이드]` + TODO)

### 핵심 흐름 (가이드 명시)
```
사용자 자연어 → extract_schedule_request(구조화) → save_structured_request(SQLite 저장)
             → list_saved_requests / get_saved_request / personal_list_saved_schedules(조회)
             → personal_update_saved_schedule / personal_delete_saved_schedules(수정/삭제)
```

### 구현 대상 함수 (티어별) — 23개 TODO 요약
| 티어 | 함수 | 핵심 TODO 요지 |
|---|---|---|
| 공통 | `SQLITE_MEMORY_PROMPT` (L31) | 새 대화/재시작에도 SQLite 조회 가능한 영속 메모리 규칙 |
| 공통 | `WEEK03_TOOL_CALL_PROMPT` (L34) | 구조화→저장→조회/수정/삭제 tool 호출 순서 규칙 |
| **메인** | `save_structured_request` @tool (L344-345) | 검증된 인자 → 저장 dict(None 제외) → store 저장, ok/tool_name JSON 반환 |
| **메인** | `list_saved_requests` @tool (L357) | kind/date_from/date_to 필터 조회, `rows` 반환 |
| **메인** | `get_saved_request` @tool (L365) | request_id 단건, 없으면 `row=None` |
| **메인** | `personal_list_saved_schedules` @tool (L378-379) | 기본 kind=personal_schedule, `filters`+`schedules` 반환 |
| 심화 | `SaveStructuredRequestInput.unwrap_legacy_payload` (L223) | 레거시 payload/structured_request wrapper 정규화 (`model_validator(mode="before")`) |
| 심화 | `_save_input_from` (L230) | dict/JSON/자연어/StructuredRequest → 단일 입력 스키마 |
| 심화 | `save_structured_request_payload` (L241) | tool 없이 직접 저장 helper |
| 심화 | `_delete_saved_schedules` (L302-303) | 조건 없으면 거부, `deleted_count/filters/deleted` 반환 |
| 심화 | `structured_request_from_week01_schedule` (L310) | Week1 attendees/id → members/source_schedule_id |
| 심화 | `personal_create_schedule` @tool (L324-325) | Week1 임시 생성 + SQLite 이중 저장 |
| 심화 | `delete_saved_schedules_dict` (L394) | app_store→store 매핑 helper |
| 심화 | `personal_update_saved_schedule` @tool (L409-410) | None 아닌 필드만 update, `updated_schedule/shared_sync` |
| 심화 | `personal_delete_saved_schedules` @tool (L425) | `_delete_saved_schedules` 위임 |
| 공통 | `week03_prompt_parts` (L458, L461) | Week1/2 프롬프트 위에 누적 |
| 공통 | `build_week03_agent` (L472) | `create_agent`로 생성 **+ 싱글톤 대입(현재 미대입 버그)** |

### 반환 규칙 (하드)
- 모든 `@tool`은 **JSON 문자열** 반환 → 반드시 `json_payload()`(ensure_ascii=False) 경유.
- 기본 `ok`/`tool_name` 포함, 조회=`rows`/`row`, 삭제=`deleted_count`/`filters`/`deleted`.

### 절대 지킬 것 (claude.md Hard constraint)
1. **Pydantic 재생성 금지** — tool 본문에서 args_schema가 이미 검증했으므로 클래스를 다시 만들지 않는다.
2. **래퍼 저장 금지** — `ok`/`tool_name`/`base_date`를 `payload_json`(raw_json)에 넣지 않는다.
3. **삭제 안전 가드** — 필터 전부 비면 삭제 거부.
4. **조회 실패 대응** — 단건 `row=None`, 목록 `rows=[]`, 예외 금지.
5. **유니코드 보존** — `json_payload`로 직렬화.
6. **프롬프트 누적** — Week1/2 위에 append (덮어쓰기 금지).
7. **Pydantic V2 문법** — `model_validator(mode="before")`, `model_dump()`.

---

## 2. 개발 마일스톤 (일반 과제 + 심화 과제 유기적 결합)

### M0 — 프롬프트 + 에이전트 부트스트랩 (공통) · *가장 먼저*
`./run.sh --week3`가 뜨게 만드는 선행 조건. 현재 `build_week03_agent`가 싱글톤을 대입하지 않아 `None`을 반환하는 버그를 여기서 잡는다.
- `SQLITE_MEMORY_PROMPT`: Week3는 임시 메모리 대신 SQLite 기록장을 사용, 새 대화/재시작에도 조회 tool로 확인하라는 한국어 규칙.
- `WEEK03_TOOL_CALL_PROMPT`: (1) `extract_schedule_request` 구조화 → (2) 결과 필드를 `save_structured_request`에 그대로 전달 → (3) 조회는 list/get/personal_list → (4) 수정/삭제 전 목록으로 후보 확인 → (5) 조건 없는 삭제 금지.
- `week03_prompt_parts()`: 두 TODO 슬롯에 브릿지 지시 + `current_app_date_iso()` 기준일/tool 선택 기준/주차 범위 지시 추가. `week02_prompt_parts()` 항목 유지.
- `build_week03_agent()`: `_WEEK03_AGENT = create_agent(model=chat_model(), tools=week03_tools(), system_prompt=week03_system_prompt())` 대입 후 반환. (Week3는 `response_format` 없음 — 배치 스키마가 아니라 tool JSON/자유 답변 반환.)
- **검증**: `import` 성공, `week03_system_prompt()` 비어있지 않고 Week1/2 텍스트 포함, `./run.sh --week3` 기동.

### M1 — 메인 저장→조회 세로 슬라이스 (메인과제, 독립 채점 가능)
`save_structured_request`, `list_saved_requests`, `get_saved_request`, `personal_list_saved_schedules`.
- `save_structured_request`: 함수 인자로 `save_dict` 조립 → **None 값 drop**(단 `kind`, `original_text=""`는 유지) → `_store().save_structured_request(save_dict)` → `json_payload(tool_result("save_structured_request", ok=True, **result))`. result에는 `request_id/kind/saved_rows/shared_sync` 포함. **Pydantic 재생성·래퍼 저장 금지.**
- `list_saved_requests`: `_store().list_saved_requests(kind, date_from, date_to)` → `rows`(없으면 `[]`).
- `get_saved_request`: `_store().get_saved_request(request_id)` → `row`(없으면 `None`, `ok=True` 유지).
- `personal_list_saved_schedules`: `kind = kind or "personal_schedule"`, `_store().list_schedules(limit, kind, date_from, date_to)` → `filters`+`schedules`.
- **검증(E2E)**: "내일 10시 개인 코칭 저장해줘" → trace에 `extract_schedule_request`→`save_structured_request`, `saved_rows`에 schedules row. "내 일정 보여줘" → `personal_list_saved_schedules` 노출. 앱 재시작/새 대화에도 유지 → **메인과제 완료**.

### M2 — 수정 + 삭제 (심화)
`_delete_saved_schedules`, `personal_update_saved_schedule`, `personal_delete_saved_schedules`, `delete_saved_schedules_dict`.
- `_delete_saved_schedules`: 조건 없음(모든 필터 falsy, `schedule_ids=[]` 포함)이고 `delete_all=False`면 **`ok=False, error=..., deleted_count=0`**. `delete_all`→`store.delete_all_schedules()`, 아니면 `store.delete_schedules_by_filter(...)`. `deleted_count/filters/deleted` dict 반환.
- `personal_update_saved_schedule`: `_store().update_schedule(...)` 그대로 전달(None=변경 안 함, store가 처리). `None`이면 `ok=False`, 아니면 `updated_schedule=result["schedule"]`+`shared_sync=result["shared_sync"]`.
- `personal_delete_saved_schedules`: `_delete_saved_schedules(store=_store(), ...)` 위임 후 `json_payload`.
- `delete_saved_schedules_dict`: `app_store or _store()` 매핑 → `_delete_saved_schedules`(dict 반환, 테스트/직접 호출용).
- **검증(E2E)**: 목록에서 `schedule_id` 확보 → "14시로 바꿔줘" 업데이트(`updated_schedule.start_time` 변경+`shared_sync`) → "삭제해줘" `deleted_count=1` → 재조회 시 사라짐. 조건 없는 삭제는 `ok=False`.

### M3 — Week 1 호환 이중 기록 + 레거시 정규화 (심화)
`structured_request_from_week01_schedule`, `personal_create_schedule`(호환), `unwrap_legacy_payload`, `_save_input_from`, `save_structured_request_payload`.
- `structured_request_from_week01_schedule`: Week1 dict → `SaveStructuredRequestInput(kind="personal_schedule", ..., members=attendees or [], source_schedule_id=schedule["id"])`. `"미정"`/빈 시간 → `None` 정규화. (builder helper이므로 모델 직접 생성 허용.)
- `personal_create_schedule`(@tool, week03_tools에서 Week1 tool 대체): `week01_personal_create_schedule.invoke({...})` → `json.loads` → `structured_request_from_week01_schedule` → `save_structured_request(sr.model_dump(exclude_none=True))` → `created`+`structured_request`+`sqlite_save` 반환. `source_schedule_id`로 재저장 멱등.
- `unwrap_legacy_payload`(`model_validator(mode="before")` classmethod): dict에 `payload`/`structured_request` 래퍼 있으면 한 겹 풀고, 아니면 **passthrough**(평평한 dict는 그대로 → 정상 경로 안 깨짐).
- `_save_input_from`: `SaveStructuredRequestInput`→그대로 / `StructuredRequest`→`model_validate(model_dump())` / `dict`→`model_validate` / `str`→`json.loads` 시도(dict면 검증, 실패=자연어→`extract_structured_request` 후 검증).
- `save_structured_request_payload`: `_save_input_from` → `(store or _store()).save_structured_request(inp.model_dump(exclude_none=True))`.
- **검증**: 읽기전용 스크립트로 자연어/평평한 dict/`{"payload":{...}}`/`StructuredRequest` 모두 `request_id` 반환, 동일 Week1 id 재생성 시 `already_exists`.

**순서**: M0(부트스트랩) → M1(메인) → M2(수정/삭제) → M3(호환/레거시). M0가 모든 in-app 검증을 언락, M1이 메인 과제 독립 완료, M2·M3는 M0 스캐폴딩+M1 데이터 위에서 가산.

---

## 3. 기술적 예외 상황 + 극복 전략

- **(a) Pydantic V2 호환** — `unwrap_legacy_payload`는 `mode="before"`+`@classmethod`+**passthrough 기본**(일반 경로 안 깨짐). `SaveStructuredRequestInput`은 `StructuredRequest` 상속(필드 재정의 최소). store 페이로드는 `model_dump(exclude_none=True)`로 None 제거, 호출자 에코는 `model_dump()`. **tool 본문에서 모델 생성 금지**, builder helper에서만 생성.
- **(b) SQLite FK 제약** — `schedules.request_id` → `structured_requests.request_id` FK. 부모→자식 삽입 순서와 트랜잭션은 **store가 단일 `with self.connect()` 안에서 소유**. 우리는 평평한 dict 하나만 넘기고 `schedules`를 직접 쓰지 않는다. 중간 실패는 원자적 롤백.
- **(c) 싱글톤 미대입 버그** — 현재 `build_week03_agent`의 `if _WEEK03_AGENT is None:` 블록이 `...`뿐이라 `None` 반환→러너 크래시. M0에서 `global _WEEK03_AGENT` 대입으로 해결.
- **(d) 삭제 전체 안전 가드** — store는 필터 없으면 `[]`(="0건 삭제, ok")를 주지만, `_delete_saved_schedules`가 **명시적으로 `ok=False` 거부**해야 모호한 "지워줘"가 조용히 성공/전삭으로 읽히지 않음. 전체 삭제는 `delete_all=True`만.
- **(e) 멱등성(`source_schedule_id`)** — 호환 `personal_create_schedule`의 이중 기록은 Week1 `id`를 `source_schedule_id`로 실어 store가 기존 row 감지 시 `already_exists:True` 조기 반환(중복 방지). 매핑 누락 시 멱등성 깨짐.
- **(f) `shared_sync` 보존** — save/update가 반환하는 `shared_sync`(dict|None)를 tool 출력에 **그대로 노출**(합성·삭제 금지). Week5/6가 외부 복사본에 의존. `already_exists`/`delete_all` 경로는 `None`이 정상.
- **(g) None 제외** — save dict 조립 후 None drop(단 `kind`, `original_text=""` 유지)해 `raw_json`에 `null` 오염 방지. 모델 소스는 `model_dump(exclude_none=True)`.
- **(h) 입력 분기(`_save_input_from`)** — 검사 순서 `SaveStructuredRequestInput`→`StructuredRequest`→`dict`→`str`. `str`은 `try: json.loads`; dict면 구조화 입력, 실패/비-dict면 자연어→`extract_structured_request`(LLM 호출은 이 분기에서만, dict/JSON 경로에선 호출 금지). JSON이 list/scalar면 자연어/무효 취급.

---

## 4. 검증 계획

**정적 (읽기전용)**
- `python -c "import student_parts.week03_build_nanas_logbook"` — 컴파일/문법/데코레이터/미정의명 점검.
- `python -c "from student_parts.week03_build_nanas_logbook import week03_system_prompt; print(week03_system_prompt())"` — 두 프롬프트 비어있지 않고 누적 확인.
- `week03_tools()`가 이름 교체된 `personal_create_schedule` + Week3 tool 7종을 예외 없이 반환.

**E2E (`./run.sh --week3`, 가이드 발화 그대로)**
1. "내일 10시 개인 코칭 저장해줘" → `extract_schedule_request`→`save_structured_request`, `saved_rows`에 schedules row + `request_id`.
2. "내 일정 보여줘" → `personal_list_saved_schedules`에 노출 + `schedule_id`.
3. 앱 재시작/새 대화 → 동일 일정 유지(**영속성 = 메인과제 완료**).
4. (심화) "코칭 14시로 바꿔줘" → `personal_update_saved_schedule` `start_time` 변경+`shared_sync`.
5. (심화) "그 일정 삭제해줘" → `personal_delete_saved_schedules` `deleted_count=1`, 재조회 시 소멸.
6. (가드) 조건 없는 삭제 → `ok=False`.
7. (레거시) 자연어/dict/`{"payload":{...}}`/`StructuredRequest` 저장 모두 `request_id`, 동일 id 재생성 `already_exists`.

---

## 5. 산출물 · 작업 규약

- **plan.md**: (이 문서) 승인된 최종 계획.
- **dev-log.md**: claude.md 개발 원칙 #6/#7에 따라 단계 종료 시마다 체크리스트 `[x]` 갱신 + 발생한 모든 에러/해결 흐름 기록.
- **편집 대상**: `student_parts/week03_build_nanas_logbook.py` 단 하나. `fixed/**`, `week01`, `week02`, `run.sh`는 손대지 않는다(주변 Pydantic V2 스타일 100% 모방).

---

## 편집할 핵심 파일
- `student_parts/week03_build_nanas_logbook.py` — 유일한 구현 대상(본문 채우기).
- (읽기 참조) `fixed/app_store.py`, `student_parts/week02_structure_natural_language_requests.py`, `student_parts/week01_wake_up_nana.py`, `fixed/config.py`, `fixed/llm.py`.
