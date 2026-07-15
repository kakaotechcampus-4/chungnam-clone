# Week 3 — 나나의 기록장을 만들다 (SQLite 영속 저장)

> Claude Code **Plan 모드**로 구현 전에 작성·승인한 작업 계획서 원본입니다.
> 코드베이스 탐색(과제 파일 TODO 분석, fixed 배선 추적, baseline 비교, 강의 노트북 레퍼런스)을 마친 뒤 이 계획대로 진행했습니다.

## Context

3주차: 2주차 구조화 결과(`StructuredRequest`)를 **SQLite에 저장**하고 조회/수정/삭제한다. 나나가 1주차 임시 메모리 대신 **앱을 꺼도 남는 "기록장"**을 갖게 되는 주.
- 수정 파일 딱 하나: `student_parts/week03_build_nanas_logbook.py` (480줄, **TODO 18곳**)
- **SQL은 전부 주어짐**(`fixed/app_store.py`의 `AppSQLiteStore`, 767줄) — 학생 몫은 그 위의 얇은 tool 계층 + 프롬프트
- 검증 흐름: `extract_schedule_request`(2주차 심화 bridge!) → `save_structured_request` → 사이드바 "저장된 일정"에 표시 + **새 대화/재시작에도 유지**
- 진행 구조: **Phase 1 베이스라인 비교·평가 → Phase 2 큰 그림(비유 포함) → Phase 3 단위별 구현(기능·변수·설계의도 3종 설명)** + **Q&A는 파일에 상세 한글 주석으로 반영**
- 작업 규칙: 코드 수정 후 멈춰 설명 → 직접 확인 후 커밋 결정. baseline 코드 복사 금지(참고만).
- 현재: `songyujin/week3` 브랜치, 강의자료 동기화 완료, week2 PR 머지됨.

---

## Phase 1 — 베이스라인 비교·평가 (탐색 완료, 발표만 남음)

승인 후 첫 메시지로 비교 리포트 전달. 핵심 결론(이미 확정):
- **구조 동등**: 스키마 필드/타입/기본값, 모든 tool의 반환 JSON 키, bridge 계약(`ok/tool_name/base_date/structured_request`) 전부 일치 → 내 구현이 정답과 같은 계약을 지킴
- **차이 4가지**:
  1. baseline week01에 `"owner": "me"` 필드 추가 (레포 어디서도 안 읽는 **불활성 값** — DB가 자체 기본값 'me'를 가짐 → 리스크 0)
  2. baseline은 `response_format=StructuredRequestBatch`(plain), 내 쪽은 `ToolStrategy(...)` — **프록시 환경에서 더 견고** (직접 디버깅으로 검증한 개선)
  3. 내 경계 규칙 ①~⑧은 baseline에 없음 — **순수 상위호환**, 그리고 `week03_prompt_parts → *week02_prompt_parts` 상속으로 **3주차 저장 품질에 직접 기여** (⑤미정→null, ⑥조회를 저장 안 함, ⑧schedule/todo 분기가 DB 테이블 분기를 결정)
  4. baseline은 week01 `CHAT_MEMORY_PROMPT`를 채움(카나 지시), 내 쪽은 나나 지시를 week01_prompt_parts에 직접 — 기능적 동등
- **3주차 호환성 리스크: 구조적 0.** 유일한 전파는 프롬프트 내용(=장점으로 작용)
- 평가: 코드 유지 (baseline로 바꿀 이유 없음). `owner` 필드는 "왜 있나" 학습 포인트로만 설명

## Phase 2 — 큰 그림 (개념 수업, 코드 0줄)

일상 비유: **"수첩(1주차 인메모리) → 장부+분류 서랍장(SQLite)"**. 흐름 4단계:
1. **주문 접수**: 사용자 문장 → `extract_schedule_request`가 검증된 구조화 JSON으로 (2주차에 만든 것)
2. **영수증 보관**: `save_structured_request` tool → `structured_requests` 테이블에 **원본 그대로**(raw_json) + 영수증 번호(`request_id`) 발급
3. **서랍 분류**: 같은 저장 안에서 `kind`별 분기 — schedules(개인/그룹, `schedule_type`으로 구분)/todos/reminders. unknown은 영수증만 (안전 분류의 저장판)
4. **꺼내 보기**: 조회 tool + 사이드바(app.py가 `list_schedules` 직접 호출) — **대화 무관·재시작 무관** (1주차 session_id 격리와 의도된 대비)

