# Week 04 구현 계획 — Nana's Memory 검색 (RAG 출처 분리)

## Context — 왜 이 작업을 하는가

Week 3까지는 Nana가 SQLite에 일정/할 일을 영속 저장하는 것까지 구현했다. Week 4의 과제는
"저장된 기억을 다시 꺼내 쓰는" 단계로, 단일 RAG 함수 하나로 모든 걸 검색하게 두지 않고
**데이터 출처별로 전용 검색 tool을 분리**해서 LLM이 질문 성격에 맞는 도구를 스스로 고르게
만드는 것이다. 대상 파일 `student_parts/week04_retrieve_nanas_memory.py`는 이미 입력
스키마·모듈 싱글턴(`REFERENCE_STORE`/`SQLITE_STORE`/`CONVERSATION_RAG_STORE`)·에이전트 조립
함수(`week04_tools`, `build_week04_agent` 등)가 완성돼 있고, helper 5개·tool 5개·
`week04_prompt_parts()`만 TODO 스텁으로 남아 있다. 이 문서는 그 스텁을 채우기 위한 설계와
가정을 정리한다.

## 구현 대상 파일

- **수정 파일은 이 한 곳뿐**: `student_parts/week04_retrieve_nanas_memory.py`
- 의존 파일(읽기 전용, 이미 완성됨):
  - `fixed/reference_store.py` — `PersonalReferenceStore`
  - `fixed/app_store.py` — `AppSQLiteStore`
  - `fixed/conversation_rag_store.py` — `ConversationRAGStore`
  - `fixed/session_scope.py` — `current_session_scope`, `DEFAULT_SESSION_SCOPE`
  - `student_parts/week03_build_nanas_logbook.py` — `week03_prompt_parts`, `week03_tools`

## 반환 계약 (반드시 준수)

- 모든 tool 반환은 `json_payload(...)`로 직렬화한다 (`ensure_ascii=False`, 한글 보존).
- top-level 키는 출처별로 고정한다: 개인 참고자료 검색 → `{"hits": [...]}`, SQLite 저장
  기록 검색 → `{"rows": [...]}`.
- `top_k`/`limit`은 tool 내부에서 `safe_limit(...)`으로 안전 범위 보정한다.
- `tags`는 `tags or []`로 `None` 방어한다.
- 프롬프트는 **append만** 한다 (`[*week03_prompt_parts(), <신규>]`) — 이전 주차 프롬프트를
  덮어쓰지 않는다.

## 스토어 시그니처 (호출 대상, 확인 완료)

- `REFERENCE_STORE.add_personal_reference(title, content, tags=None) -> dict`
  → `{reference_id, title, content, tags(list), backend(dict)}`
- `REFERENCE_STORE.search_personal_references(query, limit=3) -> list[dict]`
  → 각 hit `{id, title, content, tags(콤마결합 str), distance}` (중첩 `metadata` 없음)
- `SQLITE_STORE.search_saved_requests(query, kind=None, limit=5) -> list[dict]`
  → raw DB row 그대로 (`raw_json`/`members_json`은 JSON 문자열). **`limit`은 키워드로 전달**
  (`kind`를 건너뛰기 위해).
- `CONVERSATION_RAG_STORE.sync_from_sqlite(SQLITE_STORE) -> {upserted, skipped, deleted, total}`
- `CONVERSATION_RAG_STORE.search(*, query, top_k=5, exclude_conversation_id=None,
  conversation_id=None) -> list[dict]` (키워드 전용 인자) → hit에 중첩 `metadata` 포함
- `CONVERSATION_RAG_STORE.context_from_hits(hits) -> str`, `.backend_info() -> dict`
- 현재 대화 식별: `current_session_scope()` (활성 대화 없으면 `DEFAULT_SESSION_SCOPE` 반환)

## 구현 항목

### 1회차 — 개인 참고자료 + SQLite 저장 기록 (helper 3 + tool 3)

**`add_personal_reference_dict(reference_store, *, title, content, tags=None)`**
- `tags = tags or []`
- `record = reference_store.add_personal_reference(title, content, tags)`
- 반환: `{"reference_backend": record["backend"], "reference": record}`

**`search_personal_reference_hits(reference_store, *, query, top_k=2)`**
- `limit = safe_limit(top_k, default=2, maximum=20)`
- `raw = reference_store.search_personal_references(query, limit)`
- 각 raw hit을 계약 형태로 재구성 (스토어 hit은 flat이므로 metadata를 조립):
  `{"id": h["id"], "content": h["content"], "distance": h["distance"],
  "metadata": {"title": h.get("title", ""), "tags": h.get("tags", "")}}`

**`search_saved_request_rows(sqlite_store, *, query, top_k=3)`**
- `limit = safe_limit(top_k, default=3, maximum=50)`
- `return sqlite_store.search_saved_requests(query, limit=limit)` (결과 없으면 `[]` 그대로)

**`@tool add_personal_reference(title, content, tags=None)`**
→ `json_payload(add_personal_reference_dict(REFERENCE_STORE, title=title, content=content, tags=tags))`

**`@tool search_personal_references(query, top_k=2)`**
→ `json_payload({"hits": search_personal_reference_hits(REFERENCE_STORE, query=query, top_k=top_k)})`

