# Week 4 — 나나가 기억을 찾아오다 (Agentic RAG: ChromaDB + SQLite 검색)

> Claude Code **Plan 모드**로 구현 전에 작성·승인한 작업 계획서 원본입니다.
> 코드베이스 탐색(과제 파일 TODO 분석, fixed RAG 인프라 배선 추적, baseline 비교, 강의 노트북 레퍼런스)을 마친 뒤 이 계획대로 진행했습니다.

## Context

4주차: 나나가 저장만 하던 것을 넘어 **필요할 때 기억을 "찾아온다".** 핵심은 *"RAG를 하나의 마법 함수로 보지 않고, 데이터 출처별로 검색 tool을 분리하는 것"*(가이드 원문). 출처 3개:
- **개인 참고자료** → ChromaDB 의미검색(`search_personal_references`) — 임베딩 벡터, 뜻으로 찾음
- **저장된 구조화 기록**(3주차 일정/할일/알림) → SQLite LIKE 검색(`search_saved_requests`)
- **지난 대화 발화** → 대화 단위 ChromaDB RAG(`search_conversation_messages`, 추가과제)

- 수정 파일 하나: `student_parts/week04_retrieve_nanas_memory.py` (377줄, TODO 11곳)
- **store 3개 완성돼 있음**: `REFERENCE_STORE`(fixed/reference_store.py)·`SQLITE_STORE`(app_store.py)·`CONVERSATION_RAG_STORE`(conversation_rag_store.py) — 학생은 이 싱글턴 위의 tool 계층 + 라우팅 프롬프트만 작성
- 라우팅 = **프롬프트+docstring 기반**(라우터 tool 없음). LLM이 질문 성격 보고 tool 선택
- 진행 구조: Phase1 baseline 비교 → Phase2 큰 그림(비유) → Phase3 함수 단위(기능·변수·설계의도 3종). **함수 하나 = 설명 하나 = 커밋 하나.**
- 주석은 협업 관점에서 실무적으로 — 다른 사람과 함께 유지보수한다는 전제로 동료에게 필요한 맥락 위주로 작성.

---

## Phase 1 — baseline 비교·평가

- 새로 온 건 **week03 정답**(`student_parts_baseline/week03...`). 내 week03과 함수 단위 비교 결과: **구조·반환키·시그니처 대부분 동일**. 차이는 내 쪽 개선(삭제 status 3값·delete_all 충돌 거부·빈 봉투 규칙·도구혼동 프롬프트)뿐 — baseline엔 없는 순수 상위호환.
- **week4 호환성 리스크 0**: week04는 week03에서 `week03_tools()`·`week03_prompt_parts()` 딱 2개만 import(둘 다 형태 동일). 개선분은 LLM이 읽는 JSON일 뿐 week04 파이썬이 안 읽음. baseline은 어디서도 import 안 됨(참고용).
- 평가: 코드 유지.

## Phase 2 — 큰 그림 (개념 수업, 코드 0줄)

비유: **나나 = 사서**. 질문이 오면 어느 서가에서 찾을지 먼저 정한다.
- 📚 **의미 서가(ChromaDB 참고자료)**: "발표 언제였지?" 같은 자연어 메모 — 단어가 안 겹쳐도 뜻으로 찾음
- 🗂️ **구조 대장(SQLite 기록)**: "저장한 group_schedule 보여줘" — 3주차에 쌓은 구조화 row를 키워드로
- 🗣️ **회의록 검색(대화 RAG)**: "저번에 뭐 얘기했지?" — 지난 대화를 대화 단위로, 지금 대화는 제외

흐름 4단계: ① 질문 도착 → ② LLM이 프롬프트+docstring 보고 tool 선택 → ③ store가 검색(의미 or 키워드) → ④ hits/rows 근거로 답변.

핵심 개념: 임베딩(문장→벡터), distance 낮을수록 의미 가까움(실측 0.36 vs 0.88), 의미검색 vs 키워드검색(참고자료=의미, saved_requests=LIKE), 임베딩 전용 모델·프록시가 chat과 별도, lazy sync + 현재 대화 제외 이유, 라우팅은 프롬프트가(라우터 없음). 미니 실습: `REFERENCE_STORE`가 seed한 기본 참고자료 3개 조회.

