# Week 5 — 카나가 지난 대화를 불러오다 (MCP: 외부 SQLite를 tool 서버로)

> Claude Code **Plan 모드**로 구현 전에 작성·승인한 작업 계획서.

## Context

5주차는 **데이터 접근을 agent 프로세스 밖으로 내보내는 주**다. 4주차까지 나나는 앱이 직접 들고 있는 저장소(SQLite·ChromaDB)를 읽었다. 이번 주 카나는 **다른 사람들의 지난 대화와 공유 일정**을 봐야 하는데, 그건 내 앱의 데이터가 아니다. 그래서 별도 프로세스(MCP 서버)가 그 DB를 소유하고, agent는 **MCP tool 호출로만** 접근한다.

과제 파일 가이드 원문: *"학생이 직접 SQL을 작성하는 주차가 아니라, MCP tool을 호출하고 그 결과를 agent용 JSON으로 전달하는 wrapper tool을 만드는 주차입니다."*

- 수정 파일 하나: `student_parts/week05_load_kanas_past_conversations.py` (415줄, **TODO 10곳** = 함수 9개 + 프롬프트 1) + 새 테스트 파일 2개
- **MCP는 진짜다**: `fixed/mcp_client.py`가 `mcp_server/sqlite_mcp_server.py`를 **stdio 로컬 서브프로세스**로 띄움(`MultiServerMCPClient`). **네트워크·토큰·포트 불필요** → 9개 함수 중 8개가 오프라인 검증 가능
- 서버·store는 전부 완성돼 있음(`mcp_server/sqlite_mcp_server.py`, `fixed/external_people_store.py`) — 학생은 **wrapper 계층 + 병합 로직 + 프롬프트**만
- 진행 구조: Phase1 baseline 비교 → Phase2 큰 그림 → Phase3 함수 단위. **함수 하나 = 설명 하나 = 커밋 하나**, 검토 후 커밋
- 6주차 연결: `list_shared_schedules`·`collect_member_schedules`의 rows가 6주차 `find_common_available_slots`의 busy_rows가 됨 → 그래서 이 둘이 추가과제가 아니라 **메인**
- 현재: `songyujin/week5` 브랜치, 4주차 2차 피드백 반영 커밋(`e8577be`) push 완료

---

## Phase 1 — baseline 비교 (탐색 완료, 발표만)

- **week05 정답은 없다.** `student_parts_baseline/`엔 week01~04만 있음. 새로 온 건 **week04 정답**
- 내 week04 vs baseline 함수 단위 비교: 반환 계약은 week05·앱이 만지는 모든 지점에서 동일. 차이는 **전부 내 쪽 개선**(`REFERENCE_MIN_CANDIDATES`, `retrieval.sufficient` 리포트, 라우팅 규칙 ①~⑧) — baseline엔 하나도 없음
- **week5 호환성 리스크 0**: week05는 내 코드에서 `PERSONAL_SCHEDULES`·`join_system_prompt`·`StructuredRequest`·`week04_tools`·`week04_prompt_parts` 5개만 쓰고 전부 계약 일치. week04 tool의 **반환 JSON을 파이썬으로 읽는 코드는 레포에 없음**(전부 LLM에게 문자열로 감) → 내가 추가한 `retrieval` 키는 무해
- ⚠️ **baseline을 안 쓴 게 결과적으로 옳았던 지점**: baseline week04 프롬프트엔 *"Week 4에서는 외부 멤버 이전 대화나 그룹 일정 최종 조율을 처리하지 않는다."*가 있다. `week05_prompt_parts()`가 `week04_prompt_parts()`를 그대로 펼치므로, baseline을 채택했다면 **5주차 agent가 외부 멤버 대화 조회를 거부**했을 것. 내 프롬프트엔 그 문구가 없음
- baseline이 앞선 유일한 항목: `search_nana_memory` 구현(94줄). 단 `week04_tools()`에 미등록·호출처 0 → 그대로 `...` 유지
- 💡 **가져올 것 하나**: baseline에 있던 *"assistant 발화만으로 사실을 확정하지 않는다"* 주의가 내 실제 프롬프트엔 없음(가이드 주석에만). **5주차는 지난 대화를 근거로 답하는 주**라 이번에 프롬프트 규칙으로 넣는다

## Phase 2 — 큰 그림 (개념 수업, 코드 0줄)

