# Error Log

Week 04 구현(`student_parts/week04_retrieve_nanas_memory.py`) 과정에서 발생한 에러/예외와 해결 과정을 기록합니다.

## 2026-07-21 — Week 04 구현 검증

### 1. `ModuleNotFoundError: No module named 'student_parts'`
- **상황**: 구현한 tool/helper를 직접 호출해 검증하기 위해 임시 smoke-test 스크립트를
  `$CLAUDE_JOB_DIR/tmp/smoke_week4.py`에 작성하고 `uv run python <경로>`로 실행.
- **원인**: `student_parts`는 리포 루트를 기준으로 하는 패키지인데, 스크립트가 리포 밖
  경로에 있어 `sys.path`에 리포 루트가 잡히지 않아 import에 실패함.
- **해결**: 스크립트를 리포 루트(`chungnam-clone/`) 안으로 복사한 뒤 그 경로에서
  `uv run python`으로 실행 → import 정상화. 검증 후 임시 스크립트는 삭제해 리포에
  남기지 않음.

### 2. 터미널 출력의 한글 깨짐(모지바케) — 실제 버그 아님
- **상황**: 스모크 테스트에서 `search_personal_references`, `search_conversation_messages`
  등의 JSON 결과를 터미널에 바로 출력했더니 한글 부분이 `����` 형태로 깨져 보임.
- **원인 확인**: `json_payload()`는 `json.dumps(..., ensure_ascii=False)`로 실제 UTF-8
  문자열을 만들고 있었음. 깨짐은 Windows 콘솔의 코드페이지(cp949 등)가 UTF-8 출력을
  잘못 디코딩해서 보여주는 **터미널 표시 문제**였고, 데이터 자체는 손상되지 않음.
- **검증 방법**: `PYTHONIOENCODING=utf-8`로 같은 스크립트를 실행해 결과를 파일로
  리다이렉트한 뒤 파일을 다시 읽어 한글이 정상 표시되는지 확인 → 정상 확인.
- **결론**: 코드 수정 불필요. `json_payload`가 요구사항(한글 보존)대로 동작함을 재확인.

## 스모크 테스트로 확인한 항목 (모두 통과)
- `add_personal_reference` → `reference_backend`/`reference` 키 포함 JSON 반환.
- `search_personal_references` → top-level `hits`, 각 hit에 `id/content/distance/metadata(title,tags)`.
- `search_saved_requests` → top-level `rows` (결과 없으면 `rows: []`).
- `search_conversation_messages` → `hits/rows/context/rag_backend/sync` 키 모두 포함,
  `hits == rows` 확인.
- `search_nana_memory` → `query/context/reference_hits/schedule_rows` 키 포함.
- `week04_tools()`에 `search_nana_memory`가 노출되지 않음(의도된 동작) 확인.
- `week04_prompt_parts()`가 Week 1-3 프롬프트를 덮어쓰지 않고 append했는지 확인(13개 조각).
- `build_week04_agent()`가 정상적으로 `CompiledStateGraph` 인스턴스를 생성함을 확인.

## 뒷정리
- 스모크 테스트 중 실제 ChromaDB `kanana_personal_references_openai` 컬렉션에 테스트용
  참고자료 2건(`ref_f33d1fcfaa`, `ref_0b70aaec56`)이 저장됨. 검증 완료 후
  `collection.delete(ids=[...])`로 제거해 컬렉션을 원래 seed 데이터(3건) 상태로 복원함.

## 2026-07-21 — E2E 통합 테스트 (5단계, `run_active_week_agent(4, ...)` 경유)

`./run.sh --week4`가 내부적으로 호출하는 것과 동일한 실행 경로
(`fixed/week_agent_registry.py`의 `run_active_week_agent` → `AppSQLiteStore`로 실제
대화 생성 → `conversation_session_scope`로 세션 스코프 지정 → 실제 LLM 호출)를 그대로
사용해 3개 출처 질문의 tool 라우팅을 검증함.

### 1차 시도 결과
- 개인 참고자료 질문("회의 시간대 선호 사항") → `search_personal_references` 정확히 호출됨. ✅
- SQLite 저장 기록 질문("'독후감' 관련 저장 기록 검색") → `search_saved_requests` 정확히 호출됨. ✅
- 과거 대화 이력 질문("헬스장 일정 잡아달라고 요청했던 **대화 내용**을 찾아줘") →
  기대와 다르게 `search_saved_requests`가 호출됨(`query: "헬스장 일정"`). ❌

