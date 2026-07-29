# 작업 목표

`student_parts/week05_load_kanas_past_conversations.py`의 메인과제 MCP wrapper 세로 슬라이스를
완성한다. 이 주차는 SQL을 직접 짜는 주차가 아니라, 이미 구현된 MCP 서버
(`mcp_server/sqlite_mcp_server.py`, 학생 수정 대상 아님)의 tool을 호출하고 그 결과를 LangChain
agent용 문자열로 감싸는 wrapper tool을 만드는 주차다. 핵심은 아래 두 갈래다.

- 외부 멤버의 이전 대화를 검색·로드하고 그 대화에서 일정을 추출하는 MCP wrapper
  (`search_previous_conversations` / `load_conversation_messages` / `extract_schedules_from_history`).
- 공유 일정 저장소 조회(`list_shared_schedules`)와, 내 일정(SQLite + 현재 대화 임시 일정)·외부 멤버
  busy-time을 한 `rows` 배열로 합치는 `collect_member_schedules`. 이 두 tool은 Week 6 Kana 하위
  agent가 그대로 재사용하는 연결 지점이라 메인과제다.

# 수정 범위

- 수정 대상 파일은 `./week05_load_kanas_past_conversations.py` 하나이며, 이번 문서가 다루는 메인과제
  구현 대상은 아래뿐이다.
  - `_personal_schedules_for_current_scope`
  - `_collect_member_schedules`
  - `search_previous_conversations`
  - `load_conversation_messages`
  - `extract_schedules_from_history`
  - `list_shared_schedules`
  - `collect_member_schedules`
  - `week05_prompt_parts()`의 `# TODO` 부분 (선택 — 아래 별도 절 참고)
  - 파일 상단 import 구문에 `student_parts.week04_retrieve_nanas_memory`의 `safe_limit` 추가(아래
    "공통 규칙"의 `limit` 보정에 사용)
- `create_shared_schedule`, `delete_shared_schedule`는 "추가 과제" 영역이라 이번 메인과제 문서의
  구현 대상이 아니다. 아래 "추가 과제 관련 안내" 항목만 참고하고, 구현하지 않을 경우 본문은
  그대로 두되 `week05_tools()` 목록에서 이 두 tool을 뺀다.
- `_schedule_scope`, `_structured_request_from_schedule_row`, `json_payload`, 6개 Input 스키마
  (`SearchPreviousConversationsInput`/`LoadConversationMessagesInput`/`ExtractSchedulesFromHistoryInput`/
  `ListSharedSchedulesInput`/`CollectMemberSchedulesInput`/`CreateSharedScheduleInput`/
  `DeleteSharedScheduleInput`), `call_mcp_tool`/`call_mcp_tool_sync`/`load_langchain_mcp_tools`/
  `load_langchain_mcp_tools_sync` 별칭, `week05_tools()`, `week05_system_prompt()`,
  `build_week05_agent()`, `build_week_agent()`는 이미 완성돼 있거나 이번 작업 범위가 아니므로
  수정하지 않는다.
- `mcp_server/sqlite_mcp_server.py`, `fixed/app_store.py`, `fixed/external_mcp.py`,
  `fixed/external_people_store.py`, `fixed/mcp_client.py` 등 `fixed/`·`mcp_server/` 아래 코드는 이미
  구현된 채로 주어지므로 건드리지 않는다.

# 주차 간 시스템 프롬프트 정리 (Week 5 작업 시작 전 먼저 처리)

`week05_prompt_parts()`는 `week04_prompt_parts()`→`week03_prompt_parts()`→`week02_prompt_parts()`→
`week01_prompt_parts()` 순으로 누적된 조각을 그대로 이어받는다. 그 누적된 조각 안에 이번 주차에
실제로 구현하는 기능(외부 멤버 일정 조율)과 모순되는 문장이 두 개 남아 있다.

1. `week02_prompt_parts()` (`student_parts/week02_structure_natural_language_requests.py`)의 문장:
   > "Week 2에서는 아직 외부 멤버 일정 조율을 하지 않는다. 구조화 결과를 다른 사람 일정과 조율하는
   > 동작은 이번 주차 범위 밖이다."

   Week 5에서 `collect_member_schedules`/`list_shared_schedules` 등으로 외부 멤버 일정 조율이 실제로
   시작되므로 이 문장은 전부 거짓 지시가 된다. 이 문장이 담고 있던 범위 제한은 "외부 멤버 일정
   조율" 하나뿐이므로, 부분 수정이 아니라 문장 전체를 `week02_prompt_parts()`에서 지운다.

