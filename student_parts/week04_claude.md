# 작업 목표

`student_parts/week04_retrieve_nanas_memory.py`의 메인과제 RAG 세로 슬라이스를 완성한다. Nana가
"내가 적어 둔 참고자료"와 "SQLite에 저장된 일정/할 일/알림 기록"을 출처별로 분리된 tool로 검색하게
만드는 것이 핵심이며, 하나의 통합 검색 함수로 뭉뚱그리지 않는다.

- `add_personal_reference_dict` / `search_personal_reference_hits` / `search_saved_request_rows`:
  각 tool이 사용하는 순수 데이터 helper.
- `add_personal_reference` / `search_personal_references` / `search_saved_requests`: 위 helper를
  `json_payload(...)`로 감싸 반환하는 `@tool` 함수 3개.
- `week04_prompt_parts()`의 나머지 `# TODO`: 질문 성격에 따라 참고자료 검색과 저장 기록 검색 중
  맞는 tool을 고르도록 system prompt 조각을 추가한다.

# 수정 범위

- 수정 대상 파일은 `./week04_retrieve_nanas_memory.py` 하나이며, 이번 문서가 다루는 구현 대상은
  아래 6개 함수와 `week04_prompt_parts()`의 `# TODO` 부분뿐이다.
  - `add_personal_reference_dict`
  - `search_personal_reference_hits`
  - `search_saved_request_rows`
  - `add_personal_reference`
  - `search_personal_references`
  - `search_saved_requests`
  - `week04_prompt_parts()` (Week 3까지 누적된 조각 뒤에 이어 붙이는 부분만)
- `search_conversation_messages_dict`, `search_conversation_message_rows`,
  `search_conversation_messages`, `search_nana_memory`는 "추가 과제" 영역이라 이번 메인과제 문서의
  구현 대상이 아니다. 아래 "추가 과제 관련 안내" 항목만 참고하고 본문을 건드리지 않는다.
- `json_payload`, `safe_limit`, `_decode_attendees`, `AddPersonalReferenceInput`,
  `SearchPersonalReferencesInput`, `SearchSavedRequestsInput`, `week04_tools`,
  `week04_system_prompt`, `build_week04_agent`, `build_week_agent`는 이미 완성돼 있거나 이번 작업
  범위가 아니므로 수정하지 않는다.
- `fixed/reference_store.py`, `fixed/app_store.py`, `fixed/conversation_rag_store.py` 등 `fixed/`
  아래 코드는 이미 구현된 채로 주어지므로 건드리지 않는다.

# 하지 말아야 할 것

- 파일 상단의 `[4주차 수강생 구현 가이드]` 주석은 출제 의도 확인용으로 **컨닝하지 않는다**. 그
  내용을 그대로 베끼거나 답을 그 주석에서 가져오는 방식으로 구현하지 않는다.
- `search_saved_request_rows`에서 `AppSQLiteStore.search_saved_requests(...)`가 반환한 row를 임의로
  가공(필드 추가/삭제, `members_json` 디코딩 등)하지 않는다. 함수 docstring 그대로 "실제 검색
  결과만" 반환한다 — 스펙에 없는 가공은 나중에 실제로 필요해질 때 추가한다.
- `search_personal_reference_hits`가 반환하는 `metadata.tags`를 원래 저장 형태(list[str])와 다른
  타입으로 남기지 않는다. `PersonalReferenceStore`는 태그를 `","join(...)`으로 콤마 문자열로
  저장/반환하므로, hit을 만들 때 다시 list로 되돌린다(아래 구현 명세 참고).
- `search_personal_references`/`search_saved_requests` tool 응답의 top-level 키 이름(`hits`,
  `rows`)을 다른 이름으로 바꾸지 않는다. course repo 채점 계약이 이 키 이름을 그대로 사용한다.
- 아직 본문이 `# TODO`인 `search_conversation_messages`/`search_nana_memory`(추가 과제)를
  `week04_prompt_parts()`에서 "이런 질문에는 이 tool을 호출하라"고 지시하지 않는다. Week 3의
  `WEEK03_TOOL_CALL_PROMPT`가 미구현 tool을 prompt로 추천하지 않은 것과 같은 이유다 — 구현되지
  않은 tool을 호출하도록 지시하면 agent가 그 tool을 부르고 깨진 결과를 그대로 사용자에게 전달한다.