핵심 지도: `@tool(args_schema=...)`(3주차 패턴 재사용), store 싱글턴 주입, `current_session_scope()`(3주차 대화격리)가 이번엔 `exclude_conversation_id`로 쓰임.

## Phase 3 — 함수 단위 구현 (각 단계: 설명→검토→커밋)

각 코드마다 ⑴기능 ⑵주요 변수 ⑶설계의도(대안·수정/삭제 시 문제). tool은 대부분 "헬퍼가 store 호출+정리 → tool이 json_payload로 감쌈" 2단 구조 → 헬퍼+tool을 한 기능 단위로.

**메인과제**
- M1. `add_personal_reference` (헬퍼 `add_personal_reference_dict` + @tool) — 참고자료를 ChromaDB에 저장. tags None→[], 반환에 reference_backend/reference
- M2. `search_personal_references` (헬퍼 `search_personal_reference_hits` + @tool) — 의미검색. store `limit` ← tool `top_k` 매핑, `safe_limit` 클램프, hit를 id/content/distance/metadata(title/tags)로 정리, top-level `{hits}`
- M3. `search_saved_requests` (헬퍼 `search_saved_request_rows` + @tool) — `SQLITE_STORE.search_saved_requests(query, limit)` LIKE 검색, 빈 결과 rows=[], top-level `{rows}`
- M4. `week04_prompt_parts` — 듀얼/트리플 RAG 라우팅의 핵심. `*week03_prompt_parts()` 위에 출처별 tool 선택 규칙 추가 + 3주차의 "RAG 안 함" 문구를 이번 주엔 허용으로 갱신
- M5. 메인 검증(아래 검증 방법)

**추가과제**
- A1. `search_conversation_messages` (헬퍼 `search_conversation_messages_dict` + @tool) — `CONVERSATION_RAG_STORE.sync_from_sqlite()` lazy sync → `.search(query, top_k, exclude_conversation_id=current_session_scope() when conversation_id None)` → hits/rows/context/rag_backend/sync 조립. 현재 대화 제외가 핵심. assistant 발화는 사실로 취급 금지
- A2. 최종 검증 + PR

**남겨두는 것(구현 안 함)**: `search_nana_memory`+`SearchNanaMemoryInput`(호환 stub — week04_tools 미등록, 호출처 0), `search_conversation_message_rows`(미호출 헬퍼). 둘 다 `...` 유지.

## 검증 방법

1. **시나리오 10가지 직접 테스트** — `./run.sh --week4`로 앱을 띄우고 10개 시나리오를 직접 입력. 참고자료/저장기록/지난대화 질문을 섞어, 매번 trace에서 올바른 tool 선택 + hits/rows 근거 답변을 확인. 정상 흐름뿐 아니라 엣지(애매한 질문, 두 출처 모두 필요, 결과 0건, 현재 대화 제외 등)도 포함.
2. **테스트 파일 작성**(`tests/test_week04_memory.py`) — 작은 함수 유닛 테스트부터 시작해 점차 범위를 넓혀감: 헬퍼 단위(add/search 각각) → @tool 단위 → 여러 tool 조합. 올바른 동작 + 엣지 케이스(빈 결과, top_k 경계값, 잘못된 입력, 현재 대화 제외 등)까지 assert. 마커 더미 + 청소(3주차 하네스 패턴 재사용).

## 주의
- ChromaDB 의미검색은 임베딩 프록시 필요 — SQLite와 달리 임베딩 모델 호출이라 오프라인 완전대체 불가. 단 SQLite LIKE 검색은 오프라인 가능
- `REFERENCE_STORE`는 빈 컬렉션이면 기본 참고자료 3개 seed — 유닛 테스트는 마커로 내 것만 구분/청소
- store 반환과 tool 반환 형태 차이 주의(store `limit`/comma-tags → tool `top_k`/metadata 재구성)
- week01~03 파일 수정 안 함(import만). baseline 복사 금지.