2. `week03_prompt_parts()` (`student_parts/week03_build_nanas_logbook.py`)의 문장:
   > "Week 3에서는 외부 멤버 일정 조율을 처리하지 않는다."

   같은 이유로 이 문장 전체를 `week03_prompt_parts()`에서 지운다.

이 두 문장을 지우고 나면 누적된 system prompt에 "외부 멤버 일정 조율을 하지 않는다"는 잘못된
지시가 더 이상 남지 않는다. Week 5 전용 안내(외부 멤버 대화/일정 tool 사용법)는 이 정리를 마친
뒤에 `week05_prompt_parts()`에 추가한다.

# 하지 말아야 할 것

- 파일 상단의 `[5주차 수강생 구현 가이드]` 주석은 출제 의도 확인용으로 **컨닝하지 않는다**. 그
  내용을 그대로 베끼거나 답을 그 주석에서 가져오는 방식으로 구현하지 않는다.
- `search_previous_conversations`/`extract_schedules_from_history`/`list_shared_schedules`에서
  멤버 이름·날짜 범위의 별칭/포맷 치환을 wrapper가 다시 계산하지 않는다. `normalize_external_member_names`/
  `normalize_external_schedule_date_bounds`는 이미 MCP 서버 쪽 `ExternalPeopleSQLiteStore` 내부에서
  호출되므로, `member_names`/`date_from`/`date_to` 값 자체는 wrapper가 다시 바꾸지 않고 그대로 넘긴다.
  (단, `limit`처럼 `None`/누락 방어가 필요한 필드는 이 항목과 별개다 — 아래 "LLM tool 입력 방어" 참고.)
  - `list_shared_schedules`가 감싸는 `ExternalPeopleSQLiteStore.list_shared_schedules`(약 373~374번째
    줄)는 `normalize_external_schedule_date_bounds`라는 이름의 helper를 호출하지는 않지만, 같은
    `str(date).split("T", 1)[0].strip()` 형식 정리를 인라인으로 동일하게 수행한다. 즉 `"T"` 이후
    시간부를 잘라내는 형식 정리는 `extract_schedules_from_history`·`list_shared_schedules` 두 store
    메서드 모두에서 이미 한 번 처리되므로, 이 항목(별칭/포맷 치환 재계산 금지)은 `list_shared_schedules`
    에도 그대로 적용된다 — wrapper가 `date_from`/`date_to` 문자열을 별도로 자르거나 재포맷하지 않는다.
  - 단, "형식 정리(문자열 자르기)"가 boundary에서 처리된다는 것과, `date_from`/`date_to`가 `None`일 때
    "그 방향 전체 기간"으로 채우는 것 / `date_to < date_from`을 검증하는 것은 별개의 문제다. 두
    store 메서드 어디도 이 두 가지를 대신 해주지 않으므로, 아래 "date_from/date_to 입력 방어 (공통)"
    절의 내용은 이 항목과 상충하지 않고 여전히 wrapper 책임으로 남는다.
- `load_conversation_messages`가 `call_external_tool_payload`로 받은 dict의 `sender`/`content`/
  `created_at` 순서나 필드를 가공(재정렬, 필드 추가/삭제 등)하지 않는다.
- `extract_schedules_from_history`/`list_shared_schedules` 결과 문자열을 파싱해서 필드명을 바꾸거나
  재가공하지 않는다. MCP 서버가 반환한 JSON 문자열을 그대로 돌려준다.
- `collect_member_schedules`가 반환하는 top-level 키 이름(`rows`, `schedule_summary`)을 다른 이름으로
  바꾸지 않는다. Week 6 하위 agent가 `rows`를 `busy_rows` 근거로 그대로 재사용하는 계약이다.
- `_personal_schedules_for_current_scope`에서 SQLite에 이미 저장된 일정과 `PERSONAL_SCHEDULES`의
  같은 일정을 중복으로 합치지 않는다. `student_parts/week03_build_nanas_logbook.py`의
  `personal_create_schedule`가 `PERSONAL_SCHEDULES`에 append하는 동시에
  `save_structured_request(source_schedule_id=created["id"])`를 호출해 SQLite `schedules.schedule_id`를
  그 임시 `id`와 같게 맞추는 흐름이 있으므로(`fixed/app_store.py`의 `save_structured_request`,
  약 305~353번째 줄), `schedule_id`/`id` 기준으로 겹치는 항목을 걸러내야 한다.
- `list_shared_schedules` wrapper에서 필터 기본값을 미리 채우지 않는다. 필터를 전혀 안 주면
  `ExternalPeopleSQLiteStore.list_shared_schedules`가 알아서 실습용 기본 공유 일정(7월 철수/영희/
  민준/서연/지훈/하린)을 반환한다. 아래 "date_from/date_to 입력 방어 (공통)"의 `None` 채움 규칙도
  `list_shared_schedules`에는 예외로 적용하지 않는다 — 이 항목과 같은 이유다.

