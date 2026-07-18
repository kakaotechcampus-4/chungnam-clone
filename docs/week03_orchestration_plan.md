# Week 3 — 오케스트레이션 plan (planner / builder / verifier + skills + hooks)

대상 파일: [`student_parts/week03_build_nanas_logbook.py`](../student_parts/week03_build_nanas_logbook.py)
작업 브랜치: `yoojongho/week3`

> 이 문서는 **3주차 과제 구현을 메인 세션 오케스트레이터가 `.claude/` 자산으로 수행하는 계획**을 담는다.
> 기존 [`week02_subagent_setup_plan.md`](week02_subagent_setup_plan.md)(에이전트 구축)·
> [`week02_skills_hooks_setup.md`](week02_skills_hooks_setup.md)(skill/hook 구축)에서 만든 자산을
> 3주차에 **재사용**하고, 부족한 것(verify-week3)만 보강한다.

---

## 1. 배경 (Context)

3주차 목표는 Week 2의 `StructuredRequest`(자연어 → 구조화)를 **Pydantic 입력 스키마로 검증한 뒤
SQLite([`AppSQLiteStore`](../fixed/app_store.py))에 영속 저장**하고, 저장된 요청/일정을 다시
**조회·수정·삭제**하는 것이다. Week 1이 대화 한정 임시 메모리(`PERSONAL_SCHEDULES`)였다면,
Week 3부터 Nana는 앱 재시작·새 대화에서도 유지되는 "기록장"을 갖는다.

이 구현을, 이미 갖춰 둔 **책임 범위별 서브에이전트 + skill + hook**로 위임·검증한다.
메인 세션(오케스트레이터)이 각 단계를 게이팅하며 통합한다.

> **단계 게이팅 규칙**: planner → builder → verifier로 넘어가기 전에 **매번 사용자 명시 승인**을 받는다.

---

## 2. `.claude/` 자산 인벤토리

### 서브에이전트 [`.claude/agents/`](../.claude/agents/)
| 에이전트 | model | tools | 역할 | preload skills |
| --- | --- | --- | --- | --- |
| [`planner`](../.claude/agents/planner.md) | opus | Read/Grep/Glob (read-only) | 요구사항 분석 + `file:line` 근거 실행 계획 | kanana-conventions, prompt-engineering |
| [`builder`](../.claude/agents/builder.md) | inherit | Read/Edit/Write/Grep/Glob/Bash | 계획대로 최소 변경 구현 + `py_compile` 자체검사 | kanana-conventions, prompt-engineering |
| [`verifier`](../.claude/agents/verifier.md) | opus | Read/Grep/Glob/Bash (no Edit) | 요구사항 대조 독립 검증 PASS/FAIL | kanana-conventions, **verify-week2** |

### Skills [`.claude/skills/`](../.claude/skills/)
- [`kanana-conventions`](../.claude/skills/kanana-conventions/SKILL.md) — 과제 공통 규칙(가이드=source of truth, `fixed/` 읽기전용, 임의값 금지, Pydantic 기본값 관례, helper 재사용). 3개 에이전트 공유.
- [`prompt-engineering`](../.claude/skills/prompt-engineering/SKILL.md) — system prompt·tool description 설계 원칙. Week 3의 `SQLITE_MEMORY_PROMPT`/`WEEK03_TOOL_CALL_PROMPT`/`week03_prompt_parts()` 작성에 활용.
- [`verify-week2`](../.claude/skills/verify-week2/SKILL.md) — Week 2 전용 검증 절차. **Week 3용은 없음(→ §4에서 신설).**

### Hooks [`.claude/settings.json`](../.claude/settings.json) + [`.claude/hooks/`](../.claude/hooks/)
- [`protect_paths.py`](../.claude/hooks/protect_paths.py) (PreToolUse: Edit|Write) — 경로에 `fixed/` 세그먼트가 있으면 exit 2로 차단. → Week 3도 `fixed/app_store.py` 오편집 자동 방지.
- [`check_syntax.py`](../.claude/hooks/check_syntax.py) (PostToolUse: Edit|Write) — `student_parts/*.py` 편집 직후 `py_compile`, 실패 시 exit 2. → `week03_*.py` 편집에 자동 적용.
- 두 hook 모두 **경로 규칙 기반**이라 Week 3에 수정 없이 그대로 동작한다.

---

## 3. 그대로 활용할 것 (수정 불필요)

- **planner / builder / verifier** 3개 에이전트 — 역할 분리와 단계 게이팅에 그대로 사용.
- **kanana-conventions, prompt-engineering** skill — 3개 에이전트에 이미 preload.
- **protect_paths, check_syntax** hook + `settings.json` — 경로 기반이라 Week 3 자동 커버. 변경 없음.

