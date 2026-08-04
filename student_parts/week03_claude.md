# 작업 목표

`student_parts/week03_build_nanas_logbook.py`에서 다음 두 가지를 완성한다.

1. `WEEK03_TOOL_CALL_PROMPT`: Week 3 agent가 자연어 구조화 → SQLite 저장과 조회/수정/삭제 tool을
   어떤 순서·조건으로 호출할지 알도록 만드는 system prompt 조각.
2. `save_structured_request` 함수 본문: `extract_schedule_request`가 만든 구조화 결과를 실제로
   SQLite에 저장하는 Week 3 메인과제 핵심 tool.

# 수정 범위

- 수정 대상은 `WEEK03_TOOL_CALL_PROMPT = ""`와 `save_structured_request(...)` 함수 본문 두 곳뿐이다.
- `SaveStructuredRequestInput`(그 안의 `unwrap_legacy_payload` 검증기 포함), `_save_input_from`,
  `save_structured_request_payload`, 그 외 tool 함수 본문, `week03_prompt_parts()`의 나머지
  `# TODO` 등 다른 부분은 이번 작업 범위가 아니다. 건드리지 않는다.

# 하지 말아야 할 것

- Week 4 이상(RAG, 외부 멤버 일정 조율 등) 범위의 지시를 섞지 않는다.
- `save_structured_request` 본문에서 `SaveStructuredRequestInput`이 이미 정의한 검증 규칙(필드
  패턴, 시간 순서, unknown-kind-reason 등)을 별도 로직으로 새로 작성하지 않는다. 이 tool이 일반
  함수처럼 `args_schema` 검증을 거치지 않고 직접 호출될 가능성에 대비할 때도, 새 규칙을 만들지
  말고 `SaveStructuredRequestInput` 자체로 인자를 검증한다.
- `WEEK03_TOOL_CALL_PROMPT`(또는 그 아래 이어지는 다른 prompt 조각)에 아직 본문이 `# TODO`인 tool
  (`list_saved_requests`, `get_saved_request`, `personal_update_saved_schedule`,
  `personal_delete_saved_schedules` 등)을 "이런 상황에는 이 tool을 호출한다"는 식으로 적지 않는다.
  이 tool들은 `week03_tools()`에 이미 노출돼 있어 agent가 언제든 고를 수 있으므로, 아직 구현되지
  않은 tool을 prompt로 추천하면 agent가 그 tool을 호출하고 깨진 결과를 그대로 사용자에게 전달한다.
  해당 tool을 실제로 구현한 뒤에 그 사용 상황을 prompt에 추가한다.

# WEEK03_TOOL_CALL_PROMPT 명세

다음 내용이 모두 드러나는 system prompt 조각으로 작성한다.

- 저장 순서: 새 일정/할 일/알림을 저장할 때는 반드시
  ① `extract_schedule_request(query=...)`로 자연어를 구조화한 뒤,
  ② 그 결과의 `structured_request` 필드 값을 그대로 `save_structured_request`의 인자로 전달해 저장한다.
  (자연어 문자열이나 `ok`/`tool_name`/`base_date` wrapper를 그대로 저장하지 않는다는 원칙을 여기서
  agent 지시로 드러낸다.)
- 저장 요청 키워드: "일정 등록해줘", "기억해줘", "저장해줘", "메모해줘" 같은 표현을 저장 의도의 단서로
  삼는다.
