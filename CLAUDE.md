# CLAUDE.md

Kanana Schedule Agent (LangChain 실습) 저장소. 주차별 실습 과제(`student_parts/weekNN_*.py`)를
구현·검증하며 tool-calling agent를 단계적으로 쌓는다.

이 파일은 **자동 로드되는 오리엔테이션**이다. 상세 규칙은 복제하지 않고 아래 skill에 위임한다 —
두 곳에 적으면 조용히 어긋난다. 규칙이 궁금하면 해당 skill을 정본으로 본다.

## 작업 규칙 (정본은 skill)
- **수정 대상은 지정된 `student_parts/weekNN_*.py`뿐.** `fixed/`는 **읽기 전용**(참조만).
- 대상 파일 상단의 `[수강생 구현 가이드]` 주석 + 각 `# TODO`가 요구사항의 유일한 근거(source of truth).
- Pydantic 필드 기본값·임의값 금지·helper 재사용 등 **공통 규칙 상세 → `kanana-conventions` skill이 정본.**

## 환경 / 실행
- 플랫폼: Windows + Bash(Git Bash). 실행기: **`uv run python -X utf8 ...`** (`-X utf8`로 한글 출력 보존).
- 앱 실행: **`./run.sh --weekN`** (Gradio). 실제 LLM 경로는 `.env`의 `PROXY_TOKEN`이 있어야 동작.
- 편집(`Edit`/`Write`)마다 hook가 자동 실행됨: `protect_paths.py`(사전, 보호 경로 차단) + `check_syntax.py`(사후, 구문 검사).

## 커밋
- **커밋 메시지에 `Co-Authored-By`를 붙이지 않는다.**
- **커밋은 사용자가 직접 한다** — 커밋 명령(메시지 포함, EOF 힙독)을 제시하고 실행은 사용자에게 맡긴다.
- 논리 단위 커밋: `chore`(검증 자산) / `feat`(구현) / `test`(eval + baseline).

## skill / agent 색인 (언제 무엇을 로드)
- 새 주차 과제 시작 → **`week-kickoff`** (표준 실행 루틴; 사용자가 `/week-kickoff`로 호출).
- 과제 구현·계획·검증의 공통 규칙 → **`kanana-conventions`**.
- system prompt / tool description 설계 → **`prompt-engineering`**.
- 주차 결정적 계약 검증 → **`verify-weekNN`** (`verifier.md` frontmatter에 등록).
- 하위 에이전트: `planner`(계획, 코드 수정 안 함) → `builder`(구현) → `verifier`(독립 검증). 단계 사이는 **사용자 승인 게이트**.