비유: **카나 = 다른 회사 자료를 열람하는 직원**. 4주차 나나는 자기 사무실 서류함을 직접 열었다. 이번엔 자료가 **다른 건물(외부 프로세스)**에 있어 내가 직접 들어갈 수 없다. 창구 직원(MCP 서버)에게 **정해진 양식으로** 요청하면 그가 대신 꺼내 준다.

흐름 4단계: ① 사용자 질문 → ② LLM이 MCP wrapper tool 선택 → ③ wrapper가 `call_mcp_tool_sync(이름, args)` → **서브프로세스가 떠서** 외부 SQLite를 읽고 JSON 문자열 반환 → ④ agent가 그 rows를 근거로 답변.

핵심 개념:
- **MCP = 권한·소유의 경계**: 강의 노트북 원문 *"MCP는 DB 접근 권한을 agent 내부 코드에서 분리한다."* 4주차엔 `SQLITE_STORE`가 **내 모듈 전역**이라 아무 코드나 만질 수 있었다. 5주차엔 서버만 만질 수 있다
- **transport = stdio**: HTTP가 아니라 표준입출력. `command=sys.executable, args=[서버파일]`로 자식 프로세스를 띄우고 파이프로 JSON-RPC를 주고받음
- **왜 프로세스를 나누나**: 다른 언어·다른 사람이 쓴 서버도 꽂을 수 있음(USB 허브 그림). 백엔드를 바꿔도 **tool 이름·인자·payload 계약이 같으면** agent는 그대로
- **계약이 주인공**: 서버의 6개 tool은 전부 JSON **문자열**을 반환하고 키가 정해져 있음(`{ok, tool_name, rows, …}`). wrapper가 그 모양을 보존해야 함
- **경계에서 한 번만 정규화**: 멤버 이름·날짜 정리는 서버/store가 이미 함 → wrapper에서 또 하면 이중 변환 버그
- **강의 자료와 우리 과제의 차이(중요)**: 강의 노트북엔 MCP가 **없다**. `@tool` 3개로 같은 이름·payload만 흉내 내고, 노트북 스스로 *"실제 MCP server/client 구현 문제는 별도 문제 repo에서 작성"*이라 적어 뒀다. 그 "별도 repo"가 **우리 과제 레포** — 우리는 진짜 MCP를 붙인다

미니 실습(1줄): `load_langchain_mcp_tools_sync()`로 서버가 노출하는 tool 6개를 직접 찍어 보기 → *tool 목록이 내 소스가 아니라 서버에서 온다*를 눈으로 확인.

## Phase 3 — 함수 단위 구현

각 단계마다 ⑴기능 ⑵주요 변수 ⑶설계의도(대안·수정/삭제 시 문제). 얇은 wrapper로 MCP 호출 모양을 익히고 로직 함수로 넘어간다.

### 1단계 — MCP wrapper 6개 (각각 커밋)

| # | 함수 | 이번 단계의 새 개념 |
|---|---|---|
| W1 | `search_previous_conversations` (TODO L297) | **첫 MCP 호출.** args dict 조립 → `call_mcp_tool_sync` → **문자열 그대로 반환**(서버가 이미 JSON) |
| W2 | `load_conversation_messages` (L305) | **대조 학습**: `call_external_tool_payload`는 `json.loads`까지 해서 **dict**를 준다 → `json_payload()`로 다시 감싼다. 두 경로가 왜 다 있는지 |
| W3 | `extract_schedules_from_history` (L313) | 정규화 소유권 — 날짜 정리는 **경계에서 한 번만**. 서버가 `schedule_summary`도 이미 줌 → 재계산 금지 |
| W4 | `list_shared_schedules` (L355) | **공유 저장소 ≠ 대화 기록.** 필터 없이 부르면 서버가 기본 실습 데이터로 대체하는 분기 |
| W5* | `create_shared_schedule` (L330) | 추가과제. W1과 같은 모양 → 싸다. `schedule_id`/`source_conversation_id`를 남겨야 나중에 동기화 가능(UPSERT) |
| W6* | `delete_shared_schedule` (L341) | 추가과제. 삭제 조건이 `schedule_id` **OR** `source_conversation_id`(AND 아님) |

\* 추가과제지만 **여기서 함께 구현한다**: 이 둘이 이미 `week05_tools()`에 등록돼 있어(L375-376) `...`로 남기면 LLM이 호출했을 때 `None`이 반환된다. 안 할 거면 목록에서 빼야 하는데, 6주차 보정용으로 쓸모가 있어 구현하는 쪽을 택함.