- 조회: "내 일정 보여줘" 같은 질문에는 `personal_list_saved_schedules`를 호출해 답한다.
- 미구현 tool 안내: `personal_update_saved_schedule`/`personal_delete_saved_schedules`(수정·삭제)와
  `list_saved_requests`/`get_saved_request`(request 단위 조회)는 "추가 과제" 항목이라 본문이 아직
  `# TODO`로 남아 있다. 이 tool들을 실제로 구현하기 전에는 WEEK03_TOOL_CALL_PROMPT에 "언제 이 tool을
  호출하라"는 지시를 넣지 않는다 — 구현되지 않은 tool을 부르라고 지시하면 agent가 그 tool을 호출하고
  깨진 결과(빈 응답/에러)를 사용자에게 그대로 전달하게 된다. 대신 사용자가 수정/삭제를 요청하면
  아직 지원하지 않는 기능이라고 안내하라는 지시만 넣는다.
  (개정) 과거에는 이 항목 대신 "수정/삭제 전 확인"·"삭제 안전 규칙"으로 `personal_update_saved_schedule`/
  `personal_delete_saved_schedules`/`delete_all`을 호출하라고 지시했으나, 정작 이 tool들의 본문은
  구현되지 않은 상태였다. 메인과제 범위에서 실제로 동작하는 tool은 `save_structured_request`/
  `personal_list_saved_schedules`(그리고 Week 2의 `extract_schedule_request`)뿐이므로, 그 두
  tool을 실제로 구현한 뒤에는 이 항목도 그 tool들의 사용 상황만 반영하도록 다시 좁혀야 한다.
  추가 과제로 수정/삭제 tool을 구현한 뒤에는, 그때 가서 이 항목에 그 tool들의 호출 상황을 다시
  추가한다.
- (개정) 같은 이유로, 이 파일의 "추가 과제" `personal_create_schedule`(Week 1 호환, SQLite 이중
  기록용)도 아직 본문이 `# TODO`다. 그런데 이 tool의 docstring은 "Nana의 개인 일정을 생성하고
  Week 3+ 앱 SQLite DB에도 저장합니다"라고 되어 있어, 저장 요청이 들어오면 `extract_schedule_request`
  → `save_structured_request` 2단계 대신 이 tool 하나로 끝내는 게 더 간단해 보인다는 인상을 준다.
  실제로 이 tool이 아직 구현되지 않았는데도 이런 description 때문에 agent가 저장 요청에서 이
  tool을 먼저 고르는 문제가 있었다. 그래서 지금 docstring에는 "아직 구현되지 않았으니 직접
  호출하지 말고 `save_structured_request`를 쓰라"는 경고 문단이 들어가 있다. **이 tool을
  구현하는 시점에는 그 경고 문단을 지우고 정상 동작 설명으로 바꾼다** — 안 지우면 이번엔 반대로
  "구현됐지만 쓰지 말라"는 모순된 지시가 남는다.

# save_structured_request 함수 본문 명세

다음 내용이 모두 드러나도록 구현한다.

- 이 함수는 SQLite DB에 일정/할 일/알림을 등록하는 용도이며, agent가 참고할 수 있는 내용을
  `@tool` docstring에 남긴다.
- `@tool(args_schema=SaveStructuredRequestInput)`로 Week 2 구조화 결과(`kind`/`title`/`date`/
  `start_time`/`end_time`/`members`/`priority`/`reason`/`original_text`, Week 1 호환용
  `source_schedule_id`)를 검증한다.
- tool 본문에서는 이미 검증되어 들어온 함수 인자 값을 바로 저장 dict로 정리한다. 검증 규칙
  자체를 새로 작성하지 않는다.
- 이 tool이 (agent의 `args_schema` 검증 경로를 거치지 않고) 일반 함수처럼 직접 호출될 가능성에
  대비해, 함수 인자들이 `SaveStructuredRequestInput` 스키마를 따르는지 tool 본문에서 한 번 더
  검증하는 절차를 추가한다. 즉 함수 인자로 `SaveStructuredRequestInput`을 구성해 필드 패턴·시간
  순서·unknown-kind-reason 규칙이 지켜지는지 확인한 뒤, 그 검증된 값을 저장 dict의 근거로 쓴다.
- 자연어 문자열이나 `extract_schedule_request`가 반환하는 `ok`/`tool_name`/`base_date` wrapper를
  그대로 저장하지 않는다 — 저장 대상은 `structured_request` 필드 안의 값들뿐이다.