# 공통 규칙

- 이 파일 안에서 직접 dict를 구성해 반환하는 경우에만 `json_payload(payload)`로 감싸고, MCP tool을
  그대로 통과시키는 경우에는 `call_mcp_tool_sync(...)`가 이미 반환한 JSON 문자열을 그대로 돌려준다
  (`student_parts/claude.md`의 "tool 반환값이 깨지지 않도록 하는 방어" 참고).
- MCP tool 호출은 이 파일에 이미 별칭으로 정의된 `call_mcp_tool_sync(tool_name, args)`
  (`fixed/mcp_client.py`의 `call_local_mcp_tool_sync`)를 사용한다. 호출 결과를 dict로 파싱까지 받아야
  하는 경우에만 `fixed/external_mcp.py`의 `call_external_tool_payload(tool_name, args)`를 사용한다.
- 이미 같은 기능을 하는 함수/저장소 메서드가 있으면 직접 재구현하지 않고 그대로 호출해서 쓴다
  (`AppSQLiteStore.list_schedules`, MCP 서버가 감싸고 있는 `ExternalPeopleSQLiteStore`의 각 메서드,
  `external_schedule_summary`).
- `search_previous_conversations`/`list_shared_schedules`의 `limit`은 `student_parts/claude.md`의
  "LLM이 직접 호출하는 tool의 입력 방어" 규칙에 따라 MCP tool을 호출하기 전에 `safe_limit(limit,
  default=..., maximum=...)`로 보정한다. Pydantic `Field(ge=..., le=...)`가 이미 1차 검증을 하지만,
  `safe_limit`으로 한 번 더 안전한 정수로 정리하는 것이 Week 4부터 이어지는 이 프로젝트의 일관된
  방어 패턴이다. `safe_limit`은 `student_parts/week04_retrieve_nanas_memory.py`에 이미 정의돼 있으므로
  이 파일 상단 import에 추가해서 그대로 쓰고, 새로 만들지 않는다.

# date_from/date_to 입력 방어 (공통)

`extract_schedules_from_history`/`collect_member_schedules`(`_collect_member_schedules` 경유)/
`list_shared_schedules`처럼 `date_from`/`date_to` 기간을 받는 tool은, `ExtractSchedulesFromHistoryInput`/
`CollectMemberSchedulesInput`이 두 필드를 `str`(필수)로 선언해 두었어도 `student_parts/claude.md`의
"LLM이 직접 호출하는 tool의 입력 방어" 규칙에 따라 LLM이 `None`을 보낼 수 있다고 가정하고 함수
본문에서 아래 두 가지를 직접 처리한다.

## 1. `None`인 방향은 "그 방향 전체 기간"으로 채운다

- 대상: `extract_schedules_from_history`, `collect_member_schedules`(내부에서 호출하는
  `_collect_member_schedules`). `date_from`이 `None`/빈 문자열이면 조회 가능한 가장 이른 날짜
  (예: `"0001-01-01"`)로, `date_to`가 `None`/빈 문자열이면 가장 늦은 날짜(예: `"9999-12-31"`)로
  채운 뒤 `call_mcp_tool_sync("extract_schedules_from_history", ...)`를 호출한다.
- 이 보정이 필요한 이유: `ExternalPeopleSQLiteStore.extract_schedules_from_history`는
  `WHERE date >= ? AND date <= ?`로 단순 문자열 비교만 하고, `normalize_external_schedule_date_bounds`
  (`fixed/external_people_store.py`)는 `None`을 빈 문자열로만 바꿀 뿐 "그 방향 전체 기간"으로
  채워주지 않는다. 특히 `date_to`가 빈 문자열이면 `date <= ""`가 사실상 항상 거짓이 되어 결과가
  통째로 빈 `rows`로 나온다. `fixed/` 아래는 학생 수정 대상이 아니므로, 이 보정은 이 파일의 wrapper
  (또는 `_collect_member_schedules`)에서 MCP tool을 부르기 전에 해야 한다.
- **예외 — `list_shared_schedules`에는 이 규칙을 적용하지 않는다.** 이 tool은 `date_from`/`date_to`가
  `None`이면 "필터 없음"으로 보고 서버가 실습용 기본 공유 일정을 채워 반환하는 게 이미 의도된
  동작이다(아래 "하지 말아야 할 것"의 "필터 기본값을 미리 채우지 않는다" 항목과 같은 이유). 그러므로
  `list_shared_schedules`는 `date_from`/`date_to`가 `None`이면 `None` 그대로 MCP tool에 넘긴다.

