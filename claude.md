# Nana 프로젝트 — Week 04 (Retrieving Nana's Memory)

## 실행 명령어
```bash
./run.sh --week4
```

## 주요 파일
- **구현 파일**: `student_parts/week04_retrieve_nanas_memory.py`
- **의존 파일**:
  - `fixed/reference_store.py` (`PersonalReferenceStore` - ChromaDB 기반 개인 참고자료)
  - `fixed/app_store.py` (`AppSQLiteStore` - SQLite 기반 일정/할일 기록)
  - `fixed/conversation_rag_store.py` (`ConversationRAGStore` - 대화 이력 검색)
  - `fixed/config.py` (`CONFIG` 환경 설정)
  - `student_parts/week03_build_nanas_logbook.py` (`week03_prompt_parts`, `week03_tools` 상속 및 누적)

## 아키텍처 (RAG 출처 분리)
Week 4의 핵심은 단일 RAG 함수 대신 **데이터 출처별 전용 Retrieval Tool을 분리**하여 LLM이 질문에 맞춰 적절한 도구를 선택하도록 구현하는 것이다.

```
                  [ 사용자 질문 ]
                        ↓
             [ Week 4 Nana Agent ]
      ┌─────────────────┼─────────────────┐
      ↓                 ↓                 ↓
[개인 참고자료 RAG]   [SQLite 저장 검색]  [대화 이력 RAG]
 (ChromaDB Vector)    (AppSQLiteStore)  (ConversationRAG)
  - hits 반환          - rows 반환       - hits 반환
```

## 개발 행동 원칙
1. **Think Before Coding**: 코딩 전 `plan.md`에 설계와 가정을 밝히고 검수 요청할 것.
2. **Simplicity First & Surgical Changes**: 불필요한 추상화 금지, 타겟 함수 본문만 최소/정교 수정.
3. **Goal-Driven & Self Validation**: 테스트 통과를 목표로 자가 검증 수행.
4. **Test & Feedback Loop**: 코드 수정 후 반드시 `./run.sh --week4`를 실행하여 Trace 상의 도구 호출 및 에러 유무를 직접 검증하고 피드백 루프를 수행할 것.
5. **Error Log 작성 필수**: 발생한 모든 에러, 예외 처리, 원인 및 해결 과정은 `error-log.md` 파일에 실시간으로 명확히 기록할 것.

## 구현 대상 도구 및 헬퍼 규격

### 1. 개인 참고자료 도구
- **`add_personal_reference`** (`add_personal_reference_dict` 헬퍼)
  - `title`, `content`, `tags` 수신. `tags`가 `None`이면 빈 리스트(`[]`)로 변환.
  - `REFERENCE_STORE.add_personal_reference` 호출 후 `reference_backend` 및 `reference` dict 포함 응답 반환.
  - top-level 키 제약이 없는 도구이므로, Week 1~3과 동일하게 `week03_build_nanas_logbook.tool_result(...)`로
    감싸 `{"ok": true, "tool_name": "add_personal_reference", ...}` 형태로 반환한다(세 검색 도구와 달리
    `ok`/`tool_name`을 추가해도 계약 위반이 아니다).
- **`search_personal_references`** (`search_personal_reference_hits` 헬퍼)
  - `query`, `top_k` 수신. `top_k` 범위는 `SearchPersonalReferencesInput`의 `Field(ge=1, le=20)`이
    이미 검증하므로, helper 안에서 `safe_limit`을 또 호출해 중복 클램프하지 않는다.
  - 결과는 반드시 **top-level 키 `{"hits": [...]}`** 형태의 JSON으로 반환.
  - 각 hit 요소: `id`, `content`, `distance`, `metadata` (title/tags 포함).

### 2. SQLite 저장 기록 검색 도구
- **`search_saved_requests`** (`search_saved_request_rows` 헬퍼)
  - `query`, `top_k` 수신. `top_k` 범위는 `SearchSavedRequestsInput`의 `Field(ge=1, le=50)`이
    이미 검증하므로, helper 안에서 `safe_limit`을 또 호출하지 않는다.
  - `SQLITE_STORE.search_saved_requests(query, limit=top_k)` 호출.
  - 결과는 반드시 **top-level 키 `{"rows": [...]}`** 형태의 JSON으로 반환. 데이터 없으면 `{"rows": []}`.

### 3. 대화 이력 RAG 도구
- **`search_conversation_messages`** (`search_conversation_messages_dict` 헬퍼)
  - 대화 기록을 `ConversationRAGStore`에 lazy sync 후 현재 대화를 제외하여 검색.
  - `top_k` 범위는 `SearchConversationMessagesInput`의 `Field(ge=1, le=50)`이 이미 검증하므로,
    helper 안에서 `safe_limit`을 또 호출하지 않는다.
  - 결과를 **`{"hits": [...]}`** JSON으로 반환.

### 4. 프롬프트 및 에이전트 조립
- **`week04_prompt_parts()`**: 기존 Week 1~3 프롬프트 위에 Week 4 RAG 지침 누적(Append).
- **`build_week04_agent()`**: Week 1~4 도구를 모아 에이전트 생성.

## 절대 금지 / 핵심 주의사항 (Known Gotchas)
- **Top-level JSON 키 반환 규격 준수**:
  - 개인 참고자료 검색: `{"hits": [...]}`
  - SQLite 기록 검색: `{"rows": [...]}`
- **안전한 Limit 제한**: `top_k`/`limit`의 범위는 tool의 Pydantic `args_schema`에 선언한
  `Field(ge=.., le=..)`가 1차로 강제한다. helper의 유일한 호출 경로가 이 Pydantic 검증을 거친
  tool뿐이라면, helper 안에서 `safe_limit(limit/top_k)`을 또 호출해 중복 클램프하지 않는다.
  Pydantic 검증 없이 helper가 직접 호출될 수 있는 경로가 생기는 경우에만 `safe_limit()`을 쓴다.
- **`tags` 처리**: `tags=None` 입력 시 예외 방지를 위해 `tags = tags or []` 처리.
- **유니코드 한글 보존**: JSON 반환 시 반드시 `json_payload(...)` 헬퍼를 사용하여 직렬화.
- **프롬프트 덮어쓰기 금지**: `week04_prompt_parts()` 작성 시 이전 주차 프롬프트를 덮어쓰지 말고 리스트에 추가(Append).
