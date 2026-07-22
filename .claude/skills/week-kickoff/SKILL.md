---
name: week-kickoff
description: 새 주차 과제(student_parts/weekNN_*.py)를 시작할 때 따르는 표준 실행 루틴을 메인 세션(오케스트레이터)에 로드한다. eval을 0단계에서 먼저 세우고, planner→builder→verifier를 사용자 승인 게이트로 진행하며, 프롬프트는 정적 분석이 아니라 trace/eval로 판정한다. 주차 시작 시 사용자가 직접 호출한다.
---

# 주차 과제 표준 실행 루틴 (Week Kickoff)

새 주차 과제를 시작할 때 이 순서를 따른다. 이 루틴은 **메인 세션(오케스트레이터)** 이 지키는 것이고,
각 단계 사이는 **사용자 승인 게이트**다. 승인 없이 다음 단계로 넘어가지 않는다.

> **가장 중요한 규칙 (3주차 반성):** `weekNN_eval` 골든셋을 **맨 마지막이 아니라 0단계에서 먼저** 설계한다.
> 3주차에 eval을 마지막에 만들어, 자동화 가능한 검증(중복 저장 확인)을 손으로 5번 반복했다.

## 실행 순서

### 0. 준비 — 과제 파악 + 검증 도구 뼈대 먼저
- 대상 파일 `student_parts/weekNN_*.py`의 `[수강생 구현 가이드]` 주석 + 각 `# TODO`를 읽는다(= source of truth).
- **planner에 독립 위임** → planner가 스스로 세운 계획을 오케스트레이터의 이해와 **교차대조**한다.
  오케스트레이터의 분석을 planner에 입력으로 넘기지 않는다(두 독립 판단이 하나로 붕괴하면 교차검증이 사라짐).
- **이 시점에 검증 도구 2종의 뼈대를 만든다:**
  - `verify-weekNN` skill — 결정적 계약(스키마·반환 키·안전규칙). `verify-weekN`(이전 주차) 형식을 그대로 이식하고 `verifier.md` frontmatter `skills:`에 등록.
  - **`weekNN_eval` 골든셋** — 확률적 행동(tool 호출 + 상태). 채널은 실제 앱 경로(`build_weekNN_agent()`), 시계 고정, temp DB 격리, 판정축은 tool 호출 목록 + DB/상태.
- → **[게이트] 계획 승인**

### 1. 구현 — builder
- planner 계획대로 **메인 → 추가** 순으로 구현. 편집마다 hook가 구문 검사.
- → **[게이트] 구현 결과 확인**

### 2. 계약 검증 — verifier + verify-weekNN
- 스키마·반환 키·안전규칙 등 **결정적 계약** PASS/FAIL. "코드가 스펙대로인가"만 확인(1회면 충분).
- → **[게이트] 검증 통과**

### 3. 행동 baseline 첫 측정 — eval 바로 실행
- 구현 직후 `uv run python -X utf8 evals/weekNN_eval.py --n N` 실행 → 첫 통과율 관측 + baseline 저장.
- **데이터 파괴류 결함(중복 저장·오삭제)이 여기서 통과율로 즉시 드러난다.** 손으로 반복 확인하지 않는다.

### 4. 프롬프트 튜닝 — eval을 도구로
- 프롬프트를 바꿀 때마다 eval 재실행 + `--baseline` diff로 before/after 판정. **수동 반복 실행 금지.**
- 고칠지 말지는 **통과율/trace로** 정한다. 정적 충돌 목록만으로 프롬프트를 늘리지 않는다(**"안 깨진 것 안 고침"**).
- 앱 직접 실행은 **회귀 확인이 아니라 새 실패 모드 탐색용으로만**. 발견하면 eval 케이스로 추가한다.

### 5. 최종 회귀 + baseline 확정
- `verify-weekNN` 재실행(회귀) + eval 최종 baseline 저장.

### 6. 멘토 리뷰 반영 → 커밋 → PR
- 멘토 피드백이 오면 같은 루틴(planner→builder→verifier, 게이트)으로 반영한다.
- 논리 단위 커밋(구현 / 검증 자산 / docs / eval). PR 본문은 `[week N] :` 스타일. **커밋 메시지에 Co-Authored-By를 붙이지 않는다.**

## 재사용 vs 매 주차 새로

| 그대로 재사용 | 매 주차 새로 |
|---|---|
| `.claude/agents/`(planner·builder·verifier), `.claude/skills/`(kanana-conventions, prompt-engineering, week-kickoff), `.claude/hooks/`(protect_paths, check_syntax) | `verify-weekNN` skill(+`verifier.md` 등록), `evals/weekNN_eval.py`·`weekNN_baseline.json`, `docs/weekNN_*.md` |

## 핵심 원칙 3줄
1. **eval을 0단계에서 먼저** 세우고, 프롬프트 판단은 eval/trace로만(정적 분석 X).
2. **verify = 결정적 계약(1회), eval = 확률적 행동(N회 통과율)** 으로 역할을 나눈다.
3. **각 단계 게이트에서 사용자 승인** 후 다음으로.

상세 근거·표는 `docs/week03_orchestration_plan.md` §8 참조.
