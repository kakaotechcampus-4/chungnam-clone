# Week 02 구현 계획 — 자연어 요청 구조화

## 목표
자연어 요청(또는 Week1 tool JSON)을 `StructuredRequestBatch`로 변환하는 LangChain agent를 완성한다.
데이터 저장은 하지 않고, 구조화된 Batch 객체 반환만 목적이다.

## 구현 대상 (파일: `student_parts/week02_structure_natural_language_requests.py`)

### 1. `StructuredRequest` 스키마
CLAUDE.md의 Hard constraint 규격 그대로 구현. 모든 필드에 한국어 `description` 부착.

| 필드 | 타입 | 기본값 |
|---|---|---|
| kind | `RequestKind` (Literal) | — (필수) |
| title | `str \| None` | None |
| date | `str \| None` | None (확실할 때만 YYYY-MM-DD) |
| start_time | `str \| None` | None (확실할 때만 HH:MM) |
| end_time | `str \| None` | None (확실할 때만 HH:MM) |
| members | `list[str]` | `default_factory=list` |
| priority | `str \| None` | None |
| reason | `str \| None` | None |
| original_text | `str` | "" |

- 가이드 주석은 title도 `str | None`(기본 None)을 요구하므로 이를 따른다.
  (CLAUDE.md 표에는 title이 str로 되어 있으나, 파일 내 구현 TODO 지시가 더 구체적이라 우선.)

### 2. `StructuredRequestBatch` 스키마
- `requests: list[StructuredRequest]` — `default_factory=list`. 요청이 하나여도 list 유지.
- `base_date: str` — `default_factory=current_app_date_iso`. 상대 날짜 해석 기준일.
- 두 필드 모두 한국어 description 부착.

### 3. `week02_tools()`
- `week01_tools()`를 그대로 반환 (Week1 자산 누적 상속).

### 4. `week02_prompt_parts()`
- `week01_prompt_parts()` 위에 append:
  - Week2 구조화 agent 역할 + 현재 날짜(`current_app_date_iso()`) 기준 명시
  - 자연어를 StructuredRequest 필드(kind/title/date/start_time/end_time/members 등)로 구조화
  - 모르는 값 억지 생성 금지 → None / 빈 리스트
  - Week1 tool JSON을 받은 경우 tool 재호출 없이 payload(created_schedule) 읽어 구조화
  - Week2에서는 SQLite 저장 / RAG / 외부 멤버 일정 조율 하지 않음 명시

### 5. `week02_system_prompt()`
- `join_system_prompt(...)`로 `week02_prompt_parts()` + 최종 답변 규칙 결합:
  - 최종 답변은 `StructuredRequestBatch` 형식
  - 요청이 하나여도 requests 목록에 담기
  - personal_create_schedule 결과 JSON의 created_schedule 읽어 필드 채우기

### 6. `build_week02_agent()` + 실행기 엔트리 포인트 연결
- `CONFIG.has_openai_key` 없으면 `RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")`
- 전역 `_WEEK02_AGENT` 재사용, 없을 때만 생성
- `create_agent(model=chat_model(), tools=week02_tools(), response_format=StructuredRequestBatch, system_prompt=week02_system_prompt())`
- **엔트리 포인트 확인**: 런타임 실행기(`run.sh`)가 호출하는 표준 함수는 `build_week_agent()`이다.
  현재 파일 하단(178~181행)에 `build_week_agent()`가 이미 `return build_week02_agent()`로 구현되어 있으므로,
  이 연결이 그대로 유지되는지 확인한다(신규 구현 아님, 수정하지 않음). 구현한 agent가 실행기로 정상 노출되는지 보장.

## 건드리지 않는 것 (메인과제 당시 기준)
- ~~예약 함수(`_coerce_structured_request`, `extract_structured_request`, `extract_schedule_request`)는 이후 회차용이므로 `...` 유지.~~ → 아래 "심화 과제" 섹션에서 구현.
- Week1 파일은 수정하지 않음.

## 검증
### 정적 테스트
1. `python -c "import ..."` 로 문법/import 확인
2. 스키마 인스턴스화 및 필드 description 존재 확인
3. (키 없으면) build 시 RuntimeError 확인

### 통합(E2E) 테스트 — 가이드 명시 시나리오
4. `./run.sh --week2` 실행 후 `"다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"` 입력 →
   최종 답변이 `StructuredRequestBatch` 형식의 `structured_response`로 파싱되는지 확인.
   - 기대: `requests`에 `StructuredRequest` 1건(kind=`personal_schedule` 또는 `group_schedule`,
     date=다음 주 화요일 YYYY-MM-DD, start_time=`15:00`, members에 "철수"), `base_date`=기준일.
   - 제약: 이 단계는 실제 LLM 호출이라 `.env`의 `PROXY_TOKEN`이 필요하다.
     키가 없는 환경에서는 실행 절차만 명시하고, 실제 통과 확인은 사용자 실행에 맡긴다.

