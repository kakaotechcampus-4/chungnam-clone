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

- [ ] **Milestone 1: Plan 수립 및 아키텍처 검수**
  - 클로드 코드가 작성한 Week 4 `plan.md` 검수 및 승인
- [ ] **Milestone 2: 개인 참고자료 저장/검색 도구 구현**
  - `add_personal_reference_dict` 및 `add_personal_reference` 도구 구현
  - `search_personal_reference_hits` 및 `search_personal_references` 도구 구현 (`{"hits": [...]}` 스키마)
- [ ] **Milestone 3: SQLite 저장 요청 검색 도구 구현**
  - `search_saved_request_rows` 및 `search_saved_requests` 도구 구현 (`{"rows": [...]}` 스키마)
- [ ] **Milestone 4: 대화 이력 RAG 도구 구현**
  - `search_conversation_messages_hits` 및 `search_conversation_messages` 도구 구현
- [ ] **Milestone 5: 프롬프트 누적 & Week 04 Agent 조립**
  - `week04_prompt_parts()` 구현 및 `build_week04_agent()` 연동
- [ ] **Milestone 6: 전체 자가 검증 및 테스트 통과**
  - `./run.sh --week4` 자가 테스트 통과 및 Trace 검증

---

## 3. 단계별 코드 구현 분석 및 자습 가이드 (Step-by-Step Code Walkthrough)

*(클로드 코드가 `plan.md`를 제출하고 단계를 진행할 때마다 각 함수별 작성 방식, 코드 라인 해설, 변수 구조, 호출 흐름을 상세하게 지속적으로 업데이트할 예정입니다.)*

---

## 4. 자습용 완성 코드 체크리스트 및 구현 패턴 정리

*(전체 완료 후 혼자서 작성할 때 참고할 수 있는 템플릿 코드가 이곳에 정리됩니다.)*