- 검증된 함수 인자를 저장 dict로 만들고 `None` 값을 제외한 뒤 SQLite에 저장한다.
- `ok`/`tool_name`과 저장 결과가 포함된 JSON 문자열을 반환한다.

# 참고자료

- `student_parts/week02_structure_natural_language_requests.py`의 `extract_schedule_request`: 반환
  JSON 모양(`{"ok", "tool_name", "base_date", "structured_request": {...}}`)이 위 저장 순서 지시의
  근거다.
- `student_parts/week03_build_nanas_logbook.py`의 tool 목록(`save_structured_request`,
  `list_saved_requests`, `personal_list_saved_schedules`, `personal_update_saved_schedule`,
  `personal_delete_saved_schedules`): 이 prompt가 호출 순서를 안내해야 하는 실제 대상.
- `student_parts/week03_build_nanas_logbook.py`의 `_store()`/`json_payload()`/`tool_result()`:
  SQLite 접근과 JSON 응답 포맷을 만드는 공용 helper로, `save_structured_request` 본문이 그대로
  재사용한다.
- `fixed/app_store.py`의 `AppSQLiteStore.save_structured_request(payload)`: 실제 저장을 수행하는
  메서드다. `kind`/`title`/`date`/`start_time`/`end_time`/`members`/`priority`/`reason`/
  `source_schedule_id` 키가 있는 dict를 받으므로, tool 본문은 이 키 이름에 맞춰 저장 dict를
  만들어 넘기기만 하면 된다.

# 검증 방법

- `./run.sh --week3`에서 "내일 10시 개인 코칭 저장해줘"를 입력했을 때, trace에서
  `extract_schedule_request` 다음에 `save_structured_request`가 바로 이어 호출되는지 확인한다.
  두 tool 모두 `@tool`로 선언돼 있어 호출되면 각각 독립된 tool run으로 트레이스에 남고,
  `extract_schedule_request` 내부의 `extract_structured_request` → `chat_model().with_structured_output(...)`
  LLM 호출도 그 tool run의 자식 run으로 함께 잡힌다.
- 트레이스에 `extract_schedule_request` 없이 `save_structured_request`만 곧바로 찍혀 있다면, 이는
  트레이스 수집이 안 된 것이 아니라 agent가 `WEEK03_TOOL_CALL_PROMPT`의 순서 지시를 따르지 않고
  `save_structured_request`의 인자(`kind`/`title`/`date` 등)를 스스로 채워 곧장 호출했다는 뜻이다.
  `week03_tools()`는 두 tool을 flat list로 노출할 뿐 호출 순서를 코드로 강제하지 않고, 순서 지시는
  시스템 프롬프트뿐이며 `save_structured_request`의 `args_schema=SaveStructuredRequestInput`이
  필요한 필드를 이미 다 요구하고 있어 agent가 bridge tool 없이도 그 필드를 직접 채워 호출할 수
  있기 때문이다. 이 경로로 저장되면 `extract_structured_request`가 거치는 structured-output 검증
  (시간 순서, unknown-kind-reason 등)을 우회한 값이 그대로 저장될 수 있다.
- `save_structured_request` 호출 결과 JSON에 `ok: true`와 저장된 request/schedule id가 들어
  있는지 확인한다.
- "내 일정 보여줘"를 입력했을 때 `personal_list_saved_schedules`가 호출되는지, 앱을 다시
  시작하거나 새 대화를 열어도 방금 저장한 일정이 그대로 보이는지 확인한다.
- "그 일정 지워줘"류 요청에서 삭제 tool 호출 전에 먼저 조회 tool이 호출되어 후보를 확인하는지
  확인한다.

---

# [추가 작업] build_week03_agent() 구현 가이드

# 작업 목표

