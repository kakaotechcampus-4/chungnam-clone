# Week 1 개인 일정 CRUD 구현 Plan

## 1. 목표

`Kanana Schedule Agent`가 사용자의 개인 일정 요청을 받았을 때 LLM이 스스로 골라 호출할 수 있는 LangChain tool 3개를 완성한다. Week 1 일정은 앱 DB가 아니라 **현재 대화 전용 임시 메모리**(`PERSONAL_SCHEDULES`)에만 저장하며, tool 결과는 항상 JSON 문자열로 반환한다.

## 2. 수정 범위

- 구현 파일: `student_parts/week01_wake_up_nana.py` **단 하나**.
- 검증 파일: `test/test_week01_personal_schedule.py`를 새로 생성한다.
- 구현 대상: `personal_create_schedule`, `personal_list_schedules`, `personal_delete_schedule`, `week01_prompt_parts()`.
- `CHAT_MEMORY_PROMPT`는 현재 호출 경로에 연결되어 있지 않으므로 이번 작업에서는 수정하지 않는다.
- `fixed/`, `mcp_server/`, `app.py`, `static/` 등 기준 코드는 건드리지 않는다.

## 3. 사용할 헬퍼

이미 구현되어 있어 그대로 호출한다.

| 헬퍼                           | 위치                     | 역할                                     |
| ------------------------------ | ------------------------ | ---------------------------------------- |
| `_json(payload)`               | 같은 파일                | dict -> `ensure_ascii=False` JSON 문자열 |
| `_now_iso()`                   | 같은 파일                | 타임존 포함 ISO 생성 시각                |
| `_new_personal_id()`           | 같은 파일                | `"personal_"` 접두어 임시 ID             |
| `_schedule_scope(schedule)`    | 같은 파일                | 일정의 대화 범위(없으면 기본 scope)      |
| `_current_session_schedules()` | 같은 파일                | 현재 대화 범위 일정만 필터               |
| `current_session_scope()`      | `fixed/session_scope.py` | 현재 실행의 대화 범위 ID                 |
| `current_app_date_iso()`       | `fixed/runtime_clock.py` | 앱 시작 기준 오늘 `YYYY-MM-DD`           |

규약:

- tool은 dict가 아니라 **JSON 문자열**을 반환한다.
- 각 payload는 `ok`, `tool_name`과 함께 tool별 핵심 키(`created_schedule` / `schedules` / `deleted`)를 top-level로 유지한다.
- 일정의 주체는 사용자다. 설명과 prompt에서 agent가 일정의 소유자인 것처럼 읽히는 표현을 쓰지 않는다.
- agent 명칭은 `Kanana Schedule Agent`로 표현한다.

## 4. 구현 설계

### 4.1 `personal_create_schedule`

기존 함수 signature는 변경하지 않는다. `@tool`에는 agent가 볼 설명을 `description`으로 명시한다.

```python
@tool(
    "personal_create_schedule",
    description="사용자의 개인 일정을 생성한다. date는 YYYY-MM-DD 형식, start_time은 HH:MM 형식, end_time은 HH:MM 형식 또는 '미정'이다.",
)
def personal_create_schedule(
    title: str,
    date: str,
    start_time: str,
    end_time: str = "미정",
    attendees: list[str] | None = None,
) -> str:
    """현재 대화 범위에 사용자의 개인 일정을 생성합니다.

    Args:
        title: 생성할 일정의 제목입니다.
        date: 일정 날짜입니다. YYYY-MM-DD 형식을 사용합니다.
        start_time: 일정 시작 시간입니다. HH:MM 형식을 사용합니다.
        end_time: 일정 종료 시간입니다. HH:MM 형식 또는 "미정"을 사용하며, 기본값은 "미정"입니다.
        attendees: 참석자 이름 목록입니다. None이면 빈 리스트로 저장합니다.

    Returns:
        성공 여부와 생성된 일정 정보를 포함한 JSON 문자열입니다.

        반환 예시:
        {
            "ok": true,
            "tool_name": "personal_create_schedule",
            "created_schedule": {
                "id": "personal_12345",
                "title": "회의",
                "date": "2026-07-11",
                "start_time": "10:00",
                "end_time": "11:00",
                "attendees": [],
                "created_at": "2026-06-30T11:19:52+09:00",
                "session_id": "session_abc"
            }
        }
    """
```

