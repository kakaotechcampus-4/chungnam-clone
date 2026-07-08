# Week 2 — 서브에이전트(planner / builder / verifier) 구축 plan

대상 디렉토리: [`.claude/agents/`](../.claude/agents/)
작업 브랜치: `yoojongho/week2`

> 이 문서는 **2주차 과제 구현 이전에**, 메인 세션 오케스트레이터가 작업을 위임할
> **프로젝트 전용 서브에이전트**를 구축한 계획을 담는다. 실제 스키마/agent 구현 계획이 아니라
> "그 구현을 수행할 도구(에이전트)"를 갖추는 단계다.

---

## 1. 배경 (Context)

2주차 과제([`student_parts/week02_structure_natural_language_requests.py`](../student_parts/week02_structure_natural_language_requests.py))는
`StructuredRequest` / `StructuredRequestBatch` 스키마와 Week 2 agent를 구현하는 일이다.
이 구현을 더 안정적으로 수행하기 위해, 먼저 **책임 범위별 서브에이전트**를 만든다.

Claude Code의 서브에이전트는 자체 컨텍스트 윈도우에서 실행되며, `description`을 근거로 메인
세션이 위임을 결정한다. 이를 통해 얻는 이점:

- **컨텍스트 절약** — 탐색/구현/검증을 메인 대화에서 분리해 각자 별도 윈도우에서 수행하고 요약만 반환
- **병렬 처리** — 큰 작업 단위를 책임 범위별로 나눠 위임
- **편향 없는 검증** — 구현자(builder)와 분리된 컨텍스트의 verifier가 독립적으로 검증
- 메인 세션이 각 에이전트에 **명확·꼼꼼한 요구사항 프롬프트**를 주면 위임 품질이 올라가고,
  플랜 모드 계획 실행 단계에서도 이 에이전트들을 그대로 활용할 수 있다

### 설계 결정

- **성격**: 범용 planner/builder/verifier 로 만들되, 이 Kanana LangChain 과제 맥락
  (주차별 `[수강생 구현 가이드]`/`# TODO` 우선, `fixed/`는 읽기 전용·재사용, 임의값 생성 금지)을 본문에 포함.
  → 2주차 이후 주차에도 재사용 가능.
- **언어**: 한국어+영어 병기 (핵심 지시는 한국어, 필드명/기술 용어는 영어).
- **모델(역할별 차등)**: `planner=opus`(깊은 설계), `builder=inherit`(세션 모델), `verifier=opus`(엄밀한 검증).
- **범위(scope)**: 프로젝트 범위 [`.claude/agents/`](../.claude/agents/) — 버전관리에 커밋해 팀 공유.

### 참고한 공식 문서

- Claude Code sub-agents: <https://code.claude.com/docs/ko/sub-agents>
- `.claude` 디렉토리: <https://code.claude.com/docs/ko/claude-directory>

핵심 규칙: 서브에이전트 = `.claude/agents/*.md` (YAML frontmatter + 본문=system prompt),
`name`·`description`만 필수. `tools` 생략 시 전체 상속, `tools`는 화이트리스트 /
`disallowedTools`는 블랙리스트. `model` 기본값은 `inherit`. `description`의 "use proactively"가 자동 위임을 촉진.

---

## 2. 생성 대상 (Deliverables)

```
.claude/
  agents/
    planner.md      # 요구사항 분석 · 단계별 구현 계획 (read-only)
    builder.md      # 계획/명세 기반 코드 구현 (편집 가능)
    verifier.md     # 구현 결과 독립 검증 (read-only + Bash)
docs/
  week02_subagent_setup_plan.md   # (이 문서) 구축 계획 기록
```