`student_parts/week03_build_nanas_logbook.py`의 `build_week03_agent()`를 완성해, Week 1-3 누적
tool을 가진 단일 LangChain agent를 만든다.

# 수정 범위

- 수정 대상은 `build_week03_agent()` 함수 본문(TODO 한 줄)뿐이다.
- `week03_tools()`, `week03_system_prompt()`, `week03_prompt_parts()` 등 이 함수가 사용하는
  다른 함수는 이번 작업 범위가 아니다. 건드리지 않는다.

# 하지 말아야 할 것

- `response_format`을 넘기지 않는다. Week 3 agent는 Week 2처럼 최종 답변을 pydantic
  `structured_response`로 강제해야 하는 agent가 아니라, tool 호출로 SQLite에 저장/조회하고
  자연어로 답하는 대화형 agent이기 때문이다.
- `if _WEEK03_AGENT is None:` 캐싱 가드 바깥에서 `create_agent(...)`를 호출하지 않는다. 가드
  안에서만 생성해야 모듈이 재import되지 않는 한 같은 프로세스에서 agent가 한 번만 만들어진다.

# build_week03_agent() 구현 명세

- `_WEEK03_AGENT is None`일 때만 `langchain.agents.create_agent`로 agent를 만들어
  `_WEEK03_AGENT`에 대입한다.
- `create_agent`에는 다음 인자만 넘긴다.
  - `model=chat_model()`
  - `tools=week03_tools()`
  - `system_prompt=week03_system_prompt()`
- `response_format`은 넘기지 않는다(위 "하지 말아야 할 것" 참고).
- 이미 만들어진 `_WEEK03_AGENT`가 있으면 그 값을 그대로 `return`한다.

# 참고자료

- `student_parts/week01_wake_up_nana.py`의 `build_week01_agent()`: 같은 캐싱 패턴
  (`if _WEEK0N_AGENT is None: ... create_agent(model=chat_model(), tools=..., system_prompt=...)`)
  이면서 `response_format` 없이 `model`/`tools`/`system_prompt` 세 인자만 쓰는 예시다. Week 3도
  구조화 출력을 강제하지 않으므로 이 형태를 그대로 따른다.
- `student_parts/week02_structure_natural_language_requests.py`의 `build_week02_agent()`: 겉보기엔
  비슷한 보일러플레이트지만, Week 2는 최종 답변이 `StructuredRequestBatch`여야 해서
  `response_format=ToolStrategy(StructuredRequestBatch)`를 추가로 넘긴다. 같은 문서 위쪽의
  "[추가 개선] build_week02_agent() response_format 전략 변경" 항목에 그 이유(프록시 모델이
  provider-native 구조화 출력에서 JSON을 중복 출력해 `json.loads`가 깨지는 문제)가 정리돼 있다.
  Week 3는 structured_response가 필요 없으므로 이 인자 자체가 필요 없다 — "인자만 다르다"는 게
  바로 이 `response_format` 유무 차이다.

# 검증 방법

- `./run.sh --week3`로 실행해 agent가 예외 없이 생성되고 대화가 되는지 확인한다.
- 같은 프로세스에서 `build_week03_agent()`를 두 번 호출해도 (`build_week_agent()`를 반복 호출하는
  것으로 간접 확인 가능) 같은 객체가 재사용되는지 확인한다(캐싱 가드 동작 확인).

---

# [추가 작업] personal_list_saved_schedules 구현 가이드

# 작업 목표

`student_parts/week03_build_nanas_logbook.py`의 `personal_list_saved_schedules` 함수 본문을
완성해, 조회 최적화를 위해 kind별로 분리 저장된 `schedules` 테이블을 날짜/종류 필터로 조회하는
Week 3 메인과제 tool을 만든다.

# 수정 범위

- 수정 대상은 `personal_list_saved_schedules(...)` 함수 본문(TODO 두 줄)뿐이다.
- `SavedScheduleListInput`, `AppSQLiteStore.list_schedules(...)`, 그 외 tool 함수 본문은 이번
  작업 범위가 아니다. 건드리지 않는다.

