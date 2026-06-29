# Week 1 — 개인 일정 CRUD tool 구현 plan

대상 파일: [`student_parts/week01_wake_up_nana.py`](../student_parts/week01_wake_up_nana.py)
작업 브랜치: `yoojongho/week1`

> 이 문서는 **구현 이전에 세운 계획**만 담는다.

---

## 1. 배경 (Context)

Kanana Week 1 미션은 LangChain agent(Nana)가 사용자의 "내 일정 만들어줘 / 보여줘 / 지워줘"
요청을 받아 `personal_create_schedule`, `personal_list_schedules`, `personal_delete_schedule`
3개 `@tool` 중 적절한 것을 골라 호출하도록 만드는 것이다. 일정은 앱 DB가 아니라 **현재 대화 전용
임시 메모리**(`PERSONAL_SCHEDULES`)에 저장한다.

LLM의 **tool 선택**과 **인자 추출**은 전적으로 각 `@tool`의 *이름 + description*과 agent의
*system prompt*(`week01_prompt_parts()`, `CHAT_MEMORY_PROMPT`)가 결정한다. 본문(body) 로직은
파일 상단 `[수강생 구현 가이드]`에 이미 명세돼 있어 기계적이다.

이 계획의 핵심은 description / prompt를 **예시가 정의한 단일 구조만** 따르도록 작성해서 아래 3개
평가기준을 충족시키는 것이고, 그 작성을 자유롭게 풀어쓰지 않고 **고정 템플릿 + 체크리스트**로
강제(constrain)하는 것이다.

### 평가기준
1. **tool 선택** — 질문에 필요한 tool이 제대로 선택되는가
2. **인자 추출** — function calling에서 필요한 인자가 제대로 추출되는가
3. **다중 호출** — 한 질문에 대해 tool이 중복(여러 번) 호출 가능한가

### 설계 결정
- 다중 호출(기준 3) 유도는 **system prompt에만** 넣는다. description은 예시 구조를 순수하게 유지.
- `CHAT_MEMORY_PROMPT`도 작성한다 (현재 대화 전용 임시 일정 기억 규칙).
- 강제 방식은 **고정 템플릿 + 체크리스트**.

### 수정 범위
`student_parts/week01_wake_up_nana.py` 한 파일만 수정한다. `fixed/` 등 제공 코드는 읽기만 한다.

| 위치 | 작업 |
| --- | --- |
| `CHAT_MEMORY_PROMPT` | 현재 대화 전용 기억 규칙 문자열 작성 |
| `@tool personal_create_schedule` | 데코레이터 description 추가 + 본문 구현 |
| `@tool personal_list_schedules` | 데코레이터 description 추가 + 본문 구현 |
| `@tool personal_delete_schedule` | 데코레이터 description 추가 + 본문 구현 |
| `week01_prompt_parts()` | system prompt 조각 리스트 작성 |

---

## 2. description 고정 템플릿 (강제 규칙 — 기준 1·2)

예시 구조:
`description="개인 일정을 생성한다. date는 YYYY-MM-DD, start_time은 HH:MM 형식이다."`

이를 단 하나의 슬롯 템플릿으로 고정한다. **모든 tool description은 이 형태만 쓴다.**

```
[동작 1문장(평서체, ~한다)]. [인자A]는 [형식/제약], [인자B]는 [형식/제약] 형식이다.
```

| 규칙 | 내용 |
| --- | --- |
| DO | `@tool` 데코레이터를 `@tool("<tool_name>", description="...")` 형태로 바꾸고 명시적 이름을 첫 인자로 |
| DO | 동작 문장 정확히 1문장, `~한다` 평서체 → 기준 1 신호 |
| DO | 그 뒤 인자별 형식/제약을 `… 형식이다.` 한 문장으로 → 기준 2 신호 |
| DON'T | 다중 호출/예시/주의/재시도 등 부가 설명을 description에 넣지 않음 (system prompt 담당) |
| DON'T | 줄바꿈·불릿·코드블록 금지. 평서문 2문장 이내 |

명시적 `description=`을 주면 기존 docstring은 무시되므로 docstring은 그대로 둬도 무방.

### 슬롯 채움 (작성 대상)