---

## 심화 과제 — 구조화 Bridge 함수 구현 (2026-07-11 시작)

### Context
메인과제는 완료·커밋(`052d167`)됨. 오늘 강사 base code 동기화로 같은 파일에 심화 과제 TODO 3개가 추가됨.
이 함수들은 **에이전트 전체 루프를 돌리지 않고** 자연어/JSON을 단일 `StructuredRequest`로 강제 변환해
Week 3+ 저장 tool(`fixed/app_store.py:281 save_structured_request`)로 넘기는 bridge 역할만 한다.
`save_structured_request(payload)`가 읽는 키(`kind/title/date/start_time/end_time/members/priority/reason`)는
`StructuredRequest.model_dump()`와 정확히 일치 — 그대로 넘기면 다운스트림이 바로 소비 가능.

### 대상 파일 (단일, 신규 import 불필요)
`student_parts/week02_structure_natural_language_requests.py` — 스텁 3개(L212 `_coerce_structured_request`,
L221 `extract_structured_request`, L230 `extract_schedule_request`)만 채운다.

### 구현
1. **`_coerce_structured_request(value)`**: `StructuredRequest`면 그대로, `dict`면 `model_validate(...)`,
   그 외 타입은 `RuntimeError`.
2. **`extract_structured_request(text)`**: `create_agent` 사용 금지. 오직
   `chat_model().with_structured_output(StructuredRequest, method="function_calling")`만 사용.
   메시지는 `[("system", join_system_prompt(week02_prompt_parts())), ("human", text)]` 튜플 리스트로 전달
   (Message 객체 대비 추가 import 불필요, 동작 동일). 결과를 `_coerce_structured_request(...)`로 정규화.
3. **`extract_schedule_request(query)`** (기존 `@tool`): `extract_structured_request(query)` 호출 →
   `{"ok": True, "tool_name": "extract_schedule_request", "base_date": current_app_date_iso(),
   "structured_request": structured.model_dump()}` → `json.dumps(..., ensure_ascii=False)`.

### 건드리지 않는 것
- 메인과제 코드(스키마, `week02_tools`/`week02_system_prompt`/`week02_prompt_parts`, `build_week02_agent`).
- Week1 파일, `fixed/` 파일.

### 검증
- **정적** (LLM 키 불필요, `chat_model()` 미호출 경로만): import/와이어링 확인,
  `_coerce_structured_request`에 dict/동일 객체/잘못된 타입(`None`,`123`,`"x"`,`[]`) 입력 시 각각 기대 동작·`RuntimeError` 확인.
- **라이브 E2E** (`.env`의 `PROXY_TOKEN` 필요): `extract_schedule_request.invoke({"query": "내일 오후 3시에 철수랑 회의 잡아줘"})` →
  결과 JSON에 `ok/tool_name/base_date/structured_request`(9개 필드, `kind`는 RequestKind 값) 확인.
  키 없는 환경에서는 정적 검증만 수행.

---

## 버그 수정 — "다음 주 X요일" 날짜 계산 오류 (2026-07-11)

### 발견 경위
`./run.sh --week2` 라이브 E2E에서 "내일 오전 10시에 헬스장... 그리고 다음 주 화요일 오후 3시에 영희랑 개발자 미팅"을 입력.
base_date `2026-07-11`(토요일) 기준 "다음 주 화요일"의 정답은 `2026-07-14`인데, agent가 `2026-07-21`(한 주 밀림)로
계산해 `personal_create_schedule` tool 호출과 최종 `structured_response` 양쪽에 오답이 전파됨.

### 원인
week02는 week01의 prompt(`week01_prompt_parts()`)를 그대로 상속하는데, 거기서 이미 "기준 날짜 기반으로 직접 계산해서
tool을 호출하라"고 지시함. `fixed/runtime_clock.py`에 정확한 계산용 `next_weekday_iso()` 헬퍼가 있었지만
week01 데모 시드에서만 쓰였고, LLM에는 노출되지 않아 LLM이 산수를 직접 해야 했음(실수 발생).

### 조치 (메인과제 코드 수정 — 사용자 승인 후 진행)
- `week02_structure_natural_language_requests.py`에 `next_weekday_iso` import 추가.
- 신규 tool `resolve_next_week_weekday_date(weekday: str)` 추가: 한국어(화/화요일)·영어(tue/tuesday) 요일 표현을
  `_WEEKDAY_ALIASES`로 정규화해 `next_weekday_iso(index)` 호출, `{ok, tool_name, weekday, date}` JSON 반환.
  알 수 없는 요일이면 `{ok: false, error}`.