## 2. `date_to`가 `date_from`보다 이전이면 tool 호출 전에 바로 에러로 응답한다

- 대상: `extract_schedules_from_history`, `list_shared_schedules`, `collect_member_schedules` 세
  tool 모두. 위 1번을 적용한 뒤(또는 `list_shared_schedules`처럼 둘 다 실제 값이 채워져 들어온
  경우) 두 날짜가 모두 있고 `date_to < date_from`이면(둘 다 `YYYY-MM-DD` 형식이라 문자열 비교로
  충분하다) `call_mcp_tool_sync`/`call_external_tool_payload`를 아예 호출하지 않고, 그 tool 함수
  안에서 바로 에러 payload를 만들어 반환한다. LLM이 다른 tool을 추가로 부르지 않고 이 응답만으로
  "종료일이 시작일보다 빠르다"고 사용자에게 바로 답할 수 있어야 하는 것이 핵심이다.
- 에러 payload도 "tool 반환값의 envelope 구성" 규칙(`student_parts/claude.md`)을 따른다. 이 파일
  안에서 직접 dict를 구성하는 경우이므로, `json_payload({"ok": False, "tool_name": "<그 tool 이름>",
  "error": "date_to는 date_from보다 앞설 수 없습니다: date_from=... date_to=..."})` 형태로 감싸
  반환한다. `ok: False`는 "입력 자체가 잘못됐다"는 뜻으로, 조회 결과가 비어서 `ok: True`인 다른
  정상 응답들과 구분된다.
- `collect_member_schedules`는 이 검증을 tool 함수 초입에서 하고, 검증에 걸리면
  `_personal_schedules_for_current_scope()`/`_collect_member_schedules`를 아예 호출하지 않는다
  (불필요한 SQLite/MCP 조회를 막는다).
- 위 1번(`None` 채움)과 2번(역순 검증)의 로직을 세 tool에 각각 따로 베껴 쓰지 않는다. 이름은 자유롭게
  정해도 되지만(예: `_resolve_schedule_date_range(date_from, date_to, *, fill_open_range: bool)`처럼
  `list_shared_schedules`는 `fill_open_range=False`로 불러 1번을 건너뛰게 만드는 구조), 세 tool이 같은
  helper를 재사용하는 형태로 만든다.

## 3. `date_from`/`date_to`의 입력 형식을 LLM에 tool 레벨에서도 명시한다

- `normalize_external_schedule_date_bounds`(`fixed/external_people_store.py`)는 `"T"` 기준으로 시간
  부분만 잘라내는 룰 기반 처리이지, `"7월 7일"`/`"2026/07/07"`같이 다른 형식으로 들어온 문자열을
  `'YYYY-MM-DD'`로 바꿔주지 않는다. 즉 날짜 형식이 맞는지는 MCP/store 경계가 아니라 LLM이 tool을
  호출하는 시점에 이미 결정되므로, tool이 그 형식을 명확히 요구해야 한다.
- Week 5 agent는 `week02_prompt_parts()`에서 물려받은 "오늘 날짜는 {current_app_date_iso()}이다.
  '내일'/'다음 주 화요일'같은 상대 날짜는 이 날짜 기준으로 계산해 `'YYYY-MM-DD'`로 채운다"는 system
  prompt 지시를 이미 이어받는다. 다만 이 지시만으로는 부족하니, tool 레벨에서도 같은 형식을 한 번
  더 명시하는 것이 이 프로젝트의 기존 관례다(`student_parts/week01_wake_up_nana.py`의
  `personal_list_schedules` `@tool(description=...)`이 `"date_from, date_to는 모두 선택값이며
  'YYYY-MM-DD' 형식이다. ... 예: date_from='2026-07-01', date_to='2026-07-07'"`처럼 형식과 예시를
  tool 설명에 직접 박아 두는 예시다).
- Week 5는 `@tool(args_schema=...)` 스타일이므로, 같은 정보를 `ExtractSchedulesFromHistoryInput`/
  `ListSharedSchedulesInput`/`CollectMemberSchedulesInput`의 `date_from`/`date_to` 필드에
  `Field(description=...)`로 추가한다. 설명에는 다음 세 가지가 드러나야 한다.
  - 형식: `'YYYY-MM-DD'` (시간 없이 날짜만, 예: `'2026-07-07'`).
  - `date_from`을 주면 그 날짜 이상, `date_to`를 주면 그 날짜 이하만 조회된다는 방향성.
  - `None`을 줘도 되는 필드에서 `None`의 의미: `extract_schedules_from_history`/
    `collect_member_schedules`는 "그 방향 전체 기간으로 조회"(위 1번), `list_shared_schedules`는
    "그 필터를 아예 안 준 것과 같음 → 실습용 기본 공유 일정 반환"(위 1번의 예외)이라는 서로 다른
    의미를 각 Input 스키마의 `Field(description=...)`에 그대로 반영해 서로 헷갈리지 않게 한다.

