# Week 4 — 출처별 RAG 라우팅 plan (실측 기반 의사결정 기록)

> **이 문서의 무게중심은 week02·week03 문서와 다르다.** 오케스트레이션/skill/hook 인프라는 이제
> 안정·재사용 단계라 다시 기록하지 않는다. 인프라 상세는 [`week03_orchestration_plan.md`](week03_orchestration_plan.md)와
> [`.claude/skills/week-kickoff/`](../.claude/skills/week-kickoff/)에 위임하고, 이 문서는 **코드에는 결과만
> 남고 "왜 그렇게 정했는지"는 안 남는 부분** — 프롬프트/라우팅 의사결정의 실측 근거 — 만 담는다.

관련 자산: 대상 [`student_parts/week04_retrieve_nanas_memory.py`](../student_parts/week04_retrieve_nanas_memory.py) ·
검증 [`.claude/skills/verify-week4/`](../.claude/skills/verify-week4/) ·
평가 [`evals/week04_eval.py`](../evals/week04_eval.py) · [`evals/week04_baseline.json`](../evals/week04_baseline.json)

---

## 1. 배경 (Context)

Week 4의 목표는 RAG를 하나의 만능 함수로 보지 않고 **데이터 출처별 검색 tool로 분리**하는 것이다.
세 출처 — 개인 참고자료(ChromaDB+임베딩, `search_personal_references`), 저장한 일정/할일
(SQLite `search_saved_requests`), 일반 대화 기록(대화 단위 lazy sync RAG, `search_conversation_messages`) —
중에서 질문 성격에 맞는 tool을 고르게 한다. 가이드가 1회차/2회차로 나뉘어 있다.

## 2. 진행 구조 결정 — 회차 분리

"1회차/2회차를 완전히 분리한 독립 사이클 2회"를 냉정하게 검토한 뒤 **기각**하고 다음으로 정했다:

- **계획은 하나만 공유**(planner는 두 회차 11개 TODO를 한 번에 계획, 오케스트레이터와 교차대조).
- **구현·커밋만 회차별로 분리**(Cycle A=1회차, Cycle B=2회차, 각각 builder→verifier→commit).
- **동작 eval(통과율)은 파일이 온전해지는 2회차에서.**

근거: ① 회차마다 planner를 다시 돌리면 두 독립 판단이 붕괴해 교차검증이 사라진다. ② 라우팅 프롬프트
(`week04_prompt_parts`)가 **2회차 산출물**이라, 1회차 단독으로는 라우팅 baseline을 잡을 수 없다(반쪽).
이 구조 결정은 실행 전에 내리는 것이라 eval/trace가 못 잡아 **게이트에서 명시적으로 확인**했다.
→ 이 교훈은 [`week-kickoff`](../.claude/skills/week-kickoff/SKILL.md) Step 0에 일반 규칙으로 승격.

## 3. 실측 기반 프롬프트 결정 (정적 분석이 아니라 trace/통과율로 판정)

핵심 baseline: [`week04_eval.py`](../evals/week04_eval.py) 핵심 7케이스를 각 5회, `build_week04_agent()`
실경로 + 고정 시계 + temp SQLite/ChromaDB 격리로 측정해 **35/35 게이트 PASS**.
(콘솔 총계는 `35/40` — ambiguous로 둔 `reference_rule_routing` 5회가 분모에 잡히나 게이트에서 제외된다. §3-C 참조.)

### (A) CoT workflow 미도입 — probe로 깨보고 결정

"week04_prompt_parts에 단계적 사고(CoT) 절차를 넣어야 하는가"를 글로 따지지 않고, 일부러 어렵게 만든
probe 케이스로 현재 프롬프트를 깨봤다.

- `multi_source_combine`(다중출처 결합): 첫 판정은 0/3이었으나 **트레이스를 읽으니 check 결함**이었다 —
  모델은 일정 소스(`personal_list_saved_schedules`)+참고자료 소스를 잘 엮었는데 check가 특정 함수 이름만
  요구했다. "두 출처를 엮었는가"로 완화하자 **3/3 → 최종 5/5**.
- 애매 질문 probe: 낮은 통과율은 결함이 아니라 질문 자체의 본질적 모호성이었다.