---

## 4. 추가로 필요한 것 (gap)

### (A) `verify-week3` skill 신설 — **필수**

verifier의 검증 절차 "정본"은 preload된 주차별 skill이다([`verifier.md`](../.claude/agents/verifier.md) 참조).
현재 `verify-week2`만 있어 Week 3 검증 절차가 없다.

**feasibility 판단: verify-week2 수준으로 작성 가능.** 구현이 0줄이어도 검증 대상 "계약"이 두 소스로 완전히 특정되기 때문:
1. 대상 파일 상단 `[3주차 수강생 구현 가이드]` + `# TODO` — 반환 키(`ok`/`tool_name`, `rows`/`row`, `filters`/`schedules`, `deleted_count`/`filters`/`deleted`), 안전규칙, None 제외 등
2. [`fixed/app_store.py`](../fixed/app_store.py) (읽기전용·완전 구현) — 저장/조회/수정/삭제 메서드 시그니처와 반환 shape

**격리 메커니즘**: `week03` 모듈의 `_store`를 임시 DB `AppSQLiteStore(tmp)`로 monkeypatch
(`__init__`이 `initialize()`로 테이블 생성) + [`fixed.app_store`](../fixed/app_store.py)의 외부 MCP sync 함수 no-op monkeypatch.
→ **LLM 키 없이 저장→조회→삭제 실데이터 왕복**을 시험(verify-week2보다 정적 검증에 유리 — Week 2는 실경로에 LLM 필요).

**2단계 작성(중요)**: verify-week2가 구현 후 튜닝됐듯, verify-week3도
- **Phase A** — spec 기반 뼈대 먼저 작성(지금 실행 시 placeholder라 전 항목 FAIL이 정상)
- **Phase B** — builder 구현 후 verifier가 실행하며 assertion 확정

**over-fit 금지**: 가이드가 이름을 안 못박은 payload 키(예: save 결과 wrapping)는 **가이드 보장 키만** 단언해 valid 구현을 FAIL시키지 않는다.

**위치/형식**: `.claude/skills/verify-week3/SKILL.md`, verify-week2와 동일(`allowed-tools: Bash(uv *)`, `uv run python -X utf8`).

담을 검증 단계(계약 기반):
1. `py_compile` + 모듈 import 스모크
2. **[메인]** `SaveStructuredRequestInput` 필드/기본값/description; `save_structured_request` 저장 dict None 제외; `list_saved_requests`/`get_saved_request`/`personal_list_saved_schedules` 반환 JSON 키
3. **[메인]** `week03_tools()` 목록·이름(Week1 `personal_create_schedule` 교체 여부), `build_week03_agent()` 싱글턴·`response_format` 없음
4. **[추가]** `_delete_saved_schedules` 조건 없는 삭제 거부(안전규칙), `delete_all` vs 필터 분기, `deleted_count/filters/deleted` 키
5. **[추가]** `unwrap_legacy_payload`/`_save_input_from` 정규화 분기, `structured_request_from_week01_schedule` 매핑(attendees→members, id→source_schedule_id)
6. **[추가]** `personal_update_saved_schedule` None=미수정·미존재 `ok=False`·`updated_schedule/shared_sync` 키
7. 실경로(선택, `PROXY_TOKEN` 있을 때): `./run.sh --week3` trace에서 `extract_schedule_request`→`save_structured_request` 순서, 새 대화 영속성

### (B) `verifier.md` frontmatter 갱신 — **필수**
- `skills:` 목록에 `verify-week3` 추가(주차별 하드코딩 방식 유지). [`verifier.md`](../.claude/agents/verifier.md)가 명시적으로 권고하는 방법이며, 동적 `Skill` 호출은 "미검증"으로 표시돼 있어 피한다.
- `verify-week2`는 회귀 참조용으로 남긴다.

---

## 5. 사용 흐름 (Orchestration) — verify-week3는 2단계 작성

