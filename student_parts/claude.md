# 작업 목표

일정(Schedule)의 CRUD가 가능한 LLM Agent를 만든다.
(Week 1: `personal_create_schedule` / `personal_list_schedules` / `personal_delete_schedule` 3개 tool로 개인 일정 생성·조회·삭제를 구현)

# 수정 범위

- 수정 대상 파일은 `./week01_wake_up_nana.py` 하나뿐이다. 그 외의 코드/파일(`fixed/` 등)은 건드리지 않는다.
- `week01_wake_up_nana.py` 안에서도 아래 3개 `@tool` 함수의 본문(`# TODO` 부분)만 구현한다.
  - `personal_create_schedule`
  - `personal_list_schedules`
  - `personal_delete_schedule`
- 그 외 함수(`join_system_prompt`, `_json`, `_now_iso`, `_new_personal_id`, `_schedule_scope`,
  `_current_session_schedules`, `week01_tools`, `week01_system_prompt`, `week01_prompt_parts`,
  `build_week01_agent`, `build_week_agent`, `list_personal_schedule_dicts`,
  `ensure_demo_personal_schedule`)는 구현 대상이 아니므로 수정하지 않는다.

# 하지 말아야 할 것

- 파일 최하단의 `[수강생 구현 가이드]` 주석은 프롬프트(과제 출제 의도) 확인용으로, **컨닝하지 않는다**.
  즉 그 주석 내용을 읽고 그대로 베끼거나, 답을 그 주석에서 가져오는 방식으로 구현하지 않는다.
- 이미 동일한 기능을 하는 함수가 파일 안에 존재하면, 그 로직을 직접 인라인으로 재구현하지 않고
  기존 함수를 그대로 호출해서 사용한다. (예: JSON 직렬화는 `_json`, ID 생성은 `_new_personal_id`,
  타임스탬프는 `_now_iso`, 현재 대화 범위 일정 조회는 `_current_session_schedules`/`current_session_scope`
  같은 기존 헬퍼를 재사용)
- 새로운 저장소(DB, 파일 등)를 추가하지 않는다. Week 1 일정은 `PERSONAL_SCHEDULES`(현재 프로세스 메모리)에만 존재한다.

# 구현 시 참고

- 반환값은 LangChain tool 관례상 JSON 문자열이며, `_json(payload)`로 감싼다.
- 현재 대화 범위 분리는 `fixed/session_scope.py`의 `current_session_scope()` 값을
  `session_id`로 저장/비교해서 처리한다.

# 공통사항 (3개 tool 구현 시 모두 적용)

- 이 agent 파이프라인(LangChain `create_agent`, tool calling 구조 등)은 2025.10부터 확립된 구성이다.
  학습된 기존 지식과 상충되는 부분이 있다면 추측하지 말고 검색해서 확인한다.
- JSON 문자열 반환은 반드시 이 파일의 `_json(payload)` helper를 사용한다. (LangChain tool은 문자열
  반환이 가장 안정적이므로, dict를 직접 만든 뒤 `_json(...)`으로 감싸서 반환한다.)
- 임시 저장소는 파일 상단에 정의된 `PERSONAL_SCHEDULES` 리스트를 사용한다. (새 저장소를 추가하지 않는다.)
- 새 일정 ID 생성은 미리 정의된 `_new_personal_id()`를 사용한다. (직접 ID를 만들지 않는다.)
- 생성 시각 파악은 `_now_iso()`를 사용한다.
- 채팅 범위 분리는 `fixed/session_scope.py`에 이미 구현된 함수를 활용한다.
  - `current_session_scope()` 값을 schedule dict의 `session_id` 필드에 넣어서 생성한다.
  - 조회(`personal_list_schedules`)와 삭제(`personal_delete_schedule`) 시에는 같은 `session_id`를
    가진 일정만 대상으로 처리한다. 다른 대화 범위의 일정은 조회/삭제 대상에서 제외한다.

# personal_create_schedule 구현 명세

- 매개변수는 이미 작성된 함수 시그니처를 그대로 따른다.
  (`title: str`, `date: str`, `start_time: str`, `end_time: str = "미정"`, `attendees: list[str] | None = None`)
- 반환값은 `_json(...)`으로 감싸기 전 기준으로 아래 dict 형태여야 한다.
  ```python
  {
      "ok": bool,
      "tool_name": "personal_create_schedule",
      "created_schedule": dict,
  }
  ```
- `created_schedule`은 아래 필드를 가진다.
  ```python
  {
      "id": str,
      "created_at": str,
      "title": str,
      "date": str,
      "start_time": str,
      "end_time": str,        # 기본값 "미정"
      "attendees": list[str],
      "session_id": str,
  }
  ```