# 함수별 구현 명세

## _personal_schedules_for_current_scope() -> list[dict[str, Any]]

- `AppSQLiteStore(CONFIG.app_db_path).list_schedules(...)`를 호출해 Week 3 이후 SQLite에 저장된 내
  일정 row(`schedule_id`/`request_id`/`owner`/`title`/`date`/`start_time`/`end_time`/`attendees`/
  `source`/`created_at`/`request_kind`, `fixed/app_store.py`의 `decode_schedule_row` 형태)를 가져온다.
- `PERSONAL_SCHEDULES`(`student_parts/week01_wake_up_nana.py`) 중 `_schedule_scope(schedule)`이 현재
  `current_session_scope()`와 같은 항목만 남긴다.
- 남은 `PERSONAL_SCHEDULES` 항목 중 `id`가 위 SQLite row들의 `schedule_id` 집합에 포함되는 항목은
  제외한다(이미 SQLite에도 저장된 일정이므로 중복). 이 필터링을 거친 뒤에만 SQLite row 목록과
  합쳐 반환한다.
- 반환 리스트의 각 항목 구조를 이 함수 안에서 통일할 필요는 없다. SQLite row와 `PERSONAL_SCHEDULES`
  항목은 필드 이름이 이미 겹치므로(`title`/`date`/`start_time`/`end_time`), 구조를 하나의 row 모양으로
  맞추는 작업은 `_collect_member_schedules`의 책임이다.

## _collect_member_schedules(*, member_names, date_from, date_to, personal_schedules) -> dict[str, Any]

- `call_mcp_tool_sync("extract_schedules_from_history", {"member_names": member_names, "date_from":
  date_from, "date_to": date_to})`를 호출하고 반환 문자열을 파싱해 외부 멤버 rows를 얻는다. 이 rows는
  이미 `member_name`/`title`/`date`/`start_time`/`end_time`/`notes`/`source_conversation_id` 구조를
  갖는다(MCP 서버가 이미 정규화한 결과다).
- `personal_schedules`의 각 항목을 `member_name="나"`로 채우고 `title`/`date`/`start_time`/`end_time`을
  그대로 옮겨, 외부 멤버 rows와 같은 구조(`member_name`/`title`/`date`/`start_time`/`end_time`/
  `notes`)의 row로 바꾼다. `_structured_request_from_schedule_row`를 사용해 `StructuredRequest`로 먼저
  읽은 뒤 필요한 필드만 뽑아도 되고, 필요한 필드만 직접 매핑해도 된다.
- 변환한 "나" rows와 외부 멤버 rows를 하나의 `rows` 리스트로 합친다.
- `{"rows": rows, "schedule_summary": external_schedule_summary(rows)}` 형태의 dict를 반환한다.
  (`external_schedule_summary`는 `fixed/external_people_store.py`에 이미 있다.) JSON 문자열 변환은
  이 함수를 호출하는 `collect_member_schedules` tool의 책임이다.

## search_previous_conversations(query, member_names=None, limit=5) -> str (`@tool`)

- `limit`을 `safe_limit(limit, default=5, maximum=50)`로 보정한다(`SearchPreviousConversationsInput`의
  `le=50`과 맞춘다).
- `call_mcp_tool_sync("search_previous_conversations", {"query": query, "member_names": member_names,
  "limit": <보정값>})`를 호출한 결과 문자열을 **그대로** 반환한다.
