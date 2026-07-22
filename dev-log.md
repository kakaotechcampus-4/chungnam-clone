# Nana 프로젝트 — Week 04 개발 및 자습용 기술 통합 기록 (dev-log.md)

> **문서 작성 목적**: 클로드 코드(Claude Code)가 수행한 Week 04 구현 결과물과 설계 방식을 상세히 기록 및 분석하여, 외부 검색이나 다른 자료 없이 이 문서만으로도 **Week 04 RAG 시스템의 작동 원리를 완벽히 습득하고 동일한 코드를 직접 처음부터 재현**할 수 있도록 만든 자습용 통합 가이드입니다.

---

## 1. Week 04 아키텍처 및 핵심 학습 개념 (Knowledge Base)

### 1.1 왜 "출처별 Retrieval Tool 분리"인가? (Multi-Source RAG)
기존의 단순 RAG는 모든 문서/기록을 하나의 Vector Store에 몰아넣고 유사도 검색(Vector Similarity Search)을 수행합니다. 하지만 이러한 단일 RAG 방식은 다음과 같은 치명적 한계가 존재합니다.

1. **정확한 키워드/조건 검색의 한계**: 일정 날짜, 할 일 등록 여부, ID 등 정확한 구조화 데이터(Structured Data)는 Vector 거리(Distance) 계산보다 SQL 쿼리(`WHERE`, `LIKE`, `JOIN`)가 훨씬 정확합니다.
2. **데이터 성격의 차이**:
   - **개인 참고자료**: 텍스트 의미(Semantic Meaning) 중심 검색이 유리 $\rightarrow$ **Vector Store (ChromaDB)** 사용
   - **일정/할 일/알림 저장 기록**: 메타데이터 및 키워드/상태 조건 검색이 유리 $\rightarrow$ **Relational DB (SQLite)** 사용
   - **이전 대화 맥락**: 대화 세션 기반의 컨텍스트 복원이 필요 $\rightarrow$ **Conversation RAG (ChromaDB Lazy Sync)** 사용

따라서 LLM 에이전트가 사용자 질문의 의도를 파악하고, 질문에 가장 적합한 검색 도구(Tool)를 직접 선택하여 호출(Tool Calling)하도록 아키텍처를 분리합니다.

---

### 1.2 핵심 개발 지식 상세 설명

#### A. ChromaDB & Vector Embedding (개인 참고자료 검색)
- **Embedding(임베딩)**: 텍스트를 고차원 실수 벡터(e.g. OpenAI `text-embedding-3-small` 1536차원)로 변환하는 과정입니다. 의미가 유사한 텍스트일수록 벡터 공간 상의 거리가 가깝습니다.
- **Distance Metric (거리 측정)**: ChromaDB는 기본적으로 코사인 거리(Cosine Distance) 또는 유클리드 거리를 사용합니다. Distance 값이 **0에 가까울수록 의미적으로 유의미하게 일치**함을 나타냅니다.
- **Metadata Filtering**: 문서의 본문(`content`) 외에 `title`, `tags` 등의 부가 정보를 메타데이터로 함께 저장하여 필터링 및 근거 제시용으로 사용합니다.

#### B. Pydantic V2 Args Schema & Validation
- LangChain의 `@tool(args_schema=...)`은 LLM이 생성한 JSON 인자를 Python 객체로 안전하게 파싱 및 검증합니다.
- `Field(description=...)` 문구는 LLM에게 이 인자가 어떤 역할을 하는지 알려주는 프롬프트 힌트로 작용합니다.

#### C. `safe_limit()` 보정 로직
- LLM이나 사용자가 `top_k`나 `limit` 값으로 음수, 0, 혹은 수백~수천의 너무 큰 값을 전달할 때 발생할 수 있는 메모리 폭증이나 쿼리 오류를 방지하기 위해 최소값(1)과 최대값(maximum, 예: 20) 범위를 강제로 제한(Clamp)하는 안전 래퍼 함수입니다.