**wrapper 6개에 공통으로 걸리는 함정 2개**
- 🚨 **`member_names=None` ≠ `member_names=[]`**: store는 `None`을 "필터 없음(전체 멤버)", `[]`를 "빈 결과 반환"으로 **다르게** 취급한다(`fixed/external_people_store.py` 검색/추출/조회 3곳). wrapper에서 `member_names or []` 같은 기본값을 주면 **멤버를 언급하지 않은 질문마다 결과가 조용히 0건**이 된다. 받은 값을 그대로 넘긴다
- 🚨 **이중 인코딩 금지**: W1·W3~W6은 서버가 이미 JSON 문자열을 주므로 `json_payload()`로 또 감싸면 안 된다(이스케이프된 문자열 덩어리가 됨). `json_payload()`가 필요한 건 dict를 받는 **W2 하나뿐**

→ **T1. wrapper 테스트 묶음** (별도 `test:` 커밋): 6개를 parametrize로 한 번에 — 정상 / 경계(`limit`의 `ge`·`le`) / 빈값(`member_names=[]`→`[]`, 없는 `conversation_id`→`rows: []`) / 필터없음 분기 / 왕복(create→list→delete)

### 2단계 — 로직 함수 3개 (각각 구현 커밋 + 테스트 커밋)

| # | 함수 | 핵심 |
|---|---|---|
| L1 | `_personal_schedules_for_current_scope` (L189/TODO 192) | 앱 SQLite `list_schedules` + 현재 대화의 임시 `PERSONAL_SCHEDULES`(week01) 병합. **함정 3개**: ⓐ `list_schedules` 기본 `limit=12` ⓑ week01 row는 키가 `id`, DB row는 `schedule_id` ⓒ 이미 저장된 임시 일정이 **중복 집계**되지 않게 dedup. 스코프는 이미 주어진 `_schedule_scope()`(L185)로 필터 |
| L2 | `_collect_member_schedules` (L276/TODO 285) | 내 rows + 외부 MCP rows를 **같은 키 모양**(`member_name/title/date/start_time/end_time/notes`)으로 통일 + `external_schedule_summary()`. 개인 일정이 **인자로 주입**되는 구조가 곧 테스트 이음새 |
| L3 | `collect_member_schedules` (tool, L363) | L1·L2를 **연결**. 이 둘은 호출처가 0이라, 구현만 하고 연결을 잊으면 조용히 빈 결과가 난다 |

**L2에서만 성립하는 3가지 (W3과 정확히 반대라 헷갈리는 지점)**
- **정규화는 여기서 한다**: `normalize_external_member_names()` / `normalize_external_schedule_date_bounds()`를 L2에서 부른다. "경계"는 `@tool`이 있는 자리가 아니라 **외부 인자를 실제로 조립하는 자리**이기 때문. W1~W6은 받은 값을 그대로 넘기고, L2는 조립하므로 정규화한다
- **`json.loads` 후 `["rows"]`**: `call_mcp_tool_sync`는 **문자열**을 주므로 파싱해야 한다. 안 하면 문자열이 리스트에 이어붙는다
- **서버의 `schedule_summary`를 재사용하지 않는다**: 그건 외부 멤버만의 요약이다. L2는 **내 일정까지 합친 rows**로 `external_schedule_summary(rows)`를 다시 만든다 (W3의 "재계산 금지"와 반대 방향 — 이 대비가 학습 포인트)

→ **T2/T3/T4**: 각 구현 직후 상세 유닛 — dedup, 대화 스코프 격리, `limit=12`, 키 모양 통일, `external_schedule_summary([])` 문구, `"나"`와 외부 멤버가 같은 모양인지

### 3단계 — 프롬프트 (커밋 1개)