구현 절차:

1. `schedule` dict를 만든다.
2. `schedule`에는 `id`, `title`, `date`, `start_time`, `end_time`, `attendees`, `created_at`, `session_id`를 넣는다.
3. `id`는 `_new_personal_id()`를 사용한다.
4. `created_at`은 `_now_iso()`를 사용한다.
5. `session_id`는 `current_session_scope()`를 사용한다.
6. `attendees`가 `None`이면 `[]`로 저장한다.
7. 생성한 `schedule`을 `PERSONAL_SCHEDULES.append(schedule)`로 추가한다.
8. `_json({"ok": True, "tool_name": "personal_create_schedule", "created_schedule": schedule})`을 반환한다.
9. `structured_request`, `sqlite_save` 등 Week 1 범위 밖 키는 넣지 않는다.

### 4.2 `personal_list_schedules`

기존 함수 signature는 변경하지 않는다.

```python
@tool(
    "personal_list_schedules",
    description="현재 대화에서 생성한 사용자의 개인 일정 목록을 조회한다. date_from과 date_to가 있으면 YYYY-MM-DD 날짜 범위로 필터링한다.",
)
def personal_list_schedules(date_from: str | None = None, date_to: str | None = None) -> str:
    """현재 대화 범위의 사용자 개인 일정을 날짜 범위로 조회합니다.

    Args:
        date_from: 조회 시작 날짜입니다. 값이 있으면 해당 날짜 이상인 일정만 조회합니다.
        date_to: 조회 종료 날짜입니다. 값이 있으면 해당 날짜 이하인 일정만 조회합니다.

    Returns:
        성공 여부와 조회된 일정 목록을 포함한 JSON 문자열입니다.

        반환 예시:
        {
            "ok": true,
            "tool_name": "personal_list_schedules",
            "schedules": [
                {
                    "id": "personal_12345",
                    "title": "회의",
                    "date": "2026-07-11",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "attendees": [],
                    "created_at": "2026-06-30T11:19:52+09:00",
                    "session_id": "session_abc"
                }
            ]
        }
    """
```

구현 절차:

1. `schedules = _current_session_schedules()`로 현재 대화 범위의 일정만 가져온다.
2. `date_from` 값이 있으면 `schedule.get("date", "") >= date_from`인 일정만 남긴다.
3. `date_to` 값이 있으면 `schedule.get("date", "") <= date_to`인 일정만 남긴다.
4. 날짜 비교는 `YYYY-MM-DD` 문자열 비교로 처리한다.
5. 조회 중 `PERSONAL_SCHEDULES` 원본 리스트는 수정하지 않는다.
6. 기존 저장 순서를 유지한다.
7. `_json({"ok": True, "tool_name": "personal_list_schedules", "schedules": schedules})`를 반환한다.

### 4.3 `personal_delete_schedule`

기존 함수 signature는 변경하지 않는다.

```python
@tool(
    "personal_delete_schedule",
    description="개인 일정 ID로 현재 대화의 사용자 일정을 삭제한다. 삭제할 schedule_id가 명시되어야 한다.",
)
def personal_delete_schedule(schedule_id: str) -> str:
    """현재 대화 범위에서 일정 ID에 해당하는 사용자 개인 일정을 삭제합니다.

    Args:
        schedule_id: 삭제할 개인 일정의 ID입니다.

    Returns:
        성공 여부와 실제 삭제 여부를 포함한 JSON 문자열입니다.

        반환 예시:
        {
            "ok": true,
            "tool_name": "personal_delete_schedule",
            "deleted": true
        }
    """
```

구현 절차:

1. 삭제 전 `before_count = len(PERSONAL_SCHEDULES)`를 저장한다.
2. 현재 session은 `target_session_id = current_session_scope()`로 저장한다.
3. 삭제 대상은 `schedule.get("id") == schedule_id`이고 `_schedule_scope(schedule) == target_session_id`인 일정이다.
4. 삭제 대상이 아닌 일정만 `kept_schedules`에 남긴다.
5. 리스트 객체를 유지하기 위해 `PERSONAL_SCHEDULES[:] = kept_schedules`로 갱신한다.
6. 삭제 후 `after_count = len(PERSONAL_SCHEDULES)`를 계산한다.
7. `deleted = after_count < before_count`로 실제 삭제 여부를 판단한다.
8. `_json({"ok": True, "tool_name": "personal_delete_schedule", "deleted": deleted})`를 반환한다.

### 4.4 `week01_prompt_parts`

기존 함수 signature는 변경하지 않는다.

```python
def week01_prompt_parts() -> list[str]:
    """Week 1 일정 agent의 system prompt 조각을 반환합니다.

    Returns:
        Week 1부터 누적되는 system prompt 문자열 목록입니다.
    """
```

구현 절차:

1. 문자열 list를 반환한다.
2. Kanana Schedule Agent는 사용자의 개인 일정 요청을 돕는 한국어 일정 agent라고 설명한다.
3. 현재 날짜는 `current_app_date_iso()` 값으로 포함한다.
4. 일정 생성 요청에는 `personal_create_schedule`을 사용하도록 안내한다.
5. 일정 조회 요청에는 `personal_list_schedules`를 사용하도록 안내한다.
6. 일정 삭제 요청에는 `personal_delete_schedule`을 사용하도록 안내한다.
7. 현재 대화에서 만든 개인 일정만 조회/삭제할 수 있다고 안내한다.
8. 사용자가 상대 날짜를 말하면 현재 날짜 기준으로 해석하도록 안내한다.
9. tool 결과를 바탕으로 짧고 자연스럽게 한국어로 답하도록 안내한다.
10. SQLite, App store 같은 내부 저장 방식 설명은 prompt에 넣지 않는다.

## 5. 검증 시나리오

```text
Scenario: 개인 일정을 생성한다
Given PERSONAL_SCHEDULES is empty
When personal_create_schedule is invoked with title, date, start_time, end_time, attendees
Then payload.ok is true
And payload.tool_name is "personal_create_schedule"
And payload.created_schedule.id starts with "personal_"
And payload.created_schedule.title equals input title
And payload.created_schedule.session_id equals current_session_scope()
And PERSONAL_SCHEDULES length increases by 1
```

```text
Scenario: 참석자 입력이 없으면 빈 리스트로 저장한다
Given attendees is None
When personal_create_schedule is invoked
Then payload.created_schedule.attendees equals []
```

```text
Scenario: 종료 시간이 생략되면 "미정"으로 저장한다
Given end_time is omitted
When personal_create_schedule is invoked
Then payload.created_schedule.end_time equals "미정"
```

```text
Scenario: 일정이 없으면 빈 목록을 반환한다
Given PERSONAL_SCHEDULES is empty
When personal_list_schedules is invoked
Then payload.ok is true
And payload.tool_name is "personal_list_schedules"
And payload.schedules equals []
```

```text
Scenario: 날짜 범위로 개인 일정을 조회한다
Given schedules exist on 2026-07-09, 2026-07-10, 2026-07-15, and 2026-07-16
When personal_list_schedules is invoked with date_from "2026-07-10" and date_to "2026-07-15"
Then payload.schedules contains only schedules whose date is between 2026-07-10 and 2026-07-15
And PERSONAL_SCHEDULES length does not change
```

```text
Scenario: 시작 날짜만으로 개인 일정을 조회한다
Given schedules exist on 2026-07-09, 2026-07-10, 2026-07-15, and 2026-07-16
When personal_list_schedules is invoked with date_from "2026-07-10" and date_to None
Then payload.schedules contains only schedules whose date is greater than or equal to 2026-07-10
```

