# Week 5 — 외부 MCP 라우팅 plan (실측 기반 의사결정 기록)

> week04 문서와 같은 무게중심이다. 오케스트레이션/skill/hook 인프라는 재사용 단계라 다시 적지 않고
> ([`week03_orchestration_plan.md`](week03_orchestration_plan.md)·[`week-kickoff`](../.claude/skills/week-kickoff/)에 위임),
> **코드에는 결과만 남고 "왜 그렇게 정했는지"는 안 남는 부분**만 담는다.
> Week 5는 특히 **판정축 설계**와 **재사용 원칙**에서 배운 것이 많아 그쪽에 지면을 더 썼다.

관련 자산: 대상 [`student_parts/week05_load_kanas_past_conversations.py`](../student_parts/week05_load_kanas_past_conversations.py) ·
검증 [`.claude/skills/verify-week5/`](../.claude/skills/verify-week5/) ·
평가 [`evals/week05_eval.py`](../evals/week05_eval.py) · [`evals/week05_baseline.json`](../evals/week05_baseline.json)

---

## 1. 배경 (Context)

Week 5는 학생이 SQL을 쓰는 주차가 아니라, 외부 SQLite/MCP 서버의 결과 JSON을 agent용으로 전달하는
**thin wrapper 주차**다. 메인 7개(`search_previous_conversations`/`load_conversation_messages`/
`extract_schedules_from_history`/`list_shared_schedules`/`collect_member_schedules` + helper 2)와
추가 2개(`create`/`delete_shared_schedule`)를 만든다.

`collect_member_schedules`가 이 주차의 핵심이다 — 내 일정(앱 SQLite + 현재 대화 임시)과 외부 멤버
busy-time을 **같은 6키 rows**로 합치고, 가이드 :99가 이 rows를 Week 6 `find_common_available_slots`의
`busy_rows` 근거로 지목한다. 즉 **다음 주차와의 연결 지점**이다.

## 2. 진행 구조 결정 — 회차 분리 기각

Week 4는 파일 헤더가 `[4주차 1회차/2회차]`로 쪼개져 있어 구현·커밋을 회차 분리했다. Week 5는
**한 번에** 가기로 했다(구현 순서와 커밋만 메인 → 추가로 분리).

근거: ① 파일 헤더에 회차 구분이 없고 "메인/추가"만 나뉜다. ② 7개 tool 중 5개가 동일한 pass-through
패턴이고 난이도는 `_personal_schedules_for_current_scope` + `_collect_member_schedules` 두 곳에 몰려
있는데 이 둘은 서로 붙어 있다. ③ 라우팅 프롬프트 하나가 7개 tool을 전부 좌우하므로, 추가 tool을 뺀
상태의 통과율은 최종 baseline이 될 수 없다(week-kickoff Step 0의 "앞 회차 단독 baseline 금지").

planner와 오케스트레이터가 독립적으로 같은 결론에 도달했다.

## 3. 실측 기반 프롬프트 결정

baseline은 **25축 × N=5 = 125/125 게이트 PASS**. 시계는 `2026-07-06(월)`로 고정 — 외부 실습 fixture가
`2026-07-07~17`이라 이 날짜여야 "이번 주/다음 주"가 seed에 걸린다.

첫 baseline은 **30/39(14축)** 이었고 실패 3축은 모두 0/3(결정적)이었다. 튜닝은 전부 eval/trace로만
판정했고, 정적 충돌 목록으로 프롬프트를 늘리지 않았다.

### (A) 서술형 규칙 3연패 → 워크드 예시가 뒤집었다

`external_answer_correct`("철수 이번 주 목요일에 뭐 있어?")가 `personal_list_saved_schedules`로 새는
문제에 **서술형 규칙을 세 번 강화했으나 전부 0/3**이었다. 프롬프트에 `'철수 목요일에 뭐 있어?'`를
거의 그대로 예시로 넣었는데도 안 움직였다. 이미 3/3으로 통과 중이던 `collect_both_sources`를 이끄는
`예:` **워크드 예시 형식**으로 바꾸자 **0 → 3/3**.

교훈: 이 저장소에서 라우팅을 실제로 옮기는 것은 서술형 금지문이 아니라 **입력 → tool·인자 형태의 예시**다.

### (B) 프롬프트에 tool 이름을 인용하면 salience가 올라 회귀를 부른다