개념 교육(강의 노트북 기반): DB가 왜 필요한가(휘발 vs 영속), 테이블/행/컬럼, PRIMARY KEY(영수증 번호)/FOREIGN KEY(연결), `?` 바인딩(SQL 인젝션), commit, 정규화(원본 보관 vs 검색용 분해), `owner` 컬럼의 존재 이유(6주차 멤버 확장 대비). 미니 실습: 실제 `data/kanana_app.sqlite3`를 열어 테이블 구조 구경(읽기 전용).

핵심 지도: `SaveStructuredRequestInput`은 **`StructuredRequest`를 상속**(2주차 스키마가 문자 그대로 저장 스키마의 부모가 됨) + `@tool(args_schema=...)` 패턴(2주차 심화의 `with_structured_output`과 형제 개념).

## Phase 3 — 단위별 구현 (각 단계 = 설명→검토→커밋 결정)

각 코드 제시마다 3종 설명 필수: ⑴ 함수가 하는 일 ⑵ 주요 변수가 왜 필요한지 ⑶ **설계 의도**(왜 이 방식인지·대안 비교·수정/삭제 시 생기는 문제).

**메인과제** (TODO: 저장 tool, 조회 tool 2개, 일정 조회 tool, 프롬프트 2상수+2인라인, agent builder):
- M1. `save_structured_request` @tool (344행) — 저장의 심장. 검증된 인자→None 제외 dict→`_store().save_structured_request()`→`tool_result` JSON. args_schema 패턴 설명
- M2. `list_saved_requests` + `get_saved_request` (357·365행) — 조회 2종. 빈 결과도 rows=[]/row=None (예외 금지 — 왜?)
- M3. `personal_list_saved_schedules` (378행) — 기본 kind=personal_schedule, filters+schedules 반환
- M4. 프롬프트 4곳: `SQLITE_MEMORY_PROMPT`(30행)·`WEEK03_TOOL_CALL_PROMPT`(33행)·`week03_prompt_parts` 인라인 2곳(458·461행) — tool 호출 순서(구조화→저장→조회) 지시
- M5. `build_week03_agent` (472행) — 1·2주차와 비교(이번엔 response_format 없음 — 왜 없는지 설명)
- M6. **메인 검증**: `./run.sh --week3` → "내일 10시 개인 코칭 저장해줘" → trace에서 extract→save 연쇄 확인 → "내 일정 보여줘" → **새 대화/재시작 후에도 유지** + 사이드바 표시

**추가과제** (메인 검증 통과 후):
- A1. `unwrap_legacy_payload`(223행) + `_save_input_from`(230행) + `save_structured_request_payload`(241행) — 레거시/다형 입력 정규화 3층
- A2. `_delete_saved_schedules`(302행) + `delete_saved_schedules_dict`(394행) + `personal_delete_saved_schedules`(425행) — 조건 없는 삭제 거부(안전장치 설계)
- A3. `personal_update_saved_schedule`(409행) — None=변경 안 함 패턴, shared_sync
- A4. `structured_request_from_week01_schedule`(310행) + 호환 `personal_create_schedule`(324행) — attendees→members/id→source_schedule_id 매핑, 이중 기록(인메모리+SQLite), 멱등성 가드
- A5. 추가 검증 (수정→삭제→목록 확인)
- PR: 직접 제출 (base=`songyujin/final`)

**Ongoing Rule**: 다시 묻거나 자세한 설명을 요청한 내용은 해당 코드에 **초보자용 상세 한글 주석**으로 반영해 다시 출력 (2주차 model_dump 주석 선례).

## 검증 방법
- 오프라인: tool `.invoke()` 직접 호출 + `sqlite3` CLI/파이썬으로 실제 row 확인 (`data/kanana_app.sqlite3`)
- end-to-end: `./run.sh --week3` 골든 시나리오 (저장→조회→재시작 유지→수정→삭제)
- 연결 불안정 시 1·2주차처럼 오프라인 대체

## 주의
- `student_parts_baseline/`은 참고만 — 코드 복사 금지 (과정 취지)
- week01/02 파일은 수정하지 않음 (import만 됨)
- DB 파일은 gitignore된 `data/`에 있음 — 커밋 안 됨
- 저장 tool은 원본 NL 문자열이나 ok/tool_name/base_date 래퍼를 저장하면 안 됨 (가이드 명시)