0. **[오케스트레이터]** 이 계획을 프로젝트 문서(이 파일)로 발행.
1. **[오케스트레이터] verify-week3 Phase A** — skill 뼈대(spec 기반) 작성 + `verifier.md` frontmatter 등록. `.claude/`는 hook 보호 대상 아님 → 편집 가능. Phase A는 "작성"까지만.
2. **승인 게이트 → planner** — 대상 파일 가이드+TODO를 planner가 **독립적으로** 읽어 `file:line` 근거 구현 계획을 산출한다. 오케스트레이터는 **자신의 분해(§부록)를 planner에 입력으로 넘기지 않고**, planner 결과가 나오면 그것과 교차대조한다. 두 독립 판단이 일치하면 확신을 얻고, 어긋나면 그 지점(오케스트레이터 누락·가이드 모호점)을 builder 착수 전에 해소한다. → 내 분해를 먹이면 두 판단이 하나로 붕괴해 교차검증 가치가 사라진다.
3. **승인 게이트 → builder** — planner 계획대로 메인(먼저)→추가 구현. 편집마다 `check_syntax` hook 자동 검사.
4. **승인 게이트 → verifier** — verify-week3 절차로 독립 PASS/FAIL 보고. **[Phase B]** 뼈대 assertion이 valid 구현과 어긋난 지점만 미세조정해 확정. 진짜 구현 결함은 FAIL로 남긴다.
5. **[오케스트레이터]** 결과 통합, FAIL 시 builder에 수정 위임(2~4 반복), 최종 보고. 확정된 verify-week3는 다음 주차 자산으로 남는다.

builder와 verifier를 **분리된 컨텍스트**로 두어 검증 편향을 줄인다.

---

## 6. 검증 계획 (오케스트레이션 자체 확인)

- verify-week3 skill의 정적 단계(키 불필요)가 `uv run python -X utf8`로 전부 실행되고 PASS(Phase B 이후).
- hook 동작 확인: `fixed/` 편집 시도 차단, `student_parts/week03_*.py` 구문오류 시 즉시 피드백.
- 최종 통합 시 `uv run python -m py_compile student_parts/week03_build_nanas_logbook.py` + 모듈 import 재확인.

---

## 7. 실측 기반 프롬프트 결정 (정적 분석이 아니라 trace로 판정)

프롬프트 라우팅 품질은 정적으로 판정할 수 없어(prompt-engineering §7) `./run.sh --week3` 수동 실측 trace로 두 사안을 갈랐다.

| 사안 | 예측(정적 분석) | 실측 결과 | 결정 |
|---|---|---|---|
| **이중 저장** — 자연어 저장 시 `save_structured_request` + `personal_create_schedule` 동시 호출 | 상속된 week01/02 "새 일정→personal_create_schedule" 지시와 충돌 예상 | `save_path_part` **제거 baseline에서 5/5 재현**, 적용 시 3/3 clean | **`save_path_part` 유지** (`week03_prompt_parts()` 마지막 원소) |
| **상속 라우팅·임시메모리·출력형식 충돌** — 조회/삭제가 Week1 임시 tool로, CHAT_MEMORY 교차대화 차단, StructuredRequestBatch 출력 | planner+오케스트레이터 분석이 다수 충돌 예측 | 실경로 clean — 조회 `personal_list_saved_schedules`, 삭제 `personal_delete_saved_schedules`(교차대화 반영), 출력 자연어 | **통합 override 미채택** (안 깨진 것 안 고침) |

교훈: 상속 프롬프트 충돌은 **텍스트상 실재해도 LLM 행동엔 안 나타날 수 있다.** Week3의 명시적 라우팅 + `join_system_prompt` "뒤 지시 우선" 헤더가 이미 제압했다. 정적 충돌 목록만으로 프롬프트를 늘리지 말고, **trace 실측으로 필요한 것(이중저장)만 반영**한다. 두 결정은 같은 실측 기준으로 대칭적으로 내려졌다.

---

## 8. 다음 주차 표준 실행 순서 (이번 회고 반영)

이번 주차는 골든셋 eval을 맨 마지막에 만들어, 중복 저장 확인을 verifier 1회 + 수동 5회 반복으로 다뤘다.
eval을 앞세웠다면 그 반복은 대부분 통과율과 `--baseline` diff로 자동화됐을 것이다(§7 참조).
다음 주차부터는 아래 순서를 표준으로 삼는다.

**원칙**: 검증 도구 2종을 **구현 전 계약에서 미리 설계**하고, 각 단계는 **승인 게이트**로 넘어간다.
- `verify-weekN` = 결정적 계약(스키마·반환 키·안전규칙) — 1회 검증
- `weekN_eval` = 확률적 행동(tool 라우팅·데이터 파괴류) — N회 통과율, **프롬프트 변경 판정 도구**