### 원인 분석
- 실패한 질문 문구가 "헬스장 **일정**"(구조화 일정 키워드)과 "대화 내용을 찾아줘"(대화 이력
  키워드)를 동시에 담고 있어, LLM이 검색 대상을 "일정 자체"로 해석해 `search_saved_requests`를
  선택함. 이는 프롬프트 결함이라기보다 질문 자체의 모호성에 가까움.
- 프롬프트(`WEEK04_MEMORY_PROMPT`)나 코드를 먼저 고치지 않고, 모호성을 제거한 두 문구로
  재검증해 원인을 좁힘.

### 재검증 (모호성 제거 후)
- "저번에 나눴던 대화 중에 '팀 스프린트 회의' 준비하면서 무슨 얘기를 나눴는지 **대화 내용**을
  다시 보여줘" → `search_conversation_messages` 정확히 호출됨. ✅
- "예전에 헬스장 갈 일정 얘기했던 그 대화에서 내가 정확히 **뭐라고 말했었는지** 대화 내용을
  찾아줘" → `search_conversation_messages` 정확히 호출됨. ✅

### 결론
- "일정"이라는 단어와 "대화 내용 조회 의도"가 한 문장에 함께 있을 때만 모호하게 라우팅되는
  경계 사례이며, "무슨 말을 했는지/무슨 얘기를 나눴는지"처럼 발화 자체를 묻는 표현이 있으면
  2/2 일관되게 `search_conversation_messages`로 정확히 라우팅됨을 확인함.
- 코드/프롬프트 수정은 하지 않음(실제 결함이 재현되지 않아 수정 대상이 아니라고 판단).
  다만 향후 이 경계 사례가 반복되면 `WEEK04_MEMORY_PROMPT`에 "'일정'과 '대화 내용'이 함께
  언급되면, 사용자가 구조화된 일정 값이 아니라 그때 나눈 말/맥락을 묻는 것인지 먼저 판단하라"는
  규칙을 추가하는 것을 고려할 수 있음.

### 뒷정리
- 테스트에 사용한 대화 5건(conv_793a4ea78f, conv_4d36d629e7, conv_c0e9adc63f,
  conv_1fd3c9963e, conv_9caee53bc2)은 `AppSQLiteStore.delete_conversation`으로 SQLite에서
  삭제하고, 대응하는 ChromaDB conversation chunk(`conversation:<id>`)도 함께 삭제함.
- 임시 테스트 스크립트(`_e2e_week4_tmp.py`, `_e2e_week4_retry_tmp.py`)는 검증 후 리포에서 삭제함.

## 2026-07-22 — 회색지대(Gray-zone) 반복 검증 (6개 시나리오 × 5개 프롬프트 = 30회)

사용자가 로컬에서 실사용 중 "초은이 누나" 일정 검색 실패를 발견해 원인을 분석한 뒤(아래
"사용자 실사용 중 발견" 절 참고), 동일 종류의 조용한 실패가 더 있는지 확인하기 위해
`fixed/agent_runtime.py`의 `AgentRuntime(active_week=4).run_agent(...)`를 그대로 재사용하는
스크립트(`week04_grayzone_harness.py`, 세션 스크래치 디렉터리)로 실제 LLM+tool 호출 30회를
실행함. Gradio UI를 거치지 않지만 UI와 완전히 동일한 실행 경로(세션 스코프, trace 추출)를
사용하므로 결과는 실제 사용 시나리오와 동등함.

### 사용자 실사용 중 발견 — `search_saved_requests` 전체구문 LIKE 매칭 실패
- **증상**: "초은이 누나와 반석역에서 만남" 일정이 저장되어 있는데
  `search_saved_requests(query="초은이 누나 약속")` → `rows: []`. 반면 같은 도구로
  `query="인턴 면접"`은 정상 검출됨.
- **원인**: `fixed/app_store.py:454-476`의 `AppSQLiteStore.search_saved_requests`가
  `token = f"%{query_text}%"`로 쿼리 문자열 **전체**를 하나의 부분 문자열 패턴으로 만들어
  `raw_json/title/reason`에 LIKE 매칭함. 저장된 문구와 쿼리가 글자 그대로 일치해야만
  통과되고, 패러프레이즈("약속" vs 저장된 "만남")는 실패함. **세션/대화 격리 문제가
  아님** — 이 메서드에는 `conversation_id` 필터가 전혀 없어 어느 대화에서 호출하든 전역
  테이블을 동일하게 검색함(다른 세션에서도 "인턴 면접"은 항상 찾아짐을 확인).
- plan.md의 "개선 필요 사항" 절에 후속 개선 항목으로 등록함(이번 스텁 구현 범위 밖).

### 시나리오별 결과 요약 (◯=정상, ✗=실패)