# 주차 간 시스템 프롬프트 정리 (Week 4 작업 시작 전 먼저 처리)

`week04_prompt_parts()`는 `*week03_prompt_parts()`를 그대로 이어받는다. 그런데 그 안에는 이번
주차에 실제로 구현하는 기능과 모순되는 두 문장이 이미 들어 있다. `student_parts/claude.md`의
"주차 간 system prompt 누적과 tool 충돌 처리" 원칙에 따라, Week 4 전용 지시를 추가하기 전에
이 문장들부터 정리한다.

1. `week02_prompt_parts()` (`student_parts/week02_structure_natural_language_requests.py`)의 문장:
   > "Week 2에서는 아직 SQLite 저장, RAG 검색, 외부 멤버 일정 조율을 하지 않는다. 구조화 결과를
   > DB에 저장하거나 다른 사람 일정과 조율하는 동작은 이번 주차 범위 밖이다."

   이 문장은 Week 3에서 SQLite 저장이 이미 구현되면서 절반이 거짓 지시가 되었고(당시엔 놓친
   부분이다), Week 4에서 RAG 검색까지 구현되면 "SQLite 저장, RAG 검색" 언급이 전부 거짓으로 남는다.
   `week04_prompt_parts()`를 건드리기 전에 이 문장에서 **"SQLite 저장, RAG 검색" 부분을 지우고
   "외부 멤버 일정 조율"만 남긴다** (외부 멤버 일정 조율은 Week 4에도 여전히 범위 밖이므로 이
   부분은 유지한다).

2. `week03_prompt_parts()` (`student_parts/week03_build_nanas_logbook.py`)의 문장:
   > "Week 3에서는 개인 RAG와 외부 멤버 일정 조율을 처리하지 않는다."

   이 문장은 Week 4가 개인 RAG(`search_personal_references`/`search_saved_requests`)를 구현하는
   순간 거짓 지시가 되어, 같은 system prompt 안에 "RAG를 하지 않는다"와 "이 RAG tool을 써라"가
   동시에 존재하게 된다. `week04_prompt_parts()`를 건드리기 전에 이 문장에서 **"개인 RAG" 언급을
   지우고 "외부 멤버 일정 조율"만 남긴다** (외부 멤버 일정 조율은 Week 4 범위에도 없으므로 이
   부분은 유지한다).

이 두 문장은 `week01_tool_call_prompt()`처럼 별도 함수로 분리할 필요 없이, 다음 주차부터도 계속
사실로 남는 "외부 멤버 일정 조율은 아직 하지 않는다"는 내용만 남기는 최소 수정으로 충분하다.
(문장 전체를 삭제하면 그 범위 제한 자체가 사라지므로, 삭제가 아니라 이미 구현된 부분만 도려내는
방식으로 고친다.)

# 공통 규칙 (메인과제 6개 함수 모두 해당)

- 반환값은 `json_payload(payload)`로 감싼다(이 프로젝트의 `_json` 역할과 동일한 helper다 —
  `student_parts/claude.md`의 "tool 반환값이 깨지지 않도록 하는 방어" 참고).
- `top_k`/`limit` 값은 tool 본문에서 `safe_limit(limit, default=..., maximum=...)`로 보정한다.
  각 입력 스키마(`SearchPersonalReferencesInput.top_k`, `SearchSavedRequestsInput.top_k`)가 이미
  Pydantic `Field(ge=..., le=...)`로 1차 검증을 하지만, `safe_limit`은 그 값을 다시 한 번 안전한
  정수로 정리하는 이 프로젝트의 일관된 방어 패턴이다(Week 3의 `SaveStructuredRequestInput` 재검증과
  같은 이유).
- 이미 같은 기능을 하는 함수/저장소 메서드가 있으면 직접 재구현하지 않고 그대로 호출해서 쓴다.
  (`PersonalReferenceStore.add_personal_reference`, `PersonalReferenceStore.search_personal_references`,
  `AppSQLiteStore.search_saved_requests`)

# 함수별 구현 명세

## add_personal_reference_dict(reference_store, *, title, content, tags=None) -> dict

- `tags`가 `None`이면 빈 list로 바꾼 뒤 `reference_store.add_personal_reference(title, content, tags)`를
  호출하고, 그 반환값을 그대로 돌려준다(가공하지 않는다).
