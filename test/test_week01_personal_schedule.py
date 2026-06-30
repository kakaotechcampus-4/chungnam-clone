"""Week 1 개인 일정 tool 검증 스크립트.

논리 테스트 케이스: 총 16개
- TC-01: 개인 일정 생성 payload와 저장 상태를 검증한다.
- TC-02: 참석자 입력이 None이면 빈 리스트로 저장되는지 검증한다.
- TC-03: 날짜 범위 조회가 양끝 날짜를 포함하고 원본 목록을 변경하지 않는지 검증한다.
- TC-04: session별 일정 조회가 분리되는지 검증한다.
- TC-05: 다른 session의 일정 삭제가 실패 처리되는지 검증한다.
- TC-06: 현재 session의 일정 삭제가 성공하고 조회 결과에서 사라지는지 검증한다.
- TC-07: 존재하지 않는 ID 삭제가 실패 처리되는지 검증한다.
- TC-08: 일정이 없을 때 빈 목록을 반환하는지 검증한다.
- TC-09: end_time 생략 시 "미정"으로 저장되는지 검증한다.
- TC-10: date_from만 있는 조회가 시작일 이상 일정만 반환하는지 검증한다.
- TC-11: date_to만 있는 조회가 종료일 이하 일정만 반환하는지 검증한다.
- TC-12: 삭제 시 PERSONAL_SCHEDULES 리스트 객체가 유지되는지 검증한다.
- TC-13: session_id가 없는 legacy 일정이 기본 scope에서 조회/삭제되는지 검증한다.
- TC-14: LLM 생성 요청 trace에 personal_create_schedule 호출과 결과가 남는지 검증한다.
- TC-15: LLM 조회 요청 trace에 personal_list_schedules 호출과 결과가 남는지 검증한다.
- TC-16: LLM 삭제 요청 trace에 personal_delete_schedule 호출과 삭제 결과가 남는지 검증한다.

실행:
    python test/test_week01_personal_schedule.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fixed.langchain_trace import extract_agent_events, extract_final_text
from fixed.session_scope import conversation_session_scope, current_session_scope
from student_parts.week01_wake_up_nana import (
    PERSONAL_SCHEDULES,
    build_week01_agent,
    personal_create_schedule,
    personal_delete_schedule,
    personal_list_schedules,
)


def create_schedule(**kwargs: object) -> dict[str, Any]:
    return json.loads(personal_create_schedule.invoke(kwargs))


def list_schedules(**kwargs: object) -> dict[str, Any]:
    return json.loads(personal_list_schedules.invoke(kwargs))


def delete_schedule(schedule_id: str) -> dict[str, Any]:
    return json.loads(personal_delete_schedule.invoke({"schedule_id": schedule_id}))


def extract_tool_trace(result: dict[str, Any]) -> list[dict[str, Any]]:
    return extract_agent_events(result)


def has_tool_call(trace: list[dict[str, Any]], tool_name: str) -> bool:
    return any(
        event.get("event") == "tool_call" and event.get("tool_name") == tool_name
        for event in trace
    )


def tool_result_payload(trace: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    for event in trace:
        if event.get("event") == "tool_result" and event.get("tool_name") == tool_name:
            return event["content"]
    raise AssertionError(f"{tool_name} tool_result가 없습니다. trace={trace}")


def test_direct_tool_flow() -> None:
    PERSONAL_SCHEDULES.clear()

    # TC-01: 개인 일정 생성 payload와 저장 상태를 검증한다.
    # TC-02: 참석자 입력이 None이면 빈 리스트로 저장되는지 검증한다.
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

    # TC-03: 날짜 범위 조회가 양끝 날짜를 포함하고 원본 목록을 변경하지 않는지 검증한다.
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
        # TC-05: 다른 session의 일정 삭제가 실패 처리되는지 검증한다.
        assert delete_schedule(a_id)["deleted"] is False

    with conversation_session_scope("conv_a"):
        # TC-04: session별 일정 조회가 분리되는지 검증한다.
        titles = [schedule["title"] for schedule in list_schedules()["schedules"]]
        assert titles == ["A 일정"]
        # TC-06: 현재 session의 일정 삭제가 성공하고 조회 결과에서 사라지는지 검증한다.
        assert delete_schedule(a_id)["deleted"] is True
        assert all(schedule["id"] != a_id for schedule in list_schedules()["schedules"])
        # TC-07: 존재하지 않는 ID 삭제가 실패 처리되는지 검증한다.
        assert delete_schedule("personal_not_found")["deleted"] is False


def test_direct_tool_edge_cases() -> None:
    PERSONAL_SCHEDULES.clear()

    # TC-08: 일정이 없을 때 빈 목록을 반환하는지 검증한다.
    empty_result = list_schedules(date_from=None, date_to=None)
    assert empty_result["ok"] is True
    assert empty_result["tool_name"] == "personal_list_schedules"
    assert empty_result["schedules"] == []

    # TC-09: end_time 생략 시 "미정"으로 저장되는지 검증한다.
    default_end_time = create_schedule(title="종료 미정", date="2026-07-08", start_time="13:00")
    assert default_end_time["created_schedule"]["end_time"] == "미정"

    PERSONAL_SCHEDULES.clear()
    for day in ("2026-07-09", "2026-07-10", "2026-07-15", "2026-07-16"):
        create_schedule(title=f"일정 {day}", date=day, start_time="09:00", end_time="10:00")

    # TC-10: date_from만 있는 조회가 시작일 이상 일정만 반환하는지 검증한다.
    from_only = list_schedules(date_from="2026-07-10", date_to=None)["schedules"]
    assert [schedule["date"] for schedule in from_only] == ["2026-07-10", "2026-07-15", "2026-07-16"]

    # TC-11: date_to만 있는 조회가 종료일 이하 일정만 반환하는지 검증한다.
    to_only = list_schedules(date_from=None, date_to="2026-07-15")["schedules"]
    assert [schedule["date"] for schedule in to_only] == ["2026-07-09", "2026-07-10", "2026-07-15"]

    # TC-12: 삭제 시 PERSONAL_SCHEDULES 리스트 객체가 유지되는지 검증한다.
    list_identity = id(PERSONAL_SCHEDULES)
    target_id = PERSONAL_SCHEDULES[1]["id"]
    assert delete_schedule(target_id)["deleted"] is True
    assert id(PERSONAL_SCHEDULES) == list_identity
    assert all(schedule["id"] != target_id for schedule in PERSONAL_SCHEDULES)

    # TC-13: session_id가 없는 legacy 일정이 기본 scope에서 조회/삭제되는지 검증한다.
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

    # TC-14: LLM 생성 요청 trace에 personal_create_schedule 호출과 결과가 남는지 검증한다.
    assert has_tool_call(extension_create_trace, "personal_create_schedule")
    assert extension_create_payload["ok"] is True
    assert extension_create_payload["created_schedule"]["id"].startswith("personal_")

    extension_list_request = "현재 일정 목록 보여줘"
    extension_list_result = kanana_agent.invoke({"messages": [{"role": "user", "content": extension_list_request}]})
    extension_list_trace = extract_tool_trace(extension_list_result)
    extension_list_payload = tool_result_payload(extension_list_trace, "personal_list_schedules")
    schedules = extension_list_payload["schedules"]

    # TC-15: LLM 조회 요청 trace에 personal_list_schedules 호출과 결과가 남는지 검증한다.
    assert has_tool_call(extension_list_trace, "personal_list_schedules")
    assert schedules

    target_schedule_id = schedules[-1]["id"]
    extension_delete_request = f"개인 일정 ID {target_schedule_id}를 삭제해줘"
    extension_delete_result = kanana_agent.invoke({"messages": [{"role": "user", "content": extension_delete_request}]})
    extension_delete_trace = extract_tool_trace(extension_delete_result)
    extension_delete_payload = tool_result_payload(extension_delete_trace, "personal_delete_schedule")

    # TC-16: LLM 삭제 요청 trace에 personal_delete_schedule 호출과 삭제 결과가 남는지 검증한다.
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