```text
Scenario: 종료 날짜만으로 개인 일정을 조회한다
Given schedules exist on 2026-07-09, 2026-07-10, 2026-07-15, and 2026-07-16
When personal_list_schedules is invoked with date_from None and date_to "2026-07-15"
Then payload.schedules contains only schedules whose date is less than or equal to 2026-07-15
```

```text
Scenario: 대화 session별로 일정 조회가 분리된다
Given schedule A is created inside conversation_session_scope("conv_a")
And schedule B is created inside conversation_session_scope("conv_b")
When personal_list_schedules is invoked inside conversation_session_scope("conv_a")
Then payload.schedules contains schedule A
And payload.schedules does not contain schedule B
```

```text
Scenario: 현재 session의 일정을 삭제한다
Given a schedule exists in the current session
When personal_delete_schedule is invoked with that schedule id
Then payload.deleted is true
And PERSONAL_SCHEDULES length decreases by 1
And the deleted schedule no longer appears in personal_list_schedules
And the PERSONAL_SCHEDULES list object identity is preserved
```

```text
Scenario: 존재하지 않는 ID 삭제는 실패로 표시한다
Given PERSONAL_SCHEDULES contains existing schedules
When personal_delete_schedule is invoked with "personal_not_found"
Then payload.deleted is false
And PERSONAL_SCHEDULES content does not change
```

```text
Scenario: 다른 session의 일정은 삭제하지 않는다
Given schedule A is created inside conversation_session_scope("conv_a")
When personal_delete_schedule is invoked with schedule A id inside conversation_session_scope("conv_b")
Then payload.deleted is false
And schedule A still exists inside conversation_session_scope("conv_a")
```

```text
Scenario: session_id가 없는 legacy 일정은 기본 scope에서 조회와 삭제가 가능하다
Given a schedule without session_id exists in PERSONAL_SCHEDULES
When personal_list_schedules is invoked in the default scope
Then payload.schedules contains the legacy schedule
When personal_delete_schedule is invoked with the legacy schedule id
Then payload.deleted is true
And PERSONAL_SCHEDULES is empty
```

```text
Scenario: agent가 생성, 조회, 삭제 tool을 순서대로 호출한다
Given kanana_agent is built with Week 1 tools
When user asks "내일 14시에 지아와 체크인 일정 잡아줘"
Then trace contains tool_call for personal_create_schedule
And tool_result contains created_schedule

When user asks "현재 일정 목록 보여줘"
Then trace contains tool_call for personal_list_schedules
And tool_result contains schedules

When user asks "{target_schedule_id} 일정 삭제해줘"
Then trace contains tool_call for personal_delete_schedule
And tool_result.deleted is true
And the final schedules list does not include target_schedule_id
```

## 6. 검증 코드

`test/test_week01_personal_schedule.py`를 생성하고 아래 흐름을 참고해 검증 코드를 생성한다. 모델의 최종 답변 문구가 아니라 trace의 `tool_call`과 `tool_result` payload를 기준으로 검증한다.