- `PersonalReferenceStore.add_personal_reference(...)`가 이미 아래 형태의 dict를 반환한다
  (`fixed/reference_store.py`):
  ```python
  {
      "reference_id": str,
      "title": str,
      "content": str,
      "tags": list[str],
      "backend": {
          "vector_store": "chromadb",
          "embedding_provider": "openai",
          "embedding_model": str,
          "embedding_base_url": str,
          "collection_name": str,
          "chroma_dir": str,
      },
  }
  ```

## add_personal_reference(title, content, tags=None) -> str (`@tool`)

- `add_personal_reference_dict(REFERENCE_STORE, title=title, content=content, tags=tags)`를 호출해
  위 dict를 받는다.
- 그 dict에서 `"backend"` 키를 꺼내 `reference_backend`로, 나머지 필드(`reference_id`/`title`/
  `content`/`tags`)를 `reference`로 묶어 아래 형태의 payload를 만들고 `json_payload(...)`로 감싸
  반환한다.
  ```python
  {
      "ok": True,
      "tool_name": "add_personal_reference",
      "reference_backend": {...},   # add_personal_reference_dict가 반환한 "backend" 값
      "reference": {
          "reference_id": str,
          "title": str,
          "content": str,
          "tags": list[str],
      },
  }
  ```

## search_personal_reference_hits(reference_store, *, query, top_k=2) -> list[dict]

- `reference_store.search_personal_references(query, limit=top_k)`를 호출한다. 이 메서드는 이미
  아래 형태의 hit list를 반환한다(`fixed/reference_store.py`):
  ```python
  [{"id": str, "title": str, "content": str, "tags": str, "distance": float}, ...]
  ```
  (`tags`는 저장 시 `",".join(tags)`로 합쳐진 콤마 문자열이다.)
- 이 함수는 위 결과를 tool이 바로 반환하기 쉬운 구조로 재정리한다. 각 hit을 아래 형태로 바꾼다.
  ```python
  {
      "id": str,
      "content": str,
      "distance": float,
      "metadata": {
          "title": str,
          "tags": list[str],   # 콤마 문자열을 ","로 split하고 빈 문자열은 제거해 list로 되돌린다
      },
  }
  ```

## search_personal_references(query, top_k=2) -> str (`@tool`)

- `top_k`를 `safe_limit(top_k, default=2, maximum=20)`로 보정한다(`SearchPersonalReferencesInput`의
  `le=20`과 맞춘다).
- `search_personal_reference_hits(REFERENCE_STORE, query=query, top_k=<보정값>)`를 호출해 얻은
  list를 `hits`로 담아 반환한다.
  ```python
  {"ok": True, "tool_name": "search_personal_references", "hits": [...]}
  ```

## search_saved_request_rows(sqlite_store, *, query, top_k=3) -> list[dict]

- `sqlite_store.search_saved_requests(query, limit=top_k)`를 호출하고 그 결과를 그대로 반환한다
  (kind 필터는 넘기지 않는다 — 전체 kind 대상 텍스트 검색이다). 검색 결과가 없으면 빈 list를
  그대로 반환한다.
- `AppSQLiteStore.search_saved_requests(...)`는 `structured_requests` 테이블 row를 그대로
  반환한다(`fixed/app_store.py`): `request_id`/`kind`/`title`/`date`/`start_time`/`end_time`/
  `members_json`/`priority`/`reason`/`raw_json`/`created_at`.

## search_saved_requests(query, top_k=3) -> str (`@tool`)

- `top_k`를 `safe_limit(top_k, default=3, maximum=50)`로 보정한다(`SearchSavedRequestsInput`의
  `le=50`과 맞춘다).
- `search_saved_request_rows(SQLITE_STORE, query=query, top_k=<보정값>)`를 호출해 얻은 list를
  `rows`로 담아 반환한다. 결과가 없으면 `rows: []`를 그대로 반환한다(에러로 취급하지 않는다).
  ```python
  {"ok": True, "tool_name": "search_saved_requests", "rows": [...]}
  ```

# week04_prompt_parts() 구현 명세

`week03_prompt_parts()`가 반환한 list 뒤에(위 "주차 간 시스템 프롬프트 정리"를 먼저 적용한
`week03_prompt_parts()`/`week02_prompt_parts()` 기준으로), 아래 내용이 드러나는 문자열 조각(들)을
이어 붙인다.