#### D. 유니코드 및 한글 보존 (`json_payload`)
- Python의 기본 `json.dumps()`는 한글을 `\uac00` 형태의 유니코드 이스케이프 시퀀스로 변환합니다. 이는 LLM의 토큰 소비를 불필요하게 늘리고 가독성을 떨어뜨립니다.
- `json.dumps(..., ensure_ascii=False)` 설정을 적용한 `json_payload()`를 거치면 한글이 원본 문자 그대로 직렬화되어 반환됩니다.

---

## 2. Week 04 마일스톤 및 진행 현황 (Checklist)

- [x] **Milestone 1: Plan 수립 및 아키텍처 검수**
  - 클로드 코드가 작성한 Week 4 `plan.md` 검수 및 승인 완료
- [x] **Milestone 2: 개인 참고자료 저장/검색 도구 구현**
  - `add_personal_reference_dict` 및 `add_personal_reference` 도구 구현 완료
  - `search_personal_reference_hits` 및 `search_personal_references` 도구 구현 (`{"hits": [...]}` 스키마) 완료
- [x] **Milestone 3: SQLite 저장 요청 검색 도구 구현**
  - `search_saved_request_rows` 및 `search_saved_requests` 도구 구현 (`{"rows": [...]}` 스키마) 완료
- [x] **Milestone 4: 대화 이력 RAG 도구 구현**
  - `search_conversation_messages_dict` 및 `search_conversation_messages` 도구 구현 완료
- [x] **Milestone 5: 통합 구버전 호환 도구 구현**
  - `search_nana_memory` 호환 도구 본문 완성
- [x] **Milestone 6: 프롬프트 누적 & Week 04 Agent 조립**
  - `week04_prompt_parts()` 구현 및 `build_week04_agent()` 연동 완료
- [x] **Milestone 7: E2E 통합 라우팅 검증 및 error-log.md 분석**
  - 출처별 3종 발화 E2E 테스트 통과, 경계 사례 분석, DB clean-up 완료

---

## 3. 단계별 코드 구현 상세 분석 및 자습 가이드

### 3.1 개인 참고자료 추가 (`add_personal_reference_dict` & `add_personal_reference`)

```python
def add_personal_reference_dict(
    reference_store: PersonalReferenceStore,
    *,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    tags = tags or [] # tags가 None일 경우 빈 리스트 [] 방어
    reference = reference_store.add_personal_reference(title, content, tags)
    return {"reference_backend": reference.get("backend"), "reference": reference}

@tool(args_schema=AddPersonalReferenceInput)
def add_personal_reference(title: str, content: str, tags: list[str] | None = None) -> str:
    payload = add_personal_reference_dict(REFERENCE_STORE, title=title, content=content, tags=tags)
    return json_payload(payload)
```
- **원리 해설**: `tags` 인자가 `None`으로 들어올 수 있으므로 `tags = tags or []` 패턴을 사용해 리스트 타입을 유지합니다.
- **반환 구조**: `add_personal_reference` 도구는 `reference_backend`와 `reference` 객체를 갖는 JSON 포맷으로 직렬화되어 에이전트에게 전달됩니다.

---

### 3.2 개인 참고자료 검색 (`search_personal_reference_hits` & `search_personal_references`)

```python
def search_personal_reference_hits(
    reference_store: PersonalReferenceStore,
    *,
    query: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    limit = safe_limit(top_k, default=2, maximum=20)
    raw_hits = reference_store.search_personal_references(query, limit)
    return [
        {
            "id": hit.get("id"),
            "content": hit.get("content"),
            "distance": hit.get("distance"),
            "metadata": {
                "title": hit.get("title", ""),
                "tags": hit.get("tags", ""),
            },
        }
        for hit in raw_hits
    ]

@tool(args_schema=SearchPersonalReferencesInput)
def search_personal_references(query: str, top_k: int = 2) -> str:
    hits = search_personal_reference_hits(REFERENCE_STORE, query=query, top_k=top_k)
    return json_payload({"hits": hits})
```
- **원리 해설**: ChromaDB의 flat한 딕셔너리 구조(`title`, `tags`가 평평하게 들어있는 구조)를 계약 조건에 맞춰 중첩 메타데이터 객체(`"metadata": {"title": ..., "tags": ...}`)로 재구성(Normalization)합니다.
- **계약 준수**: 도구의 최상위 반환 포맷은 반드시 `{"hits": [...]}` 스키마를 준수합니다.