- 리턴 스키마 (MCP 서버 `mcp_server/sqlite_mcp_server.py::search_previous_conversations`가 만드는
  JSON 문자열을 그대로 통과시키므로, wrapper의 반환값은 이 구조와 100% 동일하다):

  ```json
  {
    "ok": true,
    "tool_name": "search_previous_conversations",
    "rows": [
      {
        "conversation_id": "string",
        "member_name": "string",
        "title": "string",
        "content": "string",
        "created_at": "string (ISO-ish timestamp)"
      }
    ]
  }
  ```

  - `rows`는 `ExternalPeopleSQLiteStore.search_previous_conversations`가 반환하는 리스트이며, 메시지
    단위 row다(대화 하나가 메시지 수만큼 여러 row로 나뉠 수 있음). `member_names=None`이면 전체 멤버
    대상, 빈 list `[]`면 빈 `rows`.
  - 이후 `load_conversation_messages`를 호출할 때 필요한 `conversation_id`가 각 row에 들어 있다.
  - **envelope 준수 확인**: `student_parts/claude.md`의 "tool 반환값의 envelope 구성" 규칙대로
    `ok`/`tool_name`은 메타데이터, `rows`가 실제 내용이다. 이 함수는 dict를 직접 구성하지 않고
    MCP 서버가 이미 이 envelope으로 만든 JSON 문자열을 그대로 통과시키므로, wrapper 코드를 수정하지
    않아도 이 규칙을 만족한다. 이 함수를 고칠 때 `{"rows": rows}`처럼 envelope 없이 dict를 새로
    구성해 반환하지 않도록 주의한다(그 순간부터는 `json_payload`로 감싸고 `ok`/`tool_name`도 직접
    채워야 하는 "dict 직접 구성" 경로로 바뀐다).

## load_conversation_messages(conversation_id) -> str (`@tool`)

- `call_external_tool_payload("load_conversation_messages", {"conversation_id": conversation_id})`로
  dict를 받는다.