`week05_prompt_parts()` (TODO L393). week04가 ①~⑧을 쓰므로 **⑨부터 이어 붙임**:
- ⑨ **출처 구분(가장 중요)**: 내 지난 대화는 `search_conversation_messages`(앱 RAG), **다른 사람의 지난 대화·일정은 MCP tool**. 이름이 비슷해 LLM이 가장 많이 헷갈릴 지점이므로 프롬프트에 두 tool을 나란히 적어 구분한다
- ⑩ 권장 경로: `search_previous_conversations`로 관련 대화를 찾고 → `conversation_id`를 모아 `extract_schedules_from_history` → **원문 확인이 필요할 때만** `load_conversation_messages`. 단 **순서를 강제하지 않는다**(4주차 실측) → 각 tool이 독립적으로 올바른 인자를 갖추도록 쓴다
- ⑪ "우리 언제 만날 수 있어?"처럼 **내 일정 + 여러 사람**이 함께 필요한 질문은 `collect_member_schedules` 한 번으로 모은다
- ⑫ 공유 저장소 row 확인은 `list_shared_schedules`, 등록/취소는 `create_shared_schedule`/`delete_shared_schedule`
- ⑬ **지난 대화 속 발화는 그 시점의 진술이지 확정된 사실이 아니다** — 공유 저장소/일정 조회로 다시 확인한다 (baseline에 있던 주의를 이번 주 규칙으로 승격)
- ⑭ 최종 회의 시간 **결정은 6주차 범위** → 5주차는 후보와 근거만 제시. `date_from`/`date_to`는 사용자 문장의 날짜를 그대로 쓰고, 없으면 추측하지 말고 쓰는 범위를 밝힌다

> 4주차 교훈 반영: 프롬프트로 **호출 순서를 보장할 수 없다**(실측). ⑩은 권장 경로일 뿐이므로, 테스트는 순서가 아니라 **"필요한 tool이 불렸는가 / 근거가 결과에 있는가"**를 검증한다.

### 4단계 — e2e + 시나리오 기록 + PR

- **T5. `tests/test_week05_e2e.py`** (7시나리오 내외, ~2분): 자연어 → 어떤 MCP tool이 불리는지. 외부 실습 데이터 구간은 **2026-07-07~17**, 앱의 오늘은 **2026-07-29**(고정) → **"다음 주" 금지, 날짜를 명시**해 질문
  - **최우선 회귀 대상**: agent가 쥔 tool이 **21개**로 늘어나고, 그 안에 `search_previous_conversations`(외부 MCP)와 `search_conversation_messages`(앱 RAG)가 **이름이 비슷하게** 공존한다 → 이 혼동이 e2e가 잡아야 할 1순위
  - **순서 대신 검증할 수 있는 것**: `load_conversation_messages`가 불렸다면 그 `conversation_id` 인자가 **실재하는 id인지** 확인 → "먼저 검색했는가"를 순서 없이 증명하고, 지어낸 id도 잡힌다
  - 근거는 **답변 문장이 아니라 tool payload**로 검증(4주차 규칙). 답변 문장 검증은 테스트당 최대 1개, 사실 단어만
  - `PROXY_TOKEN`이 없으면 **파일 단위 skipif** → 토큰 없을 때 "조용한 초록" 대신 `skipped`로 드러나게
  - 정리: 앱 DB는 생성된 id로 삭제(4주차 헬퍼 재사용), **외부 공유 DB는 임시 경로로 우회하고 파일째 버린다** — 앱 row를 raw SQL로 지우면 `delete_*_from_shared` 훅을 건너뛰어 `app:req_*`·`group:req_*:멤버` 고아 row가 영구히 남기 때문
- `docs/week05_scenario_results.md`: 경계 질문 + trace 기록 (4주차 관례). **수동 시나리오에서 실제로 실패한 입력만 회귀 테스트로 고정**(4주차 멘토님 피드백)
- PR base는 **`songyujin/final`**

---

## 검증 수단 3가지의 역할 분담 (결정 사항)

4주차엔 시나리오 문서가 측정 로그·비교표까지 담아 **반쯤 테스트 역할**을 했고, 그래서 "시나리오에서 실패한 걸 회귀로 고정하라"는 피드백이 나왔다. 5주차는 경계를 미리 나눈다.

| 수단 | 담당 | 담지 않는 것 |
|---|---|---|
| **유닛** (`tests/test_week05_mcp.py`) | 계약·경계·병합 로직을 매번 동일하게 확인 | 답변이 사람에게 쓸모 있는지 |
| **e2e** (`tests/test_week05_e2e.py`) | "이 문장 → 이 tool 호출"의 반복 확인, 회귀 고정 | 내가 미리 생각하지 못한 실패 |
| **시나리오 문서** (`docs/week05_scenario_results.md`) | **내가 수동으로 검증한 내용만 기록** — 새로운 실패의 발견, 답변 톤·근거 인용 방식, 실행 편차(2/2·1/3 같은 실측) | 자동 재확인(시간이 지나면 stale) |