- `week02_tools()` = `[*week01_tools(), resolve_next_week_weekday_date]`로 갱신.
- `week02_prompt_parts()`에 "'다음 주 + 요일'은 직접 계산하지 말고 이 tool을 호출해 받은 날짜를 쓴다" 지시 추가.

### 검증
- 정적: `resolve_next_week_weekday_date.invoke({"weekday": "화요일"})` / `"화"` → `2026-07-14` 확인, 잘못된 요일 입력 → `ok: false` 확인.
- 라이브 E2E: 위 재현 시나리오를 `build_week02_agent()`로 재실행 → `개발자 미팅` date가 `2026-07-14`로 정정됨 확인.

---

## 버그 수정 — 한 메시지 내 이종(異種) 요청 혼합 시 일부 누락 (2026-07-11)

### 발견 경위
"오늘 저녁 9시에 철수랑 마케팅 미팅 잡고, 내일 아침 8시에 영양제 먹기 알림 등록해줘. 이번 주 금요일까지 독후감 제출하는
할 일도 추가해줘." 라이브 테스트에서 `personal_create_schedule`이 마케팅 미팅/영양제 먹기 알림 2건에 대해 정확히
호출됐음에도, 최종 `structured_response.requests`에는 `todo`(독후감 제출) 1건만 남고 나머지 2건이 누락됨.
**세션 내 여러 turn 누적이 원인이라는 가설은 기각** — 완전히 독립된 새 세션의 단일 turn에서도 동일하게 재현되어,
"한 메시지 안에 이종(personal_schedule/reminder/todo 등)의 요청이 섞였을 때" 모델이 tool로 이미 처리한 요청을
최종 구조화 목록에서 재수록하지 않는 prompt-following 문제로 특정됨. 반면 동종(personal_schedule 2건) 혼합
케이스("헬스장+개발자 미팅")는 항상 정상 동작.

### 조치 (2단계, 모두 `week02_prompt_parts()`에 추가)
1. 1차: "tool 호출 여부와 무관하게 감지된 모든 요청을 requests에 빠짐없이 포함하라"는 규칙 문장만 추가.
   → 재현 시나리오 3회 실행 중 2회 정상, 1회 여전히 누락(개선됐으나 비결정적).
2. 2차: 위 규칙 바로 뒤에 **동일 재현 시나리오를 그대로 사용한 구체적 few-shot 예시** 추가
   (마케팅 미팅=personal_schedule, 영양제 먹기 알림=reminder, 독후감 제출=todo, 총 3건이 모두 들어가야
   정답이라고 명시하고, "tool로 처리했다고 2건을 빼면 틀린 답"이라고 명시적으로 오답 예시까지 제시).
   → 재현 시나리오 5회 연속 실행 모두 3건 전부 정상 포함, kind 분류도 예시와 동일하게 정확.

### 참고
- 프록시 모델이 tool-calling 기반 구조화 출력을 완벽히 결정론적으로 따르지 못하는 한계가 있어(기존
  `build_week02_agent()`의 ToolStrategy 관련 주석 참고), 프롬프트 보강만으로 100% 결정론을 보장하긴 어려울 수
  있음. 다만 구체적 few-shot 예시 추가 후 표본상(5/5) 안정화됨을 확인.
- "이번 주 금요일"(이미 지난 요일)은 여전히 LLM이 자유 계산(2026-07-17, 사실상 "다음 주 금요일"과 동일)하며,
  `resolve_next_week_weekday_date`처럼 결정론적 tool로 옮기지는 않음 — 별도 요구 전까지 현행 유지.

### 검증
- 라이브 E2E: 재현 문장을 `build_week02_agent()`로 5회 연속 실행 → 매번 3건(personal_schedule/reminder/todo) 모두
  포함, "헬스장+개발자 미팅"(동종 2건) 시나리오 3회 재실행으로 회귀 없음 확인.

---

## 버그 수정 — bridge(`extract_structured_request`)의 "다음 주 + 요일" 재발 위험 (2026-07-13)

### 발견 경위
Claude Code와 코드 리뷰 중 `extract_structured_request` 내부의
`chat_model().with_structured_output(StructuredRequest, method="function_calling")` 호출을 점검하다가,
이 호출에는 어떤 tool도 바인딩되어 있지 않다는 사실을 확인함. 그런데 이 호출의 system 메시지
(`join_system_prompt(week02_prompt_parts())`)에는 위 "다음 주 X요일 날짜 계산 오류 (2026-07-11)" 조치에서
추가한 "다음 주 + 요일이 나오면 `resolve_next_week_weekday_date` tool을 호출하라"는 지시가 그대로 포함되어 있음.