**`@tool search_saved_requests(query, top_k=3)`**
→ `json_payload({"rows": search_saved_request_rows(SQLITE_STORE, query=query, top_k=top_k)})`

### 2회차 — 대화 이력 RAG (helper 2 + tool 1)

**`search_conversation_messages_dict(sqlite_store, conversation_rag_store, *, query, top_k=5, conversation_id=None)`**
- `limit = safe_limit(top_k, default=5, maximum=50)`
- lazy sync: `sync = conversation_rag_store.sync_from_sqlite(sqlite_store)`
- 현재 대화 제외 처리:
  - `conversation_id`가 주어지면 → `search(query=query, top_k=limit, conversation_id=conversation_id)`
  - 아니면 → `scope = current_session_scope()`;
    `exclude = None if scope == DEFAULT_SESSION_SCOPE else scope`;
    `search(query=query, top_k=limit, exclude_conversation_id=exclude)`
- `context = conversation_rag_store.context_from_hits(hits)`
- 반환: `{"hits": hits, "rows": hits, "context": context,
  "rag_backend": conversation_rag_store.backend_info(), "sync": sync}`
  (계약상 `hits`와 `rows`에 동일 결과를 담는다)

**`search_conversation_message_rows(sqlite_store, *, query, top_k=5, conversation_id=None)`**
- 위 dict helper 호출 후 `["hits"]`만 반환하는 내부 helper.

**`@tool search_conversation_messages(query, top_k=5, conversation_id=None)`**
→ `json_payload(search_conversation_messages_dict(SQLITE_STORE, CONVERSATION_RAG_STORE, query=query, top_k=top_k, conversation_id=conversation_id))`

### `search_nana_memory` (구버전 호환 통합 검색 — 구현 포함)

- CLAUDE.md의 필수 4개 도구에는 없지만, 파일에 stub으로 남아 있고 기존 trace/테스트 호환을
  위해 구현하기로 결정했다 (에이전트에는 `week04_tools()`를 통해 노출하지 않음, 기존 스펙 유지).
- `safe = safe_limit(limit, default=5, maximum=20)`
- 개인 참고자료 hit: `search_personal_reference_hits(REFERENCE_STORE, query=query, top_k=safe)`
- SQLite 일정: `SQLITE_STORE.list_schedules(limit=safe, date_from=date_from, date_to=date_to)`,
  `attendee` 지정 시 `_decode_attendees`로 디코딩한 attendees에 포함되는 row만 필터
- 두 출처를 사람이 읽기 쉬운 `context` 문자열로 합쳐 반환:
  `{"query": query, "context": context, "reference_hits": ..., "schedule_rows": ...}`

### `week04_prompt_parts()`

- 모듈 상수 `WEEK04_MEMORY_PROMPT`를 신설하고 `[*week03_prompt_parts(), WEEK04_MEMORY_PROMPT]`
  형태로 append한다.
- 세 출처를 명확히 구분해 라우팅 지시:
  - `search_personal_references` → 사용자가 적어 둔 메모/참고자료/자연어로 저장한 사실
  - `search_saved_requests` → SQLite에 저장된 구조화 일정/할 일/알림
    (kind: personal_schedule, group_schedule, todo, reminder)
  - `search_conversation_messages` → 앱에 저장된 과거 일반 채팅 발화(대화 이력 RAG)
  - 질문 성격에 따라 하나 또는 여러 도구를 선택하고, 반환된 `hits`/`rows`를 근거로 답하며
    assistant 발화만으로 사실을 확정하지 않는다.

## 검증 (Test & Feedback Loop)

1. `./run.sh --week4` 실행 → import 에러 없이 에이전트가 뜨는지 확인.
2. Trace 상 도구 라우팅 확인:
   - 참고자료 추가 후 관련 질문 → `search_personal_references` 호출, top-level `hits` 확인.
   - 저장된 일정/할 일 질문 → `search_saved_requests` 호출, top-level `rows` 확인
     (없으면 `rows: []`).
   - 과거 대화 관련 질문 → `search_conversation_messages` 호출,
     `hits/rows/context/rag_backend/sync` 키 확인, 현재 대화가 결과에 섞이지 않는지 확인.
3. 한글 JSON이 `\uXXXX`로 깨지지 않고 그대로 나오는지 확인.
4. `PROXY_TOKEN` 없으면 embedding 호출에서 `RuntimeError`가 나므로, RAG 검증은 `.env`에
   유효한 토큰이 있는 상태에서 수행한다.
5. 발생한 에러·예외·원인·해결은 `error-log.md`에 실시간 기록한다 (CLAUDE.md 원칙 5).

## Known Gotchas

- `search_saved_requests`는 `(query, kind=None, limit=5)` — `limit`을 **키워드**로 전달.
- `ConversationRAGStore.search`는 **키워드 전용** 인자.
- 개인 참고자료 검색 hit의 `tags`는 콤마결합 **문자열**(list 아님) — 그대로 `metadata.tags`에
  넣는다.
- 프롬프트는 **append만**, 이전 주차 내용 덮어쓰기 금지.
- 모든 도구 반환은 `json_payload(...)` 경유.