```python
"""Week 1 개인 일정 tool 검증 스크립트.

실행:
    python test/test_week01_personal_schedule.py
"""

import json

from fixed.langchain_trace import extract_agent_events, extract_final_text
from fixed.session_scope import conversation_session_scope, current_session_scope
from student_parts.week01_wake_up_nana import (
    PERSONAL_SCHEDULES,
    build_week01_agent,
    personal_create_schedule,
    personal_delete_schedule,
    personal_list_schedules,
)


def create_schedule(**kwargs: object) -> dict:
    return json.loads(personal_create_schedule.invoke(kwargs))


def list_schedules(**kwargs: object) -> dict:
    return json.loads(personal_list_schedules.invoke(kwargs))


def delete_schedule(schedule_id: str) -> dict:
    return json.loads(personal_delete_schedule.invoke({"schedule_id": schedule_id}))


def extract_tool_trace(result: dict) -> list[dict]:
    return extract_agent_events(result)


def has_tool_call(trace: list[dict], tool_name: str) -> bool:
    return any(
        event.get("event") == "tool_call" and event.get("tool_name") == tool_name
        for event in trace
    )


def tool_result_payload(trace: list[dict], tool_name: str) -> dict:
    for event in trace:
        if event.get("event") == "tool_result" and event.get("tool_name") == tool_name:
            return event["content"]
    raise AssertionError(f"{tool_name} tool_result가 없습니다.")


def test_direct_tool_flow() -> None:
    PERSONAL_SCHEDULES.clear()

    created = create_schedule(
        title="회의",
        date="2026-07-11",
        start_time="10:00",
        end_time="11:00",
        attendees=None,
    )
    assert created["ok"] is True
    assert created["tool_name"] == "personal_create_schedule"
    assert created["created_schedule"]["id"].startswith("personal_")
    assert created["created_schedule"]["title"] == "회의"
    assert created["created_schedule"]["attendees"] == []
    assert created["created_schedule"]["session_id"] == current_session_scope()
    assert len(PERSONAL_SCHEDULES) == 1

    PERSONAL_SCHEDULES.clear()
    for day in ("2026-07-09", "2026-07-10", "2026-07-15", "2026-07-16"):
        create_schedule(title=f"일정 {day}", date=day, start_time="09:00", end_time="10:00")

    before_count = len(PERSONAL_SCHEDULES)
    filtered = list_schedules(date_from="2026-07-10", date_to="2026-07-15")["schedules"]
    assert [schedule["date"] for schedule in filtered] == ["2026-07-10", "2026-07-15"]
    assert len(PERSONAL_SCHEDULES) == before_count

    PERSONAL_SCHEDULES.clear()
    with conversation_session_scope("conv_a"):
        a_id = create_schedule(
            title="A 일정",
            date="2026-07-20",
            start_time="09:00",
            end_time="10:00",
        )["created_schedule"]["id"]
    with conversation_session_scope("conv_b"):
        create_schedule(title="B 일정", date="2026-07-20", start_time="09:00", end_time="10:00")
        assert delete_schedule(a_id)["deleted"] is False

    with conversation_session_scope("conv_a"):
        titles = [schedule["title"] for schedule in list_schedules()["schedules"]]
        assert titles == ["A 일정"]
        assert delete_schedule(a_id)["deleted"] is True
        assert all(schedule["id"] != a_id for schedule in list_schedules()["schedules"])
        assert delete_schedule("personal_not_found")["deleted"] is False


def test_direct_tool_edge_cases() -> None:
    PERSONAL_SCHEDULES.clear()

    empty_result = list_schedules(date_from=None, date_to=None)
    assert empty_result["ok"] is True
    assert empty_result["tool_name"] == "personal_list_schedules"
    assert empty_result["schedules"] == []

    default_end_time = create_schedule(title="종료 미정", date="2026-07-08", start_time="13:00")
    assert default_end_time["created_schedule"]["end_time"] == "미정"

    PERSONAL_SCHEDULES.clear()
    for day in ("2026-07-09", "2026-07-10", "2026-07-15", "2026-07-16"):
        create_schedule(title=f"일정 {day}", date=day, start_time="09:00", end_time="10:00")

    from_only = list_schedules(date_from="2026-07-10", date_to=None)["schedules"]
    assert [schedule["date"] for schedule in from_only] == ["2026-07-10", "2026-07-15", "2026-07-16"]

    to_only = list_schedules(date_from=None, date_to="2026-07-15")["schedules"]
    assert [schedule["date"] for schedule in to_only] == ["2026-07-09", "2026-07-10", "2026-07-15"]

    list_identity = id(PERSONAL_SCHEDULES)
    target_id = PERSONAL_SCHEDULES[1]["id"]
    assert delete_schedule(target_id)["deleted"] is True
    assert id(PERSONAL_SCHEDULES) == list_identity
    assert all(schedule["id"] != target_id for schedule in PERSONAL_SCHEDULES)

    PERSONAL_SCHEDULES.clear()
    legacy_schedule = {
        "id": "personal_legacy",
        "title": "legacy",
        "date": "2026-07-21",
        "start_time": "09:00",
        "end_time": "10:00",
        "attendees": [],
        "created_at": "2026-06-30T11:19:52+09:00",
    }
    PERSONAL_SCHEDULES.append(legacy_schedule)
    assert list_schedules()["schedules"] == [legacy_schedule]
    assert delete_schedule("personal_legacy")["deleted"] is True
    assert PERSONAL_SCHEDULES == []


def test_llm_tool_trace_flow() -> None:
    PERSONAL_SCHEDULES.clear()
    kanana_agent = build_week01_agent()

    extension_create_request = "내일 14시에 지아와 체크인 일정 잡아줘"
    extension_create_result = kanana_agent.invoke({"messages": [{"role": "user", "content": extension_create_request}]})
    extension_create_trace = extract_tool_trace(extension_create_result)
    extension_create_payload = tool_result_payload(extension_create_trace, "personal_create_schedule")

    assert has_tool_call(extension_create_trace, "personal_create_schedule")
    assert extension_create_payload["ok"] is True
    assert extension_create_payload["created_schedule"]["id"].startswith("personal_")

    extension_list_request = "현재 일정 목록 보여줘"
    extension_list_result = kanana_agent.invoke({"messages": [{"role": "user", "content": extension_list_request}]})
    extension_list_trace = extract_tool_trace(extension_list_result)
    extension_list_payload = tool_result_payload(extension_list_trace, "personal_list_schedules")
    schedules = extension_list_payload["schedules"]

    assert has_tool_call(extension_list_trace, "personal_list_schedules")
    assert schedules

    target_schedule_id = schedules[-1]["id"]
    extension_delete_request = f"{target_schedule_id} 일정 삭제해줘"
    extension_delete_result = kanana_agent.invoke({"messages": [{"role": "user", "content": extension_delete_request}]})
    extension_delete_trace = extract_tool_trace(extension_delete_result)
    extension_delete_payload = tool_result_payload(extension_delete_trace, "personal_delete_schedule")

    assert has_tool_call(extension_delete_trace, "personal_delete_schedule")
    assert extension_delete_payload["deleted"] is True

    final_list_payload = list_schedules(date_from=None, date_to=None)
    assert all(schedule["id"] != target_schedule_id for schedule in final_list_payload["schedules"])

    print(extract_final_text(extension_create_result))
    print(extension_create_trace)
    print(extract_final_text(extension_list_result))
    print(extension_list_trace)
    print(extract_final_text(extension_delete_result))
    print(extension_delete_trace)
    print(final_list_payload["schedules"])


if __name__ == "__main__":
    test_direct_tool_flow()
    test_direct_tool_edge_cases()
    test_llm_tool_trace_flow()
    print("1주차 개인 일정 검증 통과")
```