**결론: CoT 미도입.** 다중출처 결합은 이미 동작했고 통과율이 만점이라, "안 깨진 것 안 고침" 원칙대로
프롬프트를 늘리지 않았다. 빨간 숫자를 그대로 믿지 않고 trace를 먼저 읽은 것이 핵심.

### (B) add 오라우팅 수정 — 앱 발굴 → eval 승격 → override

verify·eval이 통과한 뒤 앱(`./run.sh --week4`)에서 발굴: `회의는 45분 넘기지 말자고 메모해둬`가
`add_personal_reference`가 아니라 Week 3 저장 흐름(`extract_schedule_request`→`save_structured_request`)으로
샜다(반복 재현). 원인은 `week04_prompt_parts`가 검색 3종만 안내하고 **add 경로를 언급하지 않아** Week 3의
강한 저장 라우팅에 밀린 것.

| 단계 | add_reference_routing | add_reminder_guard(과교정 방지) |
|---|---|---|
| 약한 안내 1줄 | 0/3 (여전히 save로 샘) | 3/3 |
| 경쟁 tool(`extract_schedule_request`) 명시 배제 override | **3/3 → 최종 5/5** | **5/5** |

"지속적 선호/메모는 add_personal_reference로 보낸다"는 구분 지시를 **경쟁 tool을 콕 집어 배제**하는 형태로
넣어 해결했고, 진짜 리마인더는 여전히 저장으로 가는지 guard 케이스로 회귀를 막았다.

### (C) reference_rule zero-sum — 고치려다 회귀, 원복 후 ambiguous

앱에서 발굴: `점심에 회의 잡지 말라 했던 거 맞아?`가 참고자료 대신 `search_saved_requests`로 샘.
eval로 재현하니 baseline 프롬프트에서 **1/3**(회의 키워드가 저장 검색으로 끔).

- 두 tool 경계를 또렷하게 적는 수정 → `reference_rule_routing`은 **3/3**이 됐으나, 동시에
  `conversation_routing`이 **5/5 → 0/3**으로 회귀("제주도 여행 계획"이 저장 검색으로 샘).
- 어려운 케이스 하나를 위해 가이드 핵심인 대화 라우팅을 깨는 **나쁜 거래**라 판단 → **수정 원복**.

**결론:** 세 출처 라우팅은 서로 맞물린 zero-sum이라 프롬프트만으로 조율에 한계가 있다. 해당 케이스는
`ambiguous`(관측만, 게이트 제외)로 남겨 "알려진 한계"로 문서화(최종 baseline에서 0/5로 기록). eval-first가
없었으면 이 회귀를 모른 채 배포했을 것이다.

## 4. 검증 자산

- **결정적 계약** [`verify-week4`](../.claude/skills/verify-week4/SKILL.md): 키 없이 되는 1~7단계(스키마·
  반환 키·배선·SQLite 왕복) + 키 있을 때 ChromaDB RAG 실경로 8단계. 프롬프트 수정 후 **최종 회귀에서도
  1~8단계 전부 PASS, 계약 0건 훼손**. (격리 하네스 방식은 week03 문서와 동일.)
- **확률적 행동** [`week04_eval.py`](../evals/week04_eval.py): 출처별 라우팅·다중출처 결합·add 경로·현재
  대화 제외를 통과율로 판정. `current_conversation_excluded`는 LLM 없이 helper 직접 호출로 결정적 판정.

## 5. 다음 주차로 넘긴 것 (회고 반영)

[`week-kickoff`](../.claude/skills/week-kickoff/SKILL.md)에 일반 규칙으로 승격한 두 가지:
1. **회차 분리 진행 구조** 규칙(Step 0) — 계획 공유·구현/커밋 분리·동작 eval은 마지막 회차.
2. **앱 수동 탐색을 필수 단계로 격상**(Step 4) — Week 4의 진짜 결함 2건(B·C)이 전부 여기서 나왔다.

미해결로 남긴 것: 서로 맞물린 출처 라우팅(§3-C)을 프롬프트만이 아니라 tool description·입력 스키마
차원에서 경계를 또렷하게 만드는 접근 — 멘토 논의 후 시도.