`shared_create`를 고치려고 override 문장에 `extract_schedule_request → save_structured_request`를
**인용**했더니, 다음 실행에서 `external_member_routing`이 3/3 → 2/3으로 떨어지며 그 tool을 호출했다.
가드 한 줄로 상쇄해 복구(3/3). 인용은 필요할 때만, 인용하면 **그 tool의 오호출 축을 같이 재야 한다.**

### (C) 어휘 열거 → 구조적 판별자 (두 번 반복된 패턴)

앱 탐색 결함을 고치는 과정에서 **같은 실패와 같은 해법이 두 번** 나왔다.

| 문제 | 1차 시도 (어휘) | 결과 | 2차 (구조적 판별자) | 결과 |
|---|---|---|---|---|
| 외부 대화 vs 내 대화 | "나와 Kana가 **나눈 대화**"로 구분 | `"철수랑 예전에 나눈 대화"`가 내부로 오분류 → `previous_conversation_search` 5/5→**2/5** | **"외부 멤버 이름이 나오는가"** | 5/5 복구 |
| collect vs extract | collect 쪽으로 강하게 밀기 | `"내 일정은 빼고"`도 collect로 → `extract_not_collect` 5/5→**1/5** | **"질문에 '나/내'가 들어가는가"** | 양쪽 5/5 |

교훈: 라우팅 규칙은 **표현 어휘가 아니라 입력에서 기계적으로 판별 가능한 신호**로 쓴다.
어휘로 쓰면 반드시 그 어휘를 비껴가는 입력이 나오고, 반대편 축을 밀어낸다.

### (D) 프롬프트 튜닝에도 양방향 검사가 필요하다 (반성)

(C)의 회귀 3축은 **수정 당시 스팟 체크에서 안 잡혔다.** 고치려는 축만 재고 반대편 축을 같이 재지
않아, N=5 전체 실행에서야 드러났다. §4-A에서 세운 양방향 검사 규칙은 **검증 자산뿐 아니라 프롬프트
수정 검증에도** 적용해야 한다.

## 4. 검증 자산 설계 결정

`verify-week5`는 Week 4와 달리 **1~10단계 전부 `PROXY_TOKEN` 없이 실행된다.** Week 5 경로는 로컬 MCP
subprocess + SQLite뿐이라 임베딩을 타지 않는다. 실 왕복(등록→조회→삭제)까지 키 없이 잰다.

### (A) 거르는 로직은 양방향으로 검사한다 → `kanana-conventions` §6

중복 제거 검사가 "걸러야 할 것을 거르는가" 한 방향만 있었고, 그 사각지대로 **별개 일정을 합쳐버리는
결함이 verify·eval을 모두 통과**했다(§5). 반대 축 `collect_no_over_dedup`(종료시각만 다른 두 일정이
2건으로 남는가)을 추가해 양방향을 잠갔다. Week 4의 `add_reminder_guard`(과교정 방지)가 같은 패턴인데
규칙화하지 않아 재발한 것이다 → 일반 규칙으로 승격.

### (B) 판정축은 "금지 조건 검사"를 우선한다 → `kanana-conventions` §6

정답의 모양(정답 어휘·정답 tool 이름)을 열거한 케이스 **3개가 정상 동작을 FAIL로 오판**했다.

| 케이스 | 정답 모양 판정 | 실제 | 금지 조건 판정으로 교체 |
|---|---|---|---|
| `week6_boundary` | 답변에 `후보`·`괜찮` 등이 있어야 통과 → **0/3** | "피해서 잡으면 좋을 것 같습니다" (정상) | `create_shared_schedule` 호출 or `확정했` → 실패 |
| `shared_create` | `member_names=["지훈"]`으로 조회돼야 통과 → **0/3** | `member_name="나"`로 등록 (타당) | 그 날짜에 row가 생겼는가 |
| `search_member_names_arg` | `member_names`까지 채워야 통과 → **2/3** | 이름을 query에 안 넣음 (규칙 준수) | query 오염만 게이트 |

반대로 **실제 결함을 잡은 축은 전부 금지 행동·상태 변화**를 봤다. `delete_shared_routing`은 tool 이름만
봤으면 통과였지만 **삭제 후 DB에 row가 남았는지**를 봐서 잡았다.

### (C) 멀티턴 하네스 (`Case.context_turns`)

앱 탐색이 잡은 결함 2건이 **단일 턴 eval에서 모두 3/3 통과**했다. 판정 턴 앞에 선행 발화를 실제로
실행하고 앱과 같은 방식으로 user/assistant 텍스트만 history에 쌓는 `context_turns`를 추가하자 0/3으로
재현됐다. 원인은 **모델이 직전 턴의 tool 조합을 그대로 이어받는 것**이라 이력 없이는 재현 불가능하다.