# 하지 말아야 할 것

- `kind`가 `None`일 때만 `"personal_schedule"`로 바꾼다. `kind`에 다른 값(`"group_schedule"` 등)이
  명시적으로 들어오면 그 값을 그대로 존중하고 덮어쓰지 않는다.
- `date_from`/`date_to`가 `None`일 때 이를 "전체 조회"를 위한 극한 날짜 값(예: `"0000-01-01"`,
  `"9999-12-31"`)으로 치환하지 않는다. `AppSQLiteStore.list_schedules(...)`는 이미 `date_from`/
  `date_to`가 falsy면 해당 WHERE 조건 자체를 안 붙이는 방식으로 "필터 없음"을 구현해 두었다.
  극한 값으로 치환하면, `schedules.date`가 `NOT NULL`이 아니라서 날짜 없이 저장된 일정(`date IS
  NULL`)이 `date >= '0000-01-01'` 비교에서 SQL 3값 논리상 걸러져 "전체 조회" 의도와 반대로 결과에서
  빠지는 회귀가 생긴다. `date_from`/`date_to`는 받은 값을 그대로 전달한다.

# personal_list_saved_schedules 함수 본문 명세

다음 내용이 모두 드러나도록 구현한다.

- 이 함수는 조회 최적화를 위해 kind별로 분리 저장된 `schedules` 테이블을 조회하는 tool이며,
  agent가 참고할 수 있는 내용을 `@tool` docstring에 남긴다.
- `@tool(args_schema=SavedScheduleListInput)`로 `limit`/`kind`/`date_from`/`date_to` 인자를
  검증한다.
- `kind`가 `None`으로 들어오면 `"personal_schedule"`을 기본값으로 쓴다(`kind or
  "personal_schedule"`). `date_from`/`date_to`는 받은 값을 그대로 쓴다(위 "하지 말아야 할 것"
  참고).
- `AppSQLiteStore.list_schedules(limit=..., kind=..., date_from=..., date_to=...)`를 호출해
  DB를 조회한다.
- 적용된 `filters`(kind/date_from/date_to/limit)와 조회된 `schedules`를 포함한 JSON 문자열을
  반환한다.

# 참고자료

- `fixed/app_store.py`의 `AppSQLiteStore.list_schedules(limit, kind, date_from, date_to)`
  (480번째 줄): 실제 조회를 수행하는 메서드다. `save_structured_request`와 달리 tool 이름과
  메서드 이름이 다르므로 그대로 매칭해서 헷갈리지 않는다.
- `AppSQLiteStore.save_structured_request(payload)`의 kind별 분기(`fixed/app_store.py:352,
  385,395`): `personal_schedule`/`group_schedule`일 때만 `schedules`에 INSERT하므로, `schedules`
  테이블에는 애초에 `kind="unknown"`인 row가 존재하지 않는다(`todo`/`reminder`도 마찬가지로 각자
  다른 테이블에만 들어간다).
- `student_parts/week03_build_nanas_logbook.py`의 `_store()`/`json_payload()`/`tool_result()`:
  `save_structured_request`가 쓴 것과 같은 공용 helper를 그대로 재사용한다.

# 검증 방법

- `./run.sh --week3`에서 일정을 하나 저장한 뒤 "내 일정 보여줘"를 입력해 `personal_list_saved_schedules`가
  호출되고, 방금 저장한 일정이 결과에 포함되는지 확인한다.
- `kind`를 생략하고 호출했을 때 반환 JSON의 `filters.kind`가 `"personal_schedule"`로 채워지는지
  확인한다.
- 날짜 없이 저장된 일정이 있는 상태에서 `date_from`/`date_to` 없이 조회했을 때, 그 일정도 결과에
  빠지지 않고 포함되는지 확인한다.