---

### 3.3 SQLite 저장 기록 검색 (`search_saved_request_rows` & `search_saved_requests`)

```python
def search_saved_request_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    limit = safe_limit(top_k, default=3, maximum=50)
    return sqlite_store.search_saved_requests(query, limit=limit)

@tool(args_schema=SearchSavedRequestsInput)
def search_saved_requests(query: str, top_k: int = 3) -> str:
    rows = search_saved_request_rows(SQLITE_STORE, query=query, top_k=top_k)
    return json_payload({"rows": rows})
```
- **원리 해설**: `AppSQLiteStore.search_saved_requests`를 호출할 때 `limit`을 키워드 인자(`limit=limit`)로 명시하여 넘깁니다.
- **계약 준수**: 최상위 반환 포맷은 반드시 `{"rows": [...]}` 스키마를 준수합니다.

---

### 3.4 대화 이력 RAG (`search_conversation_messages_dict` & `search_conversation_messages`)

```python
def search_conversation_messages_dict(
    sqlite_store: AppSQLiteStore,
    conversation_rag_store: ConversationRAGStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    limit = safe_limit(top_k, default=5, maximum=50)
    sync = conversation_rag_store.sync_from_sqlite(sqlite_store) # Lazy Sync
    if conversation_id:
        hits = conversation_rag_store.search(query=query, top_k=limit, conversation_id=conversation_id)
    else:
        scope = current_session_scope()
        exclude = None if scope == DEFAULT_SESSION_SCOPE else scope
        hits = conversation_rag_store.search(query=query, top_k=limit, exclude_conversation_id=exclude)
    context = conversation_rag_store.context_from_hits(hits)
    return {
        "hits": hits,
        "rows": hits,
        "context": context,
        "rag_backend": conversation_rag_store.backend_info(),
        "sync": sync,
    }
```
- **원리 해설**:
  1. `sync_from_sqlite`: 검색 실행 직전에 SQLite의 대화 이력을 ChromaDB에 동기화(Lazy Sync)합니다.
  2. **현재 대화 제외 필터**: `current_session_scope()`를 조회하여 현재 활성화된 센션/대화는 검색 대상에서 제외(`exclude_conversation_id`)함으로써, 방금 한 대화가 과거 기록으로 오인되는 것을 방지합니다.

---

## 4. E2E 라우팅 테스트 및 경계 사례 분석 (E2E Test Analysis)

### 4.1 출처별 Tool 라우팅 테스트 결과
1. **개인 참고자료 발화** ("회의 시간대 선호 사항 알려줘"):
   - `search_personal_references` 도구 라우팅 100% 성공.
2. **SQLite 영속 기록 발화** ("독후감 관련 저장 기록 검색"):
   - `search_saved_requests` 도구 라우팅 100% 성공.
3. **과거 대화 발화** ("무슨 얘기를 나눴는지 대화 내용을 보여줘", "내가 정확히 뭐라고 말했었는지 대화 내용을 찾아줘"):
   - `search_conversation_messages` 도구 라우팅 100% 성공.

### 4.2 경계 사례(Edge Case) 발견 및 트러블슈팅 분석
- **문제 발생**: "헬스장 일정 잡아달라고 요청했던 대화 내용을 찾아줘" 발화 입력 시 `search_saved_requests`가 호출됨.
- **원인 분석**: 한 문장 내에 "일정"(구조화 키워드)과 "대화 내용"(대화 키워드)이 혼재되어, LLM이 일정 DB의 "헬스장 일정" 자체를 찾는 것으로 오해함.
- **검증 및 대안**: "무슨 말을 했었는지", "대화 내용을 다시 보여줘" 등 발화 자체를 묻는 표현으로 정정했을 때 `search_conversation_messages`로 정확히 전환됨을 재확인.
- **학습 포인트**: LLM Tool Calling 구현 시, 키워드가 중첩되는 다의적 질문에 대해서는 System Prompt 상에 판단 우선순위 규칙을 명시해 두는 것이 라우팅 안정성을 높입니다.