- 필드별 값 채우는 규칙
  1. `id`는 `"personal_"` 접두어가 붙은 임시 ID이며 직접 만들지 않고 `_new_personal_id()`를 호출해서 사용한다.
  2. `created_at`은 현재 시각이며 직접 만들지 않고 `_now_iso()`를 호출해서 채운다.
  3. `attendees`가 `None`이면 빈 리스트(`[]`)로 바꿔서 넣는다.
  4. `session_id`는 대화 범위를 구분하는 키로, 직접 만들지 않고 `current_session_scope()`를 호출해서 채운다.
- 위 규칙으로 만든 `created_schedule` dict를 `PERSONAL_SCHEDULES`에 append해서 저장한 뒤,
  같은 dict를 반환 JSON의 `created_schedule` 값으로 사용한다.

## personal_create_schedule `@tool` docstring 작성 가이드

- LangChain `@tool`의 docstring은 LLM이 tool 선택·인자 작성 시 참고하는 설명이므로, 아래 내용이
  드러나도록 작성한다. (기존 한 줄 docstring을 대체/보강하는 용도이며, 로직 구현이 아니다.)
  - 이 함수가 **새 개인 일정을 만드는** 함수임을 명시한다.
  - `date` 인자는 `YYYY-MM-DD` 형식임을 명시한다.
  - `start_time`, `end_time` 인자는 `HH:MM` 형식임을 명시한다.
- docstring 작성도 `[수강생 구현 가이드]` 주석을 베끼지 않고 위 3가지 요구사항만 반영해서 직접 쓴다.

# personal_list_schedules 구현 명세

- 이 `@tool`은 **조회 전용** 함수다. `PERSONAL_SCHEDULES`를 추가/삭제/수정하지 않는다.
- 매개변수는 이미 작성된 함수 시그니처를 그대로 따른다.
  (`date_from: str | None = None`, `date_to: str | None = None`)
  - `date_from`, `date_to` 모두 주어질 경우 `YYYY-MM-DD` 형식을 따른다.
- 조회 절차
  1. 먼저 `_current_session_schedules()`를 호출해서 현재 대화 범위(`session_id`)에 속한 일정만 가져온다.
  2. 그 결과에 대해서만 `date_from`/`date_to` 기간 조회를 수행한다.
     - `date_from`이 주어지면 그 날짜 이상인 일정만 남긴다.
     - `date_to`가 주어지면 그 날짜 이하인 일정만 남긴다.
     - 날짜 비교는 `YYYY-MM-DD` 문자열 비교로 충분하다.
  - 특정 기간 조회는 별도 함수로 분리하지 않고 `personal_list_schedules` 안에서 직접 구현한다.
- 반환값은 `_json(...)`으로 감싸기 전 기준으로, `personal_create_schedule`의 반환 형태(`ok`, `tool_name`,
  `<결과 데이터 필드>` 3개 키로 구성되는 dict 형태)와 동일한 패턴을 따른다.
  ```python
  {
      "ok": bool,
      "tool_name": "personal_list_schedules",
      "schedules": list[dict],
  }
  ```

# `@tool` 데코레이터 작성법 (langchain_core 1.4.0 / langchain 1.3.2 기준)

`langchain.tools.tool`은 데코레이터로 쓸 때 두 가지 형태를 지원한다.

```python
# 1) 인자 없이: 함수 이름 그대로 tool 이름이 되고, docstring이 description이 된다.
@tool
def search_api(query: str) -> str:
    """Searches the API for the query."""
    ...

# 2) 이름/옵션을 명시: 첫 위치 인자가 tool 이름, description 키워드 인자로 설명을 직접 지정.
@tool("personal_delete_schedule", description="schedule_id와 일치하는 개인 일정을 삭제한다. 예: schedule-1")
def personal_delete_schedule(schedule_id: str) -> str:
    ...
```

- `name_or_callable`(첫 위치 인자)은 tool 이름을 덮어쓴다. 함수 이름과 다른 이름을 노출하고 싶을 때만 쓴다.
- `description` 키워드 인자를 주면 docstring보다 **우선**해서 LLM에게 노출되는 설명으로 쓰인다.
  description을 따로 안 주면 docstring이, 그것도 없으면 `args_schema`의 description이 쓰인다.
- 인자 포맷(예: `date`는 `YYYY-MM-DD`, `start_time`/`end_time`은 `HH:MM`)처럼 LLM이 tool 선택·인자
  작성 시 알아야 할 내용은 docstring이 아니라 `description=` 인자 안에 자연어로 풀어 쓴다.
  (이 프로젝트는 `description=`을 우선 사용하는 방식을 따른다.)