## 5. 재사용 원칙 위반 — 3단계가 모두 통과시킨 사건

`_personal_schedules_for_current_scope`의 중복 제거 키를 **자체 제작**(`(title, date, start_time)`)했다.
그런데 week03에 `_content_schedule_id`(:254)가 이미 있었고, 그것이 바로 **비교 대상인 DB `schedule_id`를
만들어낸 함수**였다. 재구현은 컨벤션 위반인 동시에 **기능 결함**이었다 — 키가 3필드뿐이라 종료시각·
참석자만 다른 별개 일정을 하나로 합쳤다.

실패 경로:

| 단계 | 무엇을 했나 | 왜 못 잡았나 |
|---|---|---|
| planner | `_content_schedule_id`를 **정확히 찾아냄** | "문제의 원인"으로만 서술하고 §3 재사용 자산 표에 넣지 않음 |
| 오케스트레이터 | builder 명세에 키 알고리즘을 직접 지시 | "이 키를 만든 기존 함수가 뭔가"를 묻지 않고 **과잉 명세** |
| builder | 명세대로 구현 | 명세가 구체적이라 탐색할 이유가 없었음 |
| verifier | 오합침을 **관측함** | "승인된 결정 범위"라며 **관찰로 강등** |
| verify/eval | dedup을 한 방향만 검사 | 반대 축 부재 |

**사후 사람 리뷰가 잡았다.** 수정 후 `structured_request_from_week01_schedule` + `_ensure_content_dedup_key`
(week03 저장 경로와 같은 시퀀스)로 키를 되짚어 정확히 일치시켰고, 신규 helper는 0개가 됐다.

도출한 규칙 3종:
- **A** 양방향 검사 → `kanana-conventions` §6
- **B** 판정 유예 금지 → `verifier.md` ("승인된 결정"은 강등 사유가 아니다 + `설계 리스크` 보고 섹션 신설)
- **C** 재사용 리뷰 게이트 → `week-kickoff` 1.5단계 (`/simplify`)

## 6. 앱 탐색이 잡은 것 (필수 단계인 이유)

verify·eval이 다 통과한 뒤 앱 trace에서 결함 2건이 나왔다. 4주차와 같은 패턴이다.

1. **`collect_member_schedules` 우회** — 나+팀원 수집 요청에 `personal_list_saved_schedules` +
   `extract_schedules_from_history` 조합으로 LLM이 직접 병합. Week 6 `busy_rows` 연결 지점이 끊기고,
   키 구조가 다른 결과를 산문으로 합치게 된다. **직전 턴 조합의 이어받기**가 원인.
2. **내 앱 대화를 외부 대화 검색으로 라우팅** — `"아까 우리가 무슨 얘기 했지?"` →
   `search_previous_conversations{query:"얘기"}` → 0건. 출처 분리의 **반대 방향** 사각지대였다.
   덤으로 "한 단어 핵심어" 규칙이 **주제 명사** 조건 없이 쓰여 질문의 낱말을 그대로 옮기는 것도 드러났다.

둘 다 eval 케이스로 승격(`collect_without_cue`, `own_conversation_not_external`) 후 수정·재측정했다.

## 7. 범위 밖으로 분류한 것 (고치지 않음)

- **Week 3 저장 성향** — `"다음 주 회의 잡아줘"`에 되묻지 않고 즉시 저장하며 날짜를 임의 확정(`members: []`).
- **Week 3 조회 기간 축소** — `"내 일정 뭐 있어?"`에 `date_from == date_to == 오늘`.
  Week 5 인자 규칙으로는 고쳤지만 Week 3 tool 경로는 그대로 둔다.
- `normalize_external_schedule_date_bounds`의 첫 인자 `member_names`가 실제로 쓰이지 않음
  (`fixed/`는 읽기 전용, 학생 코드는 시그니처를 지킨 것이라 정상).

## 8. 다음 주차로 넘기는 것

- **양방향 검사를 프롬프트 수정에도 적용** (§3-D). 고치는 축과 반대 축을 함께 스팟 체크한 뒤 전체를 돌린다.
- **`context_turns`를 기본 도구로**. 라우팅 축은 단일 턴만으로 안전하다고 보지 않는다.
- Week 6은 `collect_member_schedules`의 rows를 `busy_rows`로 받는다. 이 tool의 6키 구조와
  `member_name="나"` 규약이 계약이므로 깨뜨리지 않는다.