문법 확인과 테스트 실행은 다음 명령으로 수행한다.

```bash
python -m compileall -q app.py fixed student_parts mcp_server
python test/test_week01_personal_schedule.py
```

## 7. 완료 기준

- [ ] 세 LangChain tool이 구현되어 있다.
- [ ] `week01_prompt_parts()`가 Week 1 agent의 tool 선택을 돕는 system prompt 조각을 반환한다.
- [ ] tool 반환값은 dict가 아니라 JSON 문자열이다.
- [ ] 반환 JSON이 기대 top-level 키(`created_schedule` / `schedules` / `deleted`)를 유지한다.
- [ ] 생성된 일정은 현재 대화 범위의 `session_id`를 가진다.
- [ ] 조회와 삭제는 현재 대화 범위의 일정만 대상으로 한다.
- [ ] 빈 조회, `end_time` 기본값, 단방향 날짜 필터, 리스트 객체 유지, legacy schedule 조회/삭제 edge case가 검증된다.
- [ ] `python -m compileall -q app.py fixed student_parts mcp_server`가 성공한다.
- [ ] LLM 연동 검증에서 생성, 조회, 삭제 tool의 `tool_call`과 `tool_result`가 trace에 남는다.
- [ ] `python test/test_week01_personal_schedule.py`가 성공한다.
- [ ] 구현 변경은 `student_parts/week01_wake_up_nana.py`에만 있고, 검증 코드는 `test/test_week01_personal_schedule.py`에만 있다.
