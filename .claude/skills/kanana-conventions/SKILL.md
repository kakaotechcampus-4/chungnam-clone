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
- **제거·필터 로직은 반드시 양방향으로 검사한다.** 무언가를 걸러내는 규칙(중복 제거·필터·제외·정규화·안전규칙)은
  두 축을 **쌍으로** 시험한다:
  1. 걸러야 할 것을 실제로 거르는가
  2. **걸러선 안 될 것을 남기는가** (과잉 제거 = 과교정도 결함이다)

  한 축만 두면 "전부 걸러내기"·"아무것도 안 걸러내기" 같은 잘못된 구현이 통과한다.
  실제 사례: Week 5 중복 제거가 ①만 검사돼, 제목·날짜·시작시각만 같은 **별개 일정을 합쳐버리는** 결함이
  verify·eval을 모두 통과했다(사후 리뷰에서 발견). Week 4의 `add_reminder_guard`(과교정 방지)가 같은 패턴이다.
  이 규칙은 `verify-weekNN` skill과 `weekNN_eval` 골든셋 **양쪽 모두**에 적용한다.
- **판정축은 "금지 조건 검사"를 우선한다.** 케이스를 쓸 때 *정답의 모양*(정답 어휘·정답 tool 이름)을
  열거하지 말고, **금지된 행동을 했는가** 또는 **관측 가능한 상태가 어떻게 변했는가**로 판정한다.
  정답은 표현 방식이 무수히 많아 열거하면 반드시 빠지는 게 생기고, valid 구현을 FAIL시킨다.
  반면 금지된 행동의 목록은 짧고 명확하다.

  | | 정답의 모양을 정해두고 검사 | 금지 조건 검사 (권장) |
  |---|---|---|
  | 예 | 답변에 `후보`·`괜찮` 중 하나가 있어야 통과 | `create_shared_schedule`을 불렀거나 `확정했`가 있으면 실패 |
  | 예 | `delete_shared_schedule`을 불렀으면 통과 | **삭제 후 DB에 row가 남았으면** 실패 |

  Week 5 실측: 정답 모양 판정으로 만든 케이스 3개가 **정상 동작을 FAIL로 오판**했고,
  실제 결함을 잡은 축은 전부 금지 행동·상태 변화를 본 쪽이었다. 특히 삭제 케이스는
  tool 이름만 봤으면 통과했을 것을 **DB row 잔존**으로 잡았다.
  정답 모양 판정이 불가피하면, 실패 시 **구현 결함인지 판정이 좁은 건지부터 구분**한다.
- 자동 테스트 하네스가 없다. 검증은 **키가 필요 없는 정적 검증**(모듈 import, `py_compile`, Pydantic 스키마 인스턴스화)을 1차로 한다.
- LLM 앱 실행(`./run.sh --week2`)은 `.env`의 `PROXY_TOKEN`이 있을 때만 가능하다.
- 실행기는 `uv`(`uv run python ...`), 플랫폼은 Windows + Bash(Git Bash).