**단계**
0. **계획·검증 도구 설계 (구현 전)** — 오케스트레이션 계획 발행, planner 독립 계획 + 오케스트레이터 교차대조, `verify-weekN` 뼈대, **`weekN_eval` 골든셋 뼈대(케이스·판정축·격리 하네스)**. 〔게이트〕
1. **구현** — builder 메인→추가, 편집마다 hook 구문 검사. 〔게이트〕
2. **계약 검증** — verifier + `verify-weekN` PASS/FAIL. 〔게이트〕
3. **행동 baseline 첫 측정** — 구현 직후 `weekN_eval --n N` 실행 + baseline 저장. 데이터 파괴류 결함(중복 저장 등)이 통과율로 즉시 드러남.
4. **프롬프트 튜닝 루프** — 프롬프트를 바꿀 때마다 eval 재실행 + `--baseline` diff로 판정. **수동 반복 실행 금지.** 앱 직접 실행은 **새 실패 모드 탐색 전용**, 발견하면 케이스로 추가.
5. **최종 회귀·baseline 확정** — `verify-weekN` 재실행(회귀), eval 최종 baseline 저장.
6. **커밋·PR** — 논리 단위(구현 / 검증 자산 / docs / eval)로 커밋.

**단계별 도구 역할**

| 단계 | 주체 | 도구 |
|---|---|---|
| 0 | planner + 오케스트레이터 | verify 뼈대, eval 뼈대 |
| 1 | builder | hook(구문 검사) |
| 2 | verifier | verify-weekN (계약, 1회) |
| 3–4 | 오케스트레이터 | weekN_eval (행동, N회 통과율) |
| 4 | 오케스트레이터 | 수동 trace (탐색 전용) |
| 5 | verifier + 오케스트레이터 | verify + eval 재실행 |

핵심: **eval을 마지막 산출물이 아니라 튜닝 도구로 앞세운다.** 프롬프트 변경 판정은 eval diff로, 수동 trace는 탐색용으로만 남긴다.

---

## 부록: Week 3 과제 분해 (오케스트레이터 교차대조용 — planner에 입력으로 넘기지 않음)

> 이 분해는 오케스트레이터가 planner의 **독립** 계획을 사후 대조하기 위한 참조다. planner에는
> 넘기지 않는다(§5 step 2). 메인/추가 구분과 각 함수 TODO의 최종 근거는 대상 파일 상단
> `[3주차 수강생 구현 가이드]`이며, planner가 이를 직접 file:line으로 읽어 계획을 세운다.

### 메인과제 (저장→조회→유지 세로 슬라이스)
- `save_structured_request` — 검증 인자를 저장 dict로(None 제외) → `AppSQLiteStore.save_structured_request()` → `ok/tool_name`+결과 JSON.
- `list_saved_requests` / `get_saved_request` — kind/날짜 필터 목록(`rows`) / `request_id` 단건(없으면 `row=None`).
- `personal_list_saved_schedules` — 기본 kind=`personal_schedule`, 날짜/limit로 `list_schedules()` → `filters`+`schedules`.
- 배선: `SQLITE_MEMORY_PROMPT`, `WEEK03_TOOL_CALL_PROMPT`, `week03_prompt_parts()` 지시, `build_week03_agent()`(create_agent, 싱글턴, response_format 없음).

### 추가과제 (수정/삭제 + Week1 호환 + 레거시 정규화)
- `personal_update_saved_schedule` — None 아닌 필드만 `update_schedule()`, 미존재 `ok=False`, `updated_schedule/shared_sync`.
- `personal_delete_saved_schedules` + `_delete_saved_schedules` — 조건 없으면 거부, `delete_all` vs 필터, `deleted_count/filters/deleted`.
- `delete_saved_schedules_dict` — tool 없이 삭제 로직 호출 helper.
- `personal_create_schedule`(Week1 호환) — 임시 일정 생성 + `structured_request_from_week01_schedule()` 변환 → SQLite 이중 저장, `structured_request`+`sqlite_save`.
- `structured_request_from_week01_schedule` — attendees→members, id→source_schedule_id.
- `unwrap_legacy_payload` / `_save_input_from` / `save_structured_request_payload` — 레거시 wrapper·dict·JSON·자연어를 저장 스키마로 정규화(자연어는 `extract_structured_request` 재사용).

### 이미 구현되어 손대지 않음
`_store`, `_tool_name`, `json_payload`, `tool_result`, 5개 Input 스키마, `week03_tools`, `week03_system_prompt`, `build_week_agent`.
**읽기 전용 재사용**: [`fixed/app_store.py`](../fixed/app_store.py), Week 1·2 helper.

---

## 이후 단계 (Next)

이 문서는 **오케스트레이션 인프라 + 실행 흐름**을 규정한다. 실제 3주차 구현은
**verify-week3 Phase A → planner → builder → verifier(Phase B)** 흐름으로 이어서 진행한다.