### 원인
- `extract_structured_request`는 심화 과제 하드 제약(CLAUDE.md)상 "create_agent 루프를 새로 만들지 않고
  with_structured_output만 단독 사용"해야 하므로, 애초에 tool을 바인딩할 수 없는 구조임.
- `week02_prompt_parts()`는 메인 agent(`build_week02_agent()`)와 bridge(`extract_structured_request`)가
  공유하는 함수라서, 메인 agent에만 유효한 "tool을 호출하라"는 지시가 tool이 없는 bridge 호출에도 그대로 주입됨.
- 결과적으로 bridge 경로에서는 모델이 실행할 수 없는 tool을 지시받은 채로 남아, 결국 직접 날짜 산수를 하게 됨 —
  2026-07-11에 메인 agent에서 고쳤던 것과 같은 종류의 "다음 주 + 요일" 오계산이 bridge 경로에서는
  그대로 재발할 수 있는 상태였음.

### 조치 (`student_parts/week02_structure_natural_language_requests.py` 수정)
- `_NEXT_WEEK_WEEKDAY_PATTERN` regex와 `_next_week_weekday_hints(text)` 헬퍼를 추가. 기존
  `_WEEKDAY_ALIASES` dict와 `next_weekday_iso()`(`resolve_next_week_weekday_date`가 쓰는 것과 동일한 헬퍼)를
  그대로 재사용해 "다음 주 + 요일" 표현을 텍스트에서 찾아 정확한 날짜를 파이썬 코드로 미리 계산.
- `extract_structured_request(text)`가 `with_structured_output`을 호출하기 전에, 위 헬퍼로 찾은 힌트가
  있으면 `week02_prompt_parts()` 결과 리스트에 "이 표현은 이미 정확히 계산되어 있으니 그대로 date 필드에
  써라"는 문장을 append한 뒤 `join_system_prompt(...)`으로 결합해 system 메시지로 사용. 힌트가 없으면 기존과
  동일하게 `join_system_prompt(week02_prompt_parts())` 그대로 사용됨(하위 호환).
- 이 방식은 tool을 새로 바인딩하거나 agent loop를 도입하지 않는 순수 문자열 전처리 + 프롬프트 주입이라,
  "with_structured_output만 단독 사용" 하드 제약을 그대로 지킴.

### 알려진 한계 (의도적으로 미해결, 2026-07-13 사용자 확인 후 보류)
- regex는 "다음 주 + 요일" 리터럴만 매칭한다. "2주 후 금요일", "3주 후 금요일", "다다음주 화요일" 등은
  매칭 대상이 아니다.
- 더 근본적으로 `fixed/runtime_clock.py`의 `next_weekday_date()`/`next_weekday_iso()` 자체가 "다음 주"로
  1주 고정되어 있고 주차 오프셋 파라미터가 없어서, 설사 regex를 확장해도 계산해 줄 방법이 없다.
- 이 한계는 bridge뿐 아니라 메인 agent가 쓰는 `resolve_next_week_weekday_date` tool에도 동일하게 있다
  (새로 생긴 결함이 아니라 기존 구현 전체에 있던 사각지대). 확장하려면 `next_weekday_date`/`next_weekday_iso`에
  `weeks_ahead` 같은 파라미터를 추가하고 tool·regex 양쪽을 함께 넓혀야 하며, 이번 범위에서는 보류하기로 함.
- 참고로 `extract_structured_request`의 system 메시지가 힌트가 있을 때 `join_system_prompt(week02_prompt_parts())`
  결과에 문장을 하나 더 append하게 되어, 심화 과제 규격 문구("system 메시지는 join_system_prompt(week02_prompt_parts())를
  사용")를 문자 그대로 보면 힌트가 있는 경우엔 그 위에 얹은 확장이다. tool 바인딩·agent loop가 아니므로 하드
  제약 위반은 아니라고 판단했다.

### 검증
- 정적: regex를 독립적으로 테스트해 "다음 주 화요일", "다음주화요일"(공백 없음), "다음 주 월요일과 다음 주
  금요일"(한 문장 2건) 모두 전체 요일 토큰("화요일" 등, "화"로 잘리지 않음)으로 정확히 매칭되고, "오늘 저녁
  회의"처럼 패턴이 없는 문장에서는 매칭이 없음을 확인.
- 라이브 E2E는 수행하지 않음(`.env`의 `PROXY_TOKEN` 필요, 이번 세션에서는 미실행) — 기존 관례와 동일하게
  라이브 검증은 실행 환경이 있는 사용자 몫으로 남긴다.