| # | 시나리오 | 결과 | 실패 원인 |
|---|---|---|---|
| 1 | 저장된 일정 자연어 패러프레이즈 검색 | 2/5 ◯, 3/5 ✗ | 위 LIKE 전체구문 매칭 버그 재현 (3건: "초은이 누나랑 약속", "...시간이랑 장소", "반석역에서 누구") |
| 2 | 저장 기록 kind 혼동 (schedule/todo/reminder) | 4/5 ◯, 1/5 ✗ | "오늘 알림 잡아둔 거 있나?" → `list_saved_requests(kind=reminder, date_from=date_to=오늘)`로 조회했으나, 저장된 "영양제 챙겨 먹기" 알림은 `date=null`(매일 반복이라 특정 날짜 없음)이라 날짜 범위 필터에 걸려 누락됨(실제로는 매일 발생하므로 오늘도 해당) |
| 3 | 상대 날짜 표현 일관성 (내일/모레/이번주/다음주/N일 후) | 5/5 ◯ | 없음 — 날짜 계산 전부 정확 |
| 4 | 개인 참고자료(ChromaDB) 검색 패러프레이즈 강건성 | 3/5 ◯, 2/5 ✗ | "배포는 어디에 했더라?", "그 프로젝트 어떻게 배포했지?" → `search_personal_references` 대신 `search_saved_requests`로 잘못 라우팅되어 빈 결과(`rows: []`) 반환 후 그대로 "정보 없음"으로 답변. 첫 번째 도구가 빈 결과를 반환해도 다른 출처(개인 참고자료)로 재시도하지 않음 |
| 5 | 대화 이력 RAG 세션 경계 (현재 대화 제외 + 과거 대화 회상) | 4/5 ◯, 1/5 ✗ | "요즘 뭐 먹었는지 얘기한 적 있어?" → `search_saved_requests(query="먹다")` 호출, LIKE 매칭 실패(저장된 title은 "먹기") + `search_conversation_messages`로 재시도하지 않아 실제로 존재하는 대화 기록도 못 찾음 (시나리오 1과 동일한 근본 원인 + 라우팅 fallback 부재) |
| 6 | 삭제 후 잔존 데이터 검색 | 5/5 ◯ | 없음 — 삭제된 일정이 구조화 검색/목록/대화 RAG 어디에도 잔존(stale)하지 않음을 확인 |

**총계: 23/30 통과 (약 77%), 7/30 실패**

### 식별된 근본 원인 3가지
1. **LIKE 전체구문 매칭** (`AppSQLiteStore.search_saved_requests`) — 시나리오 1(3건), 5(1건) 실패의 공통 원인. 위 항목 및 `plan.md` 참고.
2. **도구 라우팅 fallback 부재** — 시나리오 4(2건), 5(1건 일부)에서 첫 번째로 선택한 도구가 빈 결과(`rows: []`/`hits: []`)를 반환해도 LLM이 다른 출처 도구로 재시도하지 않고 그대로 "정보 없음"이라고 답함. `WEEK04_MEMORY_PROMPT`에 "한 도구 결과가 비어 있으면 관련 있는 다른 출처 도구로 최소 1회 재시도하라"는 지침이 없음.
3. **`date=null`인 반복성 저장 기록이 날짜 범위 필터에서 누락** — 시나리오 2(1건). `list_saved_requests`/`search_saved_requests`의 `date_from/date_to` 필터가 `date IS NULL`(반복/미정 일정)인 행을 항상 제외함.

### 부가 관찰 (버그는 아니나 설계상 주의 필요)
- "오늘 점심으로 마라탕 먹었어"처럼 순수 잡담성 발화도 `extract_schedule_request`가
  `personal_schedule`로 자동 분류/저장함(시나리오 5 setup). Week 3의 저장 로직이 다소
  공격적으로 모든 발화를 구조화하려는 경향이 있어, 실제로는 일정이 아닌 내용도 일정
  테이블에 쌓일 수 있음.

### 뒷정리
- 이번 검증에서 로컬 DB/Chroma에 생성된 테스트 데이터(일정 6건: 스터디/친구 만나기/세미나/
  회의/치과 예약/점심 마라탕 먹기, todo 1건, reminder 1건, 참고자료 1건 `ref_beedad1bb1`,
  대화 약 30여 건, `테스트삭제일정`은 시나리오 6 내에서 자체적으로 생성 후 삭제됨)는 아직
  정리하지 않음 — 사용자 확인 후 정리 여부 결정 예정.
- 테스트 스크립트(`week04_grayzone_harness.py`)와 원본 결과 JSON은 세션 스크래치
  디렉터리에 보관 중이며 리포에는 커밋되지 않음.