**연결 규칙(중요)**: 수동 검증에서 **실패를 발견하면 문서에만 남기지 않고 e2e 회귀 테스트로 옮긴다.** 문서는 *무엇을 발견했나*, 테스트는 *다시 깨지지 않게*. 4주차에 이 연결을 하면서 실제로 #5가 여전히 깨져 있던 것을 발견했다.

그래서 문서의 각 항목에 **후속 상태 한 줄**을 남긴다:
```
## 시나리오 N: "<입력 문장>"
- 실제 trace: tool A → tool B
- 판정: ✅ / ⚠️ / ❌
- 관찰: (사람만 볼 수 있는 것)
- 후속: 프롬프트 규칙 ⑨ 추가 / e2e 회귀로 고정함 ✅
```
프롬프트로 보장할 수 없는 항목은 "고정 안 함"으로 남겨도 된다 — **한계를 우리가 단정하지 않고 관측만 기록해 멘토님 판단을 받는다**(기존 원칙).

## 테스트 전략 (결정 사항)

- **시점 = 하이브리드**: 얇은 wrapper 6개는 구현을 먼저 끝내고 **묶어서 parametrize**(거의 같은 테스트를 6번 쓰는 낭비 방지). 로직 3개는 **구현 직후 즉시** 상세 유닛(버그가 실제로 숨는 곳)
- **커밋 = 분리**: `feat:` 커밋과 `test:` 커밋을 따로 → 멘토님이 구현/검증을 나눠 읽을 수 있고 "함수 하나 = 커밋 하나"도 유지
- **MCP = 100% 실호출** (가짜 주입 없음). 로컬 stdio라 네트워크 불필요. 호출 1회 ≈ 0.6~1.2초(서브프로세스 2개 생성) → 40개면 **30~50초** 예상(4주차 유닛 28초와 비슷한 수준)
- ⚠️ **실호출만 쓸 때 생기는 사각지대와 대응**: 실제 MCP 호출은 "서버가 인자를 받아들였다"만 증명하고 **"내가 의도한 인자를 넘겼다"는 증명하지 못한다.** 예를 들어 `member_names=["철수"]`를 넘겼어야 하는데 실수로 `None`을 넘기면 철수 row가 포함된 **상위집합**이 돌아와서 테스트가 그대로 통과한다. 가짜 주입(spy)을 안 쓰기로 했으니 대신 **음성 대조로 인자를 묶는다**:
  - `tool 이름 6개 집합 검증` — `load_langchain_mcp_tools_sync()`로 서버가 실제 노출하는 이름 집합을 한 번에 비교 (서브프로세스 1개, ~1초). 오타 난 tool 이름을 전부 잡는 가장 값싼 테스트
  - `필터 결과 ⊂ 무필터 결과` **이면서 엄격히 더 작을 것** → 인자 이름(`member_names`/`date_from`)이 실제로 먹혔음을 증명
  - `member_names=[]` → `rows == []`, 없는 `conversation_id` → `rows == []`, `limit=1` → 정확히 1건
  - `list_shared_schedules` 무필터 분기는 **음성 대조 2개**로 검증: 구간 밖 날짜 row와 `"나"` row를 심어 두고, 무필터 호출 결과에 **둘 다 없어야** 한다(기본 날짜창 + 기본 멤버목록이 모두 적용됐다는 증거)
- **격리 = 임시 DB 우회**: `KANANA_EXTERNAL_DB_PATH`를 tmp 경로로 바꾸는 fixture. `fixed/mcp_client.py`가 **호출 시점에 `os.environ`을 읽어 자식 프로세스에 넘기므로**, 학생 코드를 한 줄도 안 바꾸고 모든 wrapper가 임시 DB를 보게 된다. 새 DB에도 `seed()`가 실습 데이터(철수·영희·민준·서연·지훈·하린, 일정 18건)를 자동 생성하므로 **재현성이 오히려 더 좋고**, 실제 공유 DB를 오염시키지 않음
- **정리는 id로** (4주차 교훈): 시각 범위·텍스트 마커 금지. 앱 DB에 생기는 row는 생성된 `schedule_id`/`request_id`로만 삭제
- **seed 함정**: 서버가 뜰 때마다 `seed()`가 `ext_cs/ext_yh/…` id를 **지우고 다시 넣는다** → 테스트가 심는 데이터는 **자기 id**(예: `w5test_*`)를 써야 중간에 날아가지 않음. 반대로 자기 id row는 영구 보존되므로 **직접 지워야** 함
- 파일: `tests/test_week05_mcp.py`(유닛) / `tests/test_week05_e2e.py`(라우팅) — 4주차와 같은 2층 구조. `conftest.py`가 없으므로 `sys.path` 프리앰블 반복
- 규모 목표: 유닛 **60~80종**(Pydantic 스키마 경계 ~30 + wrapper 계약·대조 ~20 + 로직 3함수 ~25) / e2e **7종 내외**. 스키마 테스트는 강사가 준 제약을 지키는 **회귀 방지용**이지 내 로직 커버리지가 아니라는 점을 주석에 명시
- 테스트가 심은 `w5test_*` row가 **두 번째 MCP 호출 뒤에도 남아 있는지** 확인하는 테스트 1개를 넣는다 → 재seed가 내 데이터를 지우지 않음을 증명(4주차의 "정리가 조용히 실패" 재발 방지)
- 실행: `uv run --with pytest pytest tests/test_week05_mcp.py -v` (dev 의존성 추가 안 함 → `uv.lock` 깨끗하게 유지, 주차별 강의자료 머지 충돌 회피)

