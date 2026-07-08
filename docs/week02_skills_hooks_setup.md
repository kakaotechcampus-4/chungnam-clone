# Week 2 — Skills · Hooks 셋업 plan

대상 디렉토리: [`.claude/skills/`](../.claude/skills/) · [`.claude/hooks/`](../.claude/hooks/) · [`.claude/settings.json`](../.claude/settings.json)
작업 브랜치: `yoojongho/week2`

> 이 문서는 2주차 subagent 워크플로우([week02_subagent_setup_plan.md](week02_subagent_setup_plan.md))를 보강하기 위해
> 추가한 **Skills·Hooks 셋업 내역**을 정리한다. 실제 과제 구현이 아니라 그 구현을 돕는 도구 계층이다.

---

## 1. 배경 (Context)

planner/builder/verifier subagent를 갖춘 뒤, 워크플로우를 더 견고하게 만들기 위해 skills와 hooks를 추가했다.

- **Skills** — 반복되는 규칙/절차를 `SKILL.md`로 캡슐화. Claude가 관련 시 자동 로드하거나 `/이름`으로 호출하며,
  subagent의 `skills:` frontmatter로 **미리 로드**할 수 있다.
- **Hooks** — 라이프사이클 지점에서 셸 명령을 **결정론적**으로 실행. LLM 재량에 맡기지 않고 규칙을 강제한다(exit 2 = 차단).

### 참고한 공식 문서
- Skills: <https://code.claude.com/docs/ko/skills>
- Hooks: <https://code.claude.com/docs/ko/hooks-guide>

### 설계 결정
- **채택**: S1 `kanana-conventions`, S2 `verify-week2`, H1 `fixed/` 보호, H2 자동 `py_compile`.
- **planner에는 hook 없음** — 도구가 `Read/Grep/Glob`뿐이라 편집·위험 명령이 구조적으로 불가. read-only 화이트리스트가
  이미 hook이 줄 보호를 대체하므로 중복. planner에는 S1 skill만 preload.
- **Windows 대응** — hook은 Git Bash로 실행되고 `jq`가 없을 수 있어, 문서 예시의 `jq` 대신 **Python 스크립트**로 stdin JSON을 파싱.
- **문서화** — 공식 문서상 별도 문서는 필수가 아니나(SKILL.md·settings.json·hook docstring이 자체 정본), 프로젝트 관례로 이 파일을 남긴다.

---

## 2. 생성 대상 (Deliverables)

```
.claude/
  skills/
    kanana-conventions/SKILL.md   # S1
    verify-week2/SKILL.md         # S2
  hooks/
    protect_paths.py              # H1 스크립트
    check_syntax.py               # H2 스크립트
  settings.json                   # H1·H2 등록
  agents/{planner,builder,verifier}.md   # skills: [kanana-conventions] preload 추가
```

### Skills

| Skill | 유형 | frontmatter 요지 | 역할 |
| --- | --- | --- | --- |
| [kanana-conventions](../.claude/skills/kanana-conventions/SKILL.md) | 참조 지식 | `user-invocable: false` (·`disable-model-invocation` 미설정 → preload 허용) | 과제 공통 규칙(가이드/TODO 우선, fixed/ 읽기전용, 임의값 금지, 필드 기본값 관례, helper 재사용). planner·builder·verifier에 preload |
| [verify-week2](../.claude/skills/verify-week2/SKILL.md) | 작업 | `allowed-tools: Bash(uv *)` | Week2 정적 검증 3종(py_compile·import·Pydantic 인스턴스화) 실행. `/verify-week2`로 호출 |

### Hooks (근거: hooks 공식 문서 "보호된 파일 편집 차단" / "편집 후 자동 실행")

| Hook | 이벤트 · matcher | 스크립트 | 동작 |
| --- | --- | --- | --- |
| H1 | `PreToolUse` · `Edit\|Write` | [protect_paths.py](../.claude/hooks/protect_paths.py) | 편집 경로에 `fixed/` 세그먼트가 있으면 **exit 2로 차단** + 사유를 Claude에 피드백 |
| H2 | `PostToolUse` · `Edit\|Write` | [check_syntax.py](../.claude/hooks/check_syntax.py) | 편집 파일이 `student_parts/…*.py`면 `py_compile` 실행, 구문 오류 시 **exit 2**로 오류 반환 |

등록 파일: [`.claude/settings.json`](../.claude/settings.json)
```json
{ "hooks": {
  "PreToolUse":  [ { "matcher": "Edit|Write", "hooks": [ { "type": "command",
      "command": "uv run python \"$CLAUDE_PROJECT_DIR/.claude/hooks/protect_paths.py\"" } ] } ],
  "PostToolUse": [ { "matcher": "Edit|Write", "hooks": [ { "type": "command",
      "command": "uv run python \"$CLAUDE_PROJECT_DIR/.claude/hooks/check_syntax.py\"" } ] } ]
} }
```
> 두 스크립트는 Windows 콘솔 코드페이지와 무관하게 한국어 피드백이 깨지지 않도록 `sys.stderr.reconfigure(encoding="utf-8")`를 적용했다.

---

## 3. 적용 규칙 (재시작 필요)

- 세션 시작 시 없던 **최상위 `.claude/skills/` 디렉토리와 새 `settings.json`**을 로드하고, skill을 subagent에 실제 preload하려면
  **Claude Code 재시작 1회**가 필요하다(공식 문서: 새 최상위 skills 디렉토리는 재시작해야 감시).
- 재시작 후 `/hooks`로 hook 등록, `/doctor`로 skill 로드/중복을 확인한다.

---

## 4. 검증 결과 (셋업 스모크 테스트 — 완료)

| 항목 | 명령 | 결과 |
| --- | --- | --- |
| H1 차단 | `echo '{"tool_input":{"file_path":"fixed/llm.py"}}' \| uv run python .claude/hooks/protect_paths.py` | exit **2**, UTF-8 사유 출력 ✅ |
| H1 통과 | `...file_path":"student_parts/week02_...py"...` | exit **0** ✅ |
| H2 통과 | 정상 `student_parts/*.py` | exit **0** ✅ |
| H2 무시 | `README.md`(비대상) | exit **0** ✅ |
| H2 차단 | 구문 오류 `.py` | exit **2**, `py_compile` 오류 반환 ✅ |
| skill 로드 | 세션 등록 | `kanana-conventions`·`verify-week2` 인식 ✅ |
| agent frontmatter | `name/description/tools/model/color/skills` | 3개 모두 유효(`ALL_OK`) ✅ |

---

## 5. 이후 단계 (Next)

재시작 → `/hooks`·`/doctor` 확인 → **planner 재실행**(S1 preload 반영) → 메인 체크포인트 →
builder(H1·H2·S1 혜택) → verifier(S2 활용) 순으로 2주차 과제 구현을 이어간다.
자세한 오케스트레이션은 [학습 계획 파일의 3단계](week02_subagent_setup_plan.md)와 연동된다.