- 개인적인 선호/메모/참고사항("나는 오전에 집중이 잘 된다", "점심시간은 비워둔다" 같은 성격의
  질문이나 "그건 어떻게 하기로 했었지?" 같은 과거 메모 질의)에는 `search_personal_references`를
  호출해 근거로 삼는다.
- 저장된 일정/할 일/알림의 제목이나 키워드로 찾는 질문("코칭 관련해서 저장한 거 있어?" 처럼
  날짜/기간이 아니라 내용으로 찾는 질문)에는 `search_saved_requests`를 호출한다. 날짜/기간으로
  조회하는 질문(`personal_list_saved_schedules`가 이미 처리하는 영역)과 혼동하지 않는다 — 두
  tool은 "언제"가 아니라 "무엇을 찾는가"가 다르다.
- 새 참고자료를 등록해 달라는 요청("이거 기억해 둬", "내 선호는 이거야" 등, 일정/할 일/알림이 아닌
  자유 형식 메모)에는 `add_personal_reference`를 호출한다. `save_structured_request`(Week 3, 구조화된
  일정/할 일/알림 저장)와 역할이 다르다는 것을 명시한다 — 구조화된 일정/할 일/알림은 여전히 Week 3
  tool로 저장한다.
- 검색 결과가 비어 있으면 근거 없이 답을 지어내지 않고, 참고자료/저장 기록이 없다고 답한다.

# 추가 과제 관련 안내

`search_conversation_messages`/`search_nana_memory`는 아직 본문이 `# TODO`인 추가 과제다. 두
tool은 이미 `week04_tools()`(`search_conversation_messages`)와 파일에 노출돼 있으므로, 구현되지
않은 상태에서 이 문서 범위 밖의 지시(prompt 라우팅 등)를 추가하면 위 "하지 말아야 할 것"에서 설명한
문제가 그대로 생긴다. 이 tool들을 실제로 구현하는 시점에 별도 문서/추가 절로 라우팅 규칙을 정리한다.

# 참고자료

- `fixed/reference_store.py`의 `PersonalReferenceStore.add_personal_reference` /
  `search_personal_references`: 개인 참고자료 저장·검색을 실제로 수행하는 메서드.
- `fixed/app_store.py`의 `AppSQLiteStore.search_saved_requests`(약 454번째 줄): `structured_requests`
  테이블에 대한 단순 LIKE 검색.
- `student_parts/week03_build_nanas_logbook.py`의 `json_payload`/`tool_result` 패턴과
  `WEEK03_TOOL_CALL_PROMPT`: 이번 문서의 tool 응답 포맷·prompt 라우팅 스타일의 참고 예시.
- `student_parts/week02_structure_natural_language_requests.py`의 `week02_prompt_parts()`,
  `student_parts/week03_build_nanas_logbook.py`의 `week03_prompt_parts()`: 위 "주차 간 시스템
  프롬프트 정리" 항목이 고치라고 지시하는 실제 위치.

# 검증 방법

- `./run.sh --week4`로 실행한 뒤 "나는 오전에 집중이 잘 돼"처럼 개인 선호를 저장해 달라고 요청하고,
  trace에서 `add_personal_reference`가 호출되며 결과 JSON에 `reference_backend`/`reference` 키가
  있는지 확인한다.
- 방금 등록한 내용과 관련된 질문을 입력해 `search_personal_references`가 호출되고, 결과 JSON의
  top-level 키가 `hits`인지, 각 hit에 `id`/`content`/`distance`/`metadata.title`/`metadata.tags`가
  있는지 확인한다.
- Week 3에서 저장해 둔 일정/할 일의 제목 키워드로 질문해 `search_saved_requests`가 호출되고, 결과
  JSON의 top-level 키가 `rows`인지 확인한다.
- 일치하는 참고자료/저장 기록이 없는 질문을 넣었을 때 `hits`/`rows`가 빈 list로 오고, 답변이 없는
  내용을 지어내지 않는지 확인한다.
- Week 1-3에서 이미 확인하던 기능(개인 일정 CRUD, 구조화 응답, SQLite 저장/조회)이 Week 4 tool
  추가 이후에도 그대로 동작하는지 회귀 확인한다.
