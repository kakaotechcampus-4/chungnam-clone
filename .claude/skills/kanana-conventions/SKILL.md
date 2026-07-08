---
name: kanana-conventions
description: Kanana 주차별 실습 과제(student_parts/weekNN_*.py) 구현·계획·검증 시 따라야 할 공통 규칙. 가이드/TODO가 source of truth, fixed/ 읽기 전용, 임의값 금지, Pydantic 필드 기본값 관례, 기존 helper 재사용을 정의한다.
user-invocable: false
---

# Kanana 과제 공통 규칙 (Conventions)

이 저장소의 주차별 실습 과제를 계획·구현·검증할 때 항상 아래 규칙을 따른다.

## 1. Source of truth
- 대상 파일(`student_parts/weekNN_*.py`) **상단의 `[수강생 구현 가이드]` 주석과 각 `# TODO`**가 요구사항의 최우선 근거다. 여기서 벗어나지 않는다.

## 2. 수정 범위
- **수정 대상은 지정된 `student_parts/` 파일뿐이다.**
- `fixed/` 디렉토리의 제공 코드는 **읽기 전용**이다. 절대 수정하지 않고 재사용/참조만 한다.

## 3. 값 생성 원칙
- **모르는 값을 임의로 지어내지 않는다.** 확실하지 않으면 `None` 또는 빈 list로 둔다.
- 날짜/시간은 확실할 때만 `YYYY-MM-DD` / `HH:MM` 형식으로 채운다.

## 4. Pydantic 필드 기본값 관례
- `str | None` 필드 → `Field(default=None, description="...")`
- list 필드 → `Field(default_factory=list, description="...")`
- 문자열 필드 → `Field(default="", description="...")`
- 함수 기반 기본값 → `Field(default_factory=<함수이름>)` (괄호로 호출하지 않고 함수 객체 전달)
- **기본값을 지정하지 않은 필드는 필수(required) 필드**다. 가이드 TODO가 특정 필드에만 기본값을 명시하지 않았다면 그 필드는 필수로 둔다.
- 모든 필드에 LLM structured output이 이해할 **한국어 `Field(description=...)`**를 단다.

## 5. 재사용 우선 (새로 짜지 말 것)
- `join_system_prompt`, `week01_prompt_parts`, `week01_tools` — `student_parts/week01_wake_up_nana.py`
- `current_app_date_iso` — `fixed/runtime_clock.py`
- `chat_model` — `fixed/llm.py`
- `CONFIG`(`has_openai_key`) — `fixed/config.py`
이미 존재하는 helper를 다시 구현하지 않는다.

## 6. 검증
- 자동 테스트 하네스가 없다. 검증은 **키가 필요 없는 정적 검증**(모듈 import, `py_compile`, Pydantic 스키마 인스턴스화)을 1차로 한다.
- LLM 앱 실행(`./run.sh --week2`)은 `.env`의 `PROXY_TOKEN`이 있을 때만 가능하다.
- 실행기는 `uv`(`uv run python ...`), 플랫폼은 Windows + Bash(Git Bash).