- `personal_create_schedule`
  "개인 일정을 현재 대화의 임시 메모리에 생성한다. date는 YYYY-MM-DD, start_time과 end_time은 HH:MM, attendees는 문자열 리스트 형식이다."
- `personal_list_schedules`
  "현재 대화의 개인 일정을 날짜 범위로 조회한다. date_from은 시작일, date_to는 종료일이며 둘 다 YYYY-MM-DD 형식이다."
- `personal_delete_schedule`
  "개인 일정 ID로 현재 대화의 일정 하나를 삭제한다. schedule_id는 personal_ 접두어가 붙은 임시 일정 ID 형식이다."

---

## 3. system prompt (기준 3 + 1·2 보강)

`week01_prompt_parts()`가 반환하는 문자열 조각 리스트에 다음을 담는다.

- **역할 + 현재 날짜**: `current_app_date_iso()`를 문장에 삽입.
- **`CHAT_MEMORY_PROMPT`**: 일정은 현재 대화에서만 유지되는 임시 메모리이며 다른 대화의 일정은 보거나 지울 수 없다는 범위 규칙.
- **tool 매핑 규칙 (기준 1)**: 생성→create, 조회→list, 삭제→delete.
- **인자 규칙 (기준 2)**: 상대 날짜("내일"·"다음 주 화요일")→YYYY-MM-DD, 시간→HH:MM 변환. 미제공 인자는 지어내지 않음.
- **다중 호출 규칙 (기준 3, *여기에만*)**: "한 요청에 여러 일정이 있으면 일정 수만큼 tool을 여러 번 호출한다."

---

## 4. 본문 구현 (가이드 명세대로)

재사용 helper: `_new_personal_id()`, `_now_iso()`, `_json()`, `current_session_scope()`
([`fixed/session_scope.py`](../fixed/session_scope.py)), `_current_session_schedules()`, `_schedule_scope()`.

- **create**: id/title/date/start_time/end_time/attendees(None→[])/created_at/session_id로 dict 구성 → `append` → `created_schedule` 반환.
- **list**: `_current_session_schedules()`에서 `date_from`/`date_to`를 YYYY-MM-DD 문자열 비교로 필터 → `schedules` 반환 (원본 미수정).
- **delete**: 현재 대화 범위 + `schedule_id` 일치 항목만 `PERSONAL_SCHEDULES[:] = [...]`로 제거 → 길이 차로 `deleted` 산출. 다른 대화 범위 같은 ID는 보존.

반환 JSON top-level 키: `created_schedule` / `schedules` / `deleted` 유지.

---

## 5. 강제 장치 — self-verify 체크리스트

작성 직후 아래 매핑 체크리스트를 통과해야 한다(통과 못 하면 description/prompt 재작성).

- [ ] 3개 description이 모두 슬롯 템플릿만 따른다 (줄바꿈·불릿·다중호출 문구 없음)
- [ ] 기준 1: 동작 문장이 생성/조회/삭제를 명확히 구분
- [ ] 기준 2: 인자 형식(YYYY-MM-DD, HH:MM, 리스트, ID 접두어)이 description/prompt에 명시
- [ ] 기준 3: "여러 일정 → tool 여러 번 호출" 지시가 system prompt에만 존재
- [ ] 반환 JSON top-level 키(`created_schedule`/`schedules`/`deleted`) 유지

---

## 6. 검증 계획 (실행 방법)

```bash
./run.sh --week1     # .env의 PROXY_TOKEN 필요
```

앱 실행 후 채팅에 아래 프롬프트를 넣고 상세 trace에서 호출된 tool·인자·결과 payload를 확인한다.

| 시나리오 | 입력 예시 | 확인 포인트 |
| --- | --- | --- |
| 단일 생성 | "내일 오후 2시에 회의 일정 잡아줘" | create 1회 · date=YYYY-MM-DD · start_time=HH:MM (기준 1·2) |
| 다중 생성 | "내일 오전 10시 회의랑 오후 3시 운동, 둘 다 잡아줘" | create가 **2회** 호출 (기준 3) |
| 조회 | "내 일정 다 보여줘" | list 호출 · `schedules` 배열 |
| 삭제 | "<생성된 id> 일정 지워줘" | delete 호출 · `deleted` 값 |
| 대화 범위 격리 | 다른 대화에서 "내 일정 보여줘" | 이전 대화 일정이 보이지 않음 |