## 검증 방법

1. **유닛** — `uv run --with pytest pytest tests/test_week05_mcp.py -v` (실제 MCP 서브프로세스, 임시 DB, 네트워크 불필요)
2. **e2e 라우팅** — `uv run --with pytest pytest tests/test_week05_e2e.py -v` (실제 LLM 프록시 필요)
3. **앱 통합** — `./run.sh --week5` → Details 탭 trace. 가이드가 지정한 확인 항목: `collect_member_schedules` rows에 `"나"`와 외부 멤버가 **같은 모양**으로 들어오는지, `list_shared_schedules`가 `rows`+`schedule_summary`를 유지하는지, 추가과제 create→list→delete 왕복
4. **회귀** — 4주차 테스트(유닛 160 + e2e 17)가 여전히 통과하는지 (week05가 week04 tool·프롬프트를 상속하므로)

## 주의

- `mcp_server/`·`fixed/`·`app.py`·week01~04 파일 **수정 금지**(import만). baseline 복사 금지
- `call_mcp_tool`·`load_langchain_mcp_tools(_sync)` 3개 별칭은 **호출처 0** — 예약 별칭이므로 그대로 둔다
- `_structured_request_from_schedule_row`(L262)는 **본문이 이미 주어졌고 호출처 0 → 그대로 둔다.** 반환 모양이 week2 `StructuredRequest`(`members`/`original_text`)인데 우리가 만들 병합 row는 `member_name`/`notes`라 **키가 안 맞는다.** 억지로 끼우면 바로 되돌려 꺼내야 해서 군더더기만 생긴다
- **테스트 데이터 작성 시 3가지**: ⓐ `strip_external_row_parentheticals`가 사용자 노출 필드의 **괄호를 지운다** → 심는 텍스트에 괄호 금지 ⓑ seed된 `notes`는 `""`(빈 문자열) → 진위(truthy)로 판단하지 말고 **키 존재**로 검증 ⓒ `load_conversation_messages`는 `created_at ASC` 정렬이므로 심는 시각을 **서로 다르게 증가**시켜야 순서 검증이 흔들리지 않는다
- `search_previous_conversations` row에는 **`sender` 키가 없다**(`conversation_id/member_name/title/content/created_at`). `sender`는 `load_conversation_messages`에만 있으므로 교차 검증하면 안 된다
- `AppConfig`는 **frozen dataclass** → `CONFIG.app_db_path`를 직접 바꿀 수 없다. 앱 DB를 격리하려면 모듈의 `CONFIG` 자체를 대체한다
- `_WEEK05_AGENT`는 **첫 build 시점의 프롬프트를 굳혀서 캐시**한다 → agent를 만든 뒤 프롬프트를 바꿔도 반영되지 않는다(테스트 순서 의존 버그의 원인)
- MCP stdio에는 **타임아웃이 없다** — 서버가 멈추면 무한 대기. 테스트가 걸리면 서브프로세스부터 의심
- `week05_tools()`는 `week04_tools()`⊃`week03_tools()`를 포함 → **저장 tool이 불리면 앱 DB 저장 + 외부 공유 DB 동기화(`sync_*_to_shared`)가 함께 일어난다**(참석자 1명당 서브프로세스 2개). e2e가 일정을 저장하면 **두 DB 모두** id로 청소
- 새 클론에서 `data/chroma`가 비어 있으면 week05 import만으로도 임베딩 프록시를 호출함(week04 store seed) — 현재 환경은 이미 채워져 있어 오프라인 안전