- 그 dict를 가공 없이 `json_payload(...)`로 감싸 반환한다(`sender`/`content`/`created_at` 순서 보존).
- 리턴 스키마 (`mcp_server/sqlite_mcp_server.py::load_conversation_messages`가 만드는 dict를
  `json_payload`로 감싼 것과 동일한 구조):

  ```json
  {
    "ok": true,
    "tool_name": "load_conversation_messages",
    "rows": [
      {
        "role": "string",
        "sender": "string",
        "content": "string",
        "created_at": "string (ISO-ish timestamp)"
      }
    ]
  }
  ```

  - `rows`는 `ExternalPeopleSQLiteStore.load_conversation_messages`가 `created_at ASC`로 정렬해
    반환하는 리스트이므로, wrapper는 순서를 재정렬하지 않는다.
  - **envelope 준수 확인**: 이 함수는 `call_external_tool_payload`로 문자열이 아니라 dict를 받으므로
    `json_payload(...)`로 감싸는 것이 맞다(`student_parts/claude.md`의 "tool 반환값이 깨지지 않도록
    하는 방어" 규칙). 다만 그 dict는 이미 MCP 서버가 `ok`/`tool_name`/`rows` envelope으로 만들어 둔
    것을 `call_external_tool_payload`가 파싱만 한 결과이므로, `json_payload`는 새 envelope을 만드는
    게 아니라 기존 envelope을 문자열로 되돌리는 역할만 한다. 이 함수를 고칠 때 `{"rows": payload}`나
    `{"ok": True, "rows": payload.get("rows")}`처럼 dict를 새로 조립하지 않는다 — `payload`를 그대로
    `json_payload`에 넘겨야 `ok`/`tool_name`/`rows` 값이 원본과 동일하게 보존된다.

## extract_schedules_from_history(member_names, date_from, date_to) -> str (`@tool`)

- 위 "date_from/date_to 입력 방어 (공통)" 1번·2번을 먼저 적용한다: `date_from`/`date_to`가
  `None`/빈 문자열이면 그 방향 전체 기간으로 채우고, 채운 뒤에도 `date_to < date_from`이면 MCP tool을
  호출하지 않고 바로 에러 payload를 반환한다.
- 위 검증을 통과했을 때만 `call_mcp_tool_sync("extract_schedules_from_history", {"member_names":
  member_names, "date_from": date_from, "date_to": date_to})`를 호출한 결과 문자열을 **그대로**
  반환한다.

## list_shared_schedules(member_names=None, date_from=None, date_to=None, source_conversation_id=None, limit=50) -> str (`@tool`)

- `limit`을 `safe_limit(limit, default=50, maximum=200)`로 보정한다(`ListSharedSchedulesInput`의
  `le=200`과 맞춘다).
- 위 "date_from/date_to 입력 방어 (공통)" 1번은 적용하지 않는다(`None`은 "필터 없음"으로 그대로 MCP
  tool에 넘겨야 서버가 실습용 기본 공유 일정을 채워 반환한다 — 아래 "하지 말아야 할 것"의 "필터
  기본값을 미리 채우지 않는다" 항목과 같은 이유). 다만 2번은 적용한다: `date_from`/`date_to`가 **둘
  다** 값이 있고 `date_to < date_from`이면 MCP tool을 호출하지 않고 바로 에러 payload를 반환한다.
  둘 중 하나라도 `None`이면 이 검증을 건너뛴다.
- `call_mcp_tool_sync("list_shared_schedules", {"member_names": member_names, "date_from": date_from,
  "date_to": date_to, "source_conversation_id": source_conversation_id, "limit": <보정값>})`를 호출한
  결과 문자열을 **그대로** 반환한다. `member_names`/`date_from`/`date_to`/`source_conversation_id`
  필터를 전혀 안 주면 서버가 알아서 실습용 기본 공유 일정을 채워 반환하므로 wrapper가 그 기본값을
  대신 채울 필요는 없다.

## collect_member_schedules(member_names, date_from, date_to) -> str (`@tool`)

- 위 "date_from/date_to 입력 방어 (공통)" 1번·2번을 tool 함수 초입에서 적용한다: `date_from`/
  `date_to`가 `None`/빈 문자열이면 그 방향 전체 기간으로 채우고, 채운 뒤에도 `date_to < date_from`이면
  `_personal_schedules_for_current_scope()`/`_collect_member_schedules`를 호출하지 않고 바로 에러
  payload를 반환한다.
- 검증을 통과했을 때만 `_personal_schedules_for_current_scope()`를 호출해 내 일정 목록을 얻는다.
- `_collect_member_schedules(member_names=member_names, date_from=date_from, date_to=date_to,
  personal_schedules=<위 목록>)`를 호출해 `{"rows": ..., "schedule_summary": ...}`를 얻는다.
- `{"ok": True, "tool_name": "collect_member_schedules", "rows": ..., "schedule_summary": ...}` 형태로
  `json_payload(...)`에 감싸 반환한다. `rows`/`schedule_summary` 키 이름을 바꾸지 않는다.

# week05_prompt_parts() 구현 명세 (선택)

`week04_prompt_parts()`가 반환한 list 뒤에(위 "주차 간 시스템 프롬프트 정리"를 먼저 적용한 뒤),
아래 내용이 드러나는 문자열 조각을 자유롭게 추가할 수 있다. 필수 구현 항목은 아니며, 추가하지
않아도 메인과제 완료로 본다.

- 개인적인 저장/RAG 요청(참고자료 검색·등록, 저장된 일정/할 일/알림 검색)은 Week 4까지의 tool로
  계속 처리하고, 이번 주차에서 새로 바뀌지 않는다는 점을 명시한다.
- 외부 멤버의 이전 대화나 일정을 확인해야 하는 요청에는 `search_previous_conversations`/
  `load_conversation_messages`/`extract_schedules_from_history`/`list_shared_schedules`/
  `collect_member_schedules` 중 맞는 tool을 고르도록 안내한다.

# 추가 과제 관련 안내

`create_shared_schedule`/`delete_shared_schedule`는 아직 본문이 `# TODO`인 추가 과제다.

- `create_shared_schedule(...)`: `call_mcp_tool_sync("create_shared_schedule", {"member_name":
  member_name, "title": title, "date": date, "start_time": start_time, "end_time": end_time, "notes":
  notes, "source_conversation_id": source_conversation_id, "schedule_id": schedule_id})`를 호출한 결과
  문자열을 그대로 반환한다.
- `delete_shared_schedule(...)`: `call_mcp_tool_sync("delete_shared_schedule", {"schedule_id":
  schedule_id, "source_conversation_id": source_conversation_id})`를 호출한 결과 문자열을 그대로
  반환한다.
- 두 tool 모두 `schedule_id`/`source_conversation_id`를 그대로 전달해야, 나중에 같은 row를 다시 찾아
  수정/삭제할 수 있다. `fixed/external_mcp.py`의 `sync_personal_schedule_to_shared`/
  `delete_personal_schedule_from_shared`가 이미 같은 패턴으로 이 두 tool을 호출하는 실제 예시다.
- 구현하지 않을 경우 `week05_tools()` 목록에서 이 두 tool을 뺀다.

# 참고자료

- `mcp_server/sqlite_mcp_server.py`: `search_previous_conversations`/`load_conversation_messages`/
  `extract_schedules_from_history`/`create_shared_schedule`/`delete_shared_schedule`/
  `list_shared_schedules` MCP tool의 실제 구현. 각 tool이 반환하는 JSON의 top-level 키(`ok`/
  `tool_name`/`rows`/`shared_schedule`/`deleted`/`schedule_summary` 등)를 여기서 확인한다.
- `fixed/external_people_store.py`의 `ExternalPeopleSQLiteStore`: 위 MCP tool들이 감싸고 있는 실제
  저장소 메서드. `list_shared_schedules`(약 343번째 줄)의 "필터 없으면 7월 실습 기본값" 분기,
  `normalize_external_member_names`/`normalize_external_schedule_date_bounds`/
  `external_schedule_summary` helper가 여기에 있다.
- `fixed/external_mcp.py`: `call_external_tool_payload`와, `create_shared_schedule`/
  `delete_shared_schedule`를 호출하는 실제 예시(`sync_personal_schedule_to_shared`,
  `sync_group_schedule_to_shared`, `delete_personal_schedule_from_shared`,
  `delete_group_schedule_from_shared`).
- `fixed/app_store.py`의 `AppSQLiteStore.list_schedules`(약 480번째 줄), `save_structured_request`(약
  281번째 줄, 특히 `source_schedule_id` 처리가 있는 305~353번째 줄): 내 일정이 SQLite에 저장되는
  방식과, 임시 `id`가 SQLite `schedule_id`로 이어지는 흐름.
- `student_parts/week03_build_nanas_logbook.py`의 `personal_create_schedule`/
  `structured_request_from_week01_schedule`: `PERSONAL_SCHEDULES` append와 SQLite 저장이 동시에
  일어나는 실제 지점 — `_personal_schedules_for_current_scope`의 중복 제거 로직이 다루는 대상이다.
- `student_parts/week01_wake_up_nana.py`의 `PERSONAL_SCHEDULES`/`_schedule_scope`/
  `current_session_scope`: 아직 SQLite에 없는 현재 대화 임시 일정과 그 대화 범위 판단 방식.
- `student_parts/week02_structure_natural_language_requests.py`의 `week02_prompt_parts()`,
  `student_parts/week03_build_nanas_logbook.py`의 `week03_prompt_parts()`: 위 "주차 간 시스템 프롬프트
  정리" 항목이 고치라고 지시하는 실제 위치.

# 검증 방법

- `./run.sh --week5`로 실행한 뒤 외부 멤버(예: "철수")의 이전 대화를 찾아 달라고 요청하고, trace에서
  `search_previous_conversations`가 호출되며 결과에 `conversation_id`가 포함된 row가 오는지 확인한다.
- 찾은 `conversation_id`로 이어서 질문해 `load_conversation_messages`가 호출되고, 결과 `rows`의
  `sender`/`content`/`created_at` 순서가 시간순으로 보존되는지 확인한다.
- 특정 멤버·기간의 일정을 물어봐 `extract_schedules_from_history`가 호출되고, 결과 `rows`가
  `member_name`/`title`/`date`/`start_time`/`end_time`/`notes` 필드를 유지하는지 확인한다.
- 필터 없이 공유 일정을 조회해 `list_shared_schedules`가 호출되고, 7월 실습용 기본 공유 일정(철수/
  영희/민준/서연/지훈/하린)이 `rows`로 오는지 확인한다.
- 여러 멤버의 가능 시간을 물어봐 `collect_member_schedules`가 호출되고, 결과 `rows`에 `member_name:
  "나"` row와 외부 멤버 row가 같은 구조로 섞여 있으며, SQLite에 저장된 내 일정과 현재 대화에서 만든
  임시 일정이 중복 없이 한 번씩만 나오는지 확인한다.
- Week 1-4에서 이미 확인하던 기능(개인 일정 CRUD, 구조화 응답, SQLite 저장/조회, 개인 RAG/저장 기록
  검색)이 Week 5 tool 추가 이후에도 그대로 동작하는지 회귀 확인한다.
- 추가 과제를 구현했다면, `create_shared_schedule`로 등록한 row가 `list_shared_schedules` 조회에
  나타나고 `delete_shared_schedule`로 삭제되는지 확인한다.
- `search_previous_conversations`/`list_shared_schedules`를 `limit` 없이 직접 호출(`.invoke({...})`)해
  기본값으로 보정되는지, `limit=None`을 넘겼을 때도 예외 없이 `safe_limit`의 `default`값으로
  보정되는지 확인한다.
- `extract_schedules_from_history`/`collect_member_schedules`를 `date_from=None` 또는 `date_to=None`으로
  직접 호출(`.invoke({...})`)해 예외 없이 그 방향 전체 기간이 조회되는지 확인한다. 같은 두 tool과
  `list_shared_schedules`를 `date_to`가 `date_from`보다 이른 값으로 호출해 MCP tool을 부르지 않고도
  `ok: False`와 에러 메시지가 바로 오는지 확인한다(trace에서 `search_previous_conversations`/
  `extract_schedules_from_history`/`list_shared_schedules` 등 다른 tool이 추가로 호출되지 않아야 한다).
  `list_shared_schedules`는 `date_from`/`date_to`를 모두 비웠을 때 여전히 실습용 기본 공유 일정이
  오는지도 함께 확인한다(회귀 확인).