| 에이전트 | 책임 | `tools` | `model` | 특징 |
| --- | --- | --- | --- | --- |
| `planner` | 요구사항 분석·구현 계획 | `Read, Grep, Glob` | `opus` | 코드 무수정, `file:line` 근거 계획만 반환 |
| `builder` | 계획대로 코드 구현 | `Read, Edit, Write, Grep, Glob, Bash` | `inherit` | 주변 스타일 준수, TODO 정확 충족, 구문검사 자체수행 |
| `verifier` | 독립·비편향 검증 | `Read, Grep, Glob, Bash` | `opus` | 코드 무수정, 정적 검증 우선, PASS/FAIL 근거 반환 |

각 파일의 frontmatter와 system prompt 본문은 실제 파일을 참조:
[`planner.md`](../.claude/agents/planner.md) ·
[`builder.md`](../.claude/agents/builder.md) ·
[`verifier.md`](../.claude/agents/verifier.md).

---

## 3. 공통 규칙 (모든 에이전트 본문에 반영)

- 수강생 파일 상단 **`[수강생 구현 가이드]` 주석과 `# TODO`**를 요구사항 원본(source of truth)으로 삼는다.
- `fixed/` 제공 코드는 **읽기 전용·재사용 대상**. 수정하지 않는다.
- 기존 helper 재사용: `join_system_prompt`, `week01_prompt_parts`, `week01_tools`
  ([`student_parts/week01_wake_up_nana.py`](../student_parts/week01_wake_up_nana.py)),
  `current_app_date_iso` ([`fixed/runtime_clock.py`](../fixed/runtime_clock.py)),
  `chat_model` ([`fixed/llm.py`](../fixed/llm.py)), `CONFIG` ([`fixed/config.py`](../fixed/config.py)).
- **모르는 값을 임의로 지어내지 않는다**(과제 핵심 원칙): 불확실하면 `None`/빈 list/되묻기.
- 이 저장소는 자동 테스트 하네스가 없고 LLM 실행에 `PROXY_TOKEN`이 필요하므로,
  검증은 **키가 필요 없는 정적 검증(import·`py_compile`·Pydantic 인스턴스화)을 1차**로 한다.
  실행기는 `uv`(`./run.sh --week2` → `uv run python app.py`), 플랫폼은 Windows + Bash(Git Bash).

---

## 4. 사용 흐름 (Orchestration)

메인 세션(오케스트레이터)이 프롬프트 내용에 따라 필요한 에이전트를 선택해 위임한다.

1. **planner** — "2주차 과제 요구사항을 분석하고 구현 계획을 세워줘" → 계획(요구사항·변경대상·순서·검증)만 반환
2. **builder** — planner 계획을 명세로 넘겨 "이 계획대로 구현해줘" → 코드 구현 + 구문검사
3. **verifier** — "방금 구현을 독립적으로 검증해줘" → 요구사항별 PASS/FAIL 근거 반환

builder와 verifier를 **분리된 컨텍스트**로 두어 검증 편향을 줄인다. 필요 시 독립 작업 단위를 병렬 위임한다.

---

## 5. 검증 계획 (에이전트 구축 자체 확인)

1. **Claude Code 재시작** — 세션 시작 시 없던 `.claude/agents/`는 재시작해야 감시·로드된다(공식 문서 명시).
2. `/doctor` 실행 → 에이전트 이름 중복/로드 상태 확인.
3. 위임 스모크 테스트:
   - "planner 서브에이전트로 2주차 과제 구현 계획을 세워줘" → 계획만 반환(코드 무수정) 확인
   - "builder 서브에이전트로 …" → 편집 수행 + 구문검사 확인
   - "verifier 서브에이전트로 방금 구현을 검증해줘" → PASS/FAIL 근거 반환 확인
4. 각 `.md`의 YAML frontmatter 유효성(필수 `name`/`description`, `tools` 철자, `model` 값) 확인.

---

## 6. 이후 단계 (Next)

이 문서는 **인프라 구축**까지만 다룬다. 실제 2주차 스키마/agent 구현은 구축된
**planner → builder → verifier** 흐름으로 이어서 진행한다.
