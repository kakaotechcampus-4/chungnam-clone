# 작업 목표

`student_parts/week03_build_nanas_logbook.py`의 `WEEK03_TOOL_CALL_PROMPT`를 완성해, Week 3 agent가
자연어 구조화 → SQLite 저장과 조회/수정/삭제 tool을 어떤 순서·조건으로 호출할지 알도록 만든다.

# 수정 범위

- 수정 대상은 `WEEK03_TOOL_CALL_PROMPT = ""` 한 곳뿐이다.
- `SQLITE_MEMORY_PROMPT`, `SaveStructuredRequestInput`, 각 tool 함수 본문, `week03_prompt_parts()`의
  나머지 `# TODO` 등 다른 부분은 이번 작업 범위가 아니다. 건드리지 않는다.

# 하지 말아야 할 것

- 파일 상단의 `[3주차 수강생 구현 가이드]` 주석은 출제 의도 확인용으로 **컨닝하지 않는다**. 그 내용을
  그대로 베끼거나 답을 그 주석에서 가져오는 방식으로 구현하지 않는다.
- Week 4 이상(RAG, 외부 멤버 일정 조율 등) 범위의 지시를 섞지 않는다.

# WEEK03_TOOL_CALL_PROMPT 명세

다음 내용이 모두 드러나는 system prompt 조각으로 작성한다.

- 저장 순서: 새 일정/할 일/알림을 저장할 때는 반드시
  ① `extract_schedule_request(query=...)`로 자연어를 구조화한 뒤,
  ② 그 결과의 `structured_request` 필드 값을 그대로 `save_structured_request`의 인자로 전달해 저장한다.
  (자연어 문자열이나 `ok`/`tool_name`/`base_date` wrapper를 그대로 저장하지 않는다는 원칙을 여기서
  agent 지시로 드러낸다.)
- 저장 요청 키워드: "일정 등록해줘", "기억해줘", "저장해줘", "메모해줘" 같은 표현을 저장 의도의 단서로
  삼는다.
- 조회: "내 일정 보여줘" 같은 질문에는 `personal_list_saved_schedules`(또는 `list_saved_requests`)를
  호출해 답한다.
- 수정/삭제 전 확인: `personal_update_saved_schedule`/`personal_delete_saved_schedules`를 호출하기
  전에 `personal_list_saved_schedules`로 후보 일정과 `schedule_id`를 먼저 확인한다.
- 삭제 안전 규칙: 조건 없이 전체를 지우는 것은 사용자가 명시적으로 전체 삭제를 요구했을 때만
  `delete_all`로 수행한다.

# 참고자료

- `student_parts/week02_structure_natural_language_requests.py`의 `extract_schedule_request`: 반환
  JSON 모양(`{"ok", "tool_name", "base_date", "structured_request": {...}}`)이 위 저장 순서 지시의
  근거다.
- `student_parts/week03_build_nanas_logbook.py`의 tool 목록(`save_structured_request`,
  `list_saved_requests`, `personal_list_saved_schedules`, `personal_update_saved_schedule`,
  `personal_delete_saved_schedules`): 이 prompt가 호출 순서를 안내해야 하는 실제 대상.

# 검증 방법

- `./run.sh --week3`에서 "내일 10시 개인 코칭 저장해줘"를 입력했을 때, trace에서
  `extract_schedule_request` 다음에 `save_structured_request`가 바로 이어 호출되는지 확인한다.
- "내 일정 보여줘"를 입력했을 때 `personal_list_saved_schedules`가 호출되는지 확인한다.
- "그 일정 지워줘"류 요청에서 삭제 tool 호출 전에 먼저 조회 tool이 호출되어 후보를 확인하는지
  확인한다.
