"""Week 5 tool 헤드리스 점검 스크립트입니다.

레포에 pytest 의존성이 없고 자동 테스트 하네스도 없으므로, 실행 가능한 assert 스크립트로
관리합니다. 실제 MCP stdio 서버와 앱 SQLite를 그대로 사용하는 통합 점검이라
LLM 키 없이도 tool 계약(반환 JSON 모양, 가드, middleware 동작)을 확인할 수 있습니다.

실행:
    .venv/bin/python -m tests.week05_checks
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from fixed.external_people_store import PERSONAL_SHARED_MEMBER_NAME
from fixed.session_scope import conversation_session_scope
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES
from student_parts.week05_load_kanas_past_conversations import (
    GROUP_SCHEDULE_NOTES,
    PERSONAL_SCHEDULE_NOTES,
    _collect_member_schedules,
    _dedupe_schedule_rows,
    collect_member_schedules,
    create_shared_schedule,
    delete_shared_schedule,
    extract_schedules_from_history,
    list_shared_schedules,
    load_conversation_messages,
    search_previous_conversations,
    skip_personal_list_already_collected,
)

CHECKS: list[str] = []


def check(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok  {name}")


def payload_of(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    """@tool을 실제로 invoke하고 반환 JSON 문자열을 dict로 읽습니다."""

    raw = tool.invoke(args)
    assert isinstance(raw, str), f"{tool.name}은 JSON 문자열을 반환해야 합니다: {type(raw)}"
    return json.loads(raw)


# ---------------------------------------------------------------- 메인과제


def test_search_previous_conversations() -> None:
    payload = payload_of(search_previous_conversations, {"query": "일정", "limit": 5})
    assert payload["ok"] is True
    assert payload["tool_name"] == "search_previous_conversations"
    assert isinstance(payload["rows"], list)
    check("search_previous_conversations: ok/tool_name/rows 유지")

    scoped = payload_of(
        search_previous_conversations,
        {"query": "일정", "member_names": ["철수"], "limit": 5},
    )
    members = {str(row.get("member_name")) for row in scoped["rows"]}
    assert members <= {"철수"}, f"멤버 필터가 걸리지 않았습니다: {members}"
    check("search_previous_conversations: member_names 필터가 해당 멤버만 반환")


def test_load_conversation_messages() -> None:
    found = payload_of(search_previous_conversations, {"query": "일정", "limit": 1})
    assert found["rows"], "외부 대화 시드가 비어 있습니다."
    conversation_id = found["rows"][0]["conversation_id"]

    payload = payload_of(load_conversation_messages, {"conversation_id": conversation_id})
    assert payload["ok"] is True
    messages = payload["rows"]
    assert messages, "대화 메시지가 비어 있습니다."
    for message in messages:
        assert {"sender", "content", "created_at"} <= set(message)
    created = [message["created_at"] for message in messages]
    assert created == sorted(created), "created_at 순서가 보존되지 않았습니다."
    check("load_conversation_messages: sender/content/created_at 순서 보존")


def test_extract_schedules_from_history() -> None:
    payload = payload_of(
        extract_schedules_from_history,
        {"member_names": ["철수"], "date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert payload["ok"] is True
    for row in payload["rows"]:
        assert {"member_name", "title", "date", "start_time", "end_time", "notes"} <= set(row)
    check("extract_schedules_from_history: busy-time row 필드 유지")


def test_list_shared_schedules_without_filter() -> None:
    payload = payload_of(list_shared_schedules, {})
    assert payload["ok"] is True
    assert isinstance(payload["rows"], list)
    assert "schedule_summary" in payload
    check("list_shared_schedules: 필터 없이 호출해도 rows/schedule_summary 반환")


def test_collect_member_schedules_merges_both_sources() -> None:
    """임시 일정이 "나" row로 들어가고 외부 멤버 row와 같은 구조로 합쳐지는지 봅니다."""

    conversation_id = "week05-checks"
    temporary = {
        "id": "week05-checks-tmp-1",
        "title": "점검용 임시 일정",
        "date": "2026-08-05",
        "start_time": "10:00",
        "end_time": "11:00",
        "session_id": conversation_id,
    }
    PERSONAL_SCHEDULES.append(temporary)
    try:
        with conversation_session_scope(conversation_id):
            payload = payload_of(
                collect_member_schedules,
                {"member_names": ["나", "철수"], "date_from": "2026-08-01", "date_to": "2026-08-31"},
            )
    finally:
        PERSONAL_SCHEDULES.remove(temporary)

    assert payload["ok"] is True
    assert "나" not in payload["external_member_names"], "외부 조회에 '나'가 그대로 넘어가면 중복 집계됩니다."
    check("collect_member_schedules: 외부 조회 대상에서 '나' 제외")

    mine = [row for row in payload["rows"] if row["member_name"] == PERSONAL_SHARED_MEMBER_NAME]
    temporary_rows = [row for row in mine if row["title"] == temporary["title"]]
    assert temporary_rows, "현재 대화의 임시 일정이 빠졌습니다."
    # 임시 일정 row에는 request_kind가 없으므로 항상 개인 일정으로 읽습니다.
    assert all(row["notes"] == PERSONAL_SCHEDULE_NOTES for row in temporary_rows)
    # 앱 DB에서 온 다른 "나" row는 개인/그룹 둘 다 가능하지만 반드시 둘 중 하나로 표시됩니다.
    assert all(row["notes"].startswith((PERSONAL_SCHEDULE_NOTES, GROUP_SCHEDULE_NOTES)) for row in mine)
    check("collect_member_schedules: 현재 대화 임시 일정이 '나' row로 포함")

    assert payload["member_names"][0] == PERSONAL_SHARED_MEMBER_NAME
    assert payload["member_names"].count(PERSONAL_SHARED_MEMBER_NAME) == 1, "'나'가 두 번 들어가면 안 됩니다."
    check("collect_member_schedules: members 목록에 '나'는 맨 앞 한 번만")

    for row in payload["rows"]:
        assert {"member_name", "title", "date", "start_time", "end_time", "notes"} <= set(row)
    keys = [(row["date"], row["start_time"], row["member_name"]) for row in payload["rows"]]
    assert keys == sorted(keys), "rows가 date/start_time/member_name 순으로 정렬되지 않았습니다."
    assert payload["schedule_summary"] is not None
    check("collect_member_schedules: 두 출처 row 구조 동일 + 정렬 + schedule_summary")


def test_collect_member_schedules_filters_out_of_range_dates() -> None:
    collected = _collect_member_schedules(
        member_names=["철수"],
        date_from="2026-08-01",
        date_to="2026-08-31",
        personal_schedules=[
            {"title": "범위 안", "date": "2026-08-10", "start_time": "09:00", "end_time": "10:00"},
            {"title": "범위 밖", "date": "2026-09-10", "start_time": "09:00", "end_time": "10:00"},
            {"title": "날짜 없음", "date": None, "start_time": "09:00", "end_time": "10:00"},
        ],
    )
    titles = {row["title"] for row in collected["rows"] if row["member_name"] == PERSONAL_SHARED_MEMBER_NAME}
    assert titles == {"범위 안"}, titles
    assert collected["personal_row_count"] == 1
    check("_collect_member_schedules: 날짜 범위 밖/날짜 없는 내 일정 제외")


def test_other_members_only_when_i_am_not_requested() -> None:
    """member_names에 '나'가 없어도 내 일정은 항상 포함됩니다(도구 계약)."""

    collected = _collect_member_schedules(
        member_names=["철수"],
        date_from="2026-08-01",
        date_to="2026-08-31",
        personal_schedules=[
            {"title": "내 일정", "date": "2026-08-11", "start_time": "13:00", "end_time": "14:00"},
        ],
    )
    assert collected["personal_row_count"] == 1
    check("_collect_member_schedules: member_names에 '나'가 없어도 내 일정 포함")


def test_group_schedule_is_my_busy_time() -> None:
    """이미 잡아둔 그룹 회의도 내 바쁜 시간으로 잡혀야 합니다(공지_코드업데이트.md 버그 ①)."""

    collected = _collect_member_schedules(
        member_names=["민준"],
        date_from="2026-07-01",
        date_to="2026-07-31",
        personal_schedules=[
            {
                "title": "하린과 사전 미팅",
                "date": "2026-07-14",
                "start_time": "15:00",
                "end_time": "16:00",
                "attendees": ["하린"],
                "request_kind": "group_schedule",
            },
            {"title": "개인 집중 작업", "date": "2026-07-15", "start_time": "09:00", "end_time": "10:00"},
        ],
    )
    mine = {row["title"]: row for row in collected["rows"] if row["member_name"] == PERSONAL_SHARED_MEMBER_NAME}
    assert "하린과 사전 미팅" in mine, "그룹 일정이 빠지면 그 시간이 '빈 시간'으로 추천됩니다."
    assert mine["하린과 사전 미팅"]["notes"] == f"{GROUP_SCHEDULE_NOTES} · 참석자: 하린"
    assert mine["개인 집중 작업"]["notes"] == PERSONAL_SCHEDULE_NOTES
    check("_collect_member_schedules: 그룹 일정도 내 busy-time에 포함되고 notes로 구분")


def test_dedupe_schedule_rows_matches_differently_trimmed_rows() -> None:
    """앱 DB row와 공유 저장소 복사본은 다듬는 방식이 달라도 같은 일정으로 봅니다."""

    app_row = {
        "member_name": "나",
        "title": "팀 회의 (온라인)",
        "date": "2026-07-14",
        "start_time": "15:00",
        "end_time": "18:00",
        "notes": PERSONAL_SCHEDULE_NOTES,
    }
    shared_copy = {
        "member_name": "나",
        "title": "팀 회의",
        "date": "2026-07-14",
        "start_time": "15:00",
        "end_time": "미정",
        "notes": "앱 개인 일정 자동 동기화",
    }
    other = {**app_row, "member_name": "철수"}

    deduped = _dedupe_schedule_rows([app_row, shared_copy, other])
    assert len(deduped) == 2, deduped
    assert deduped[0]["notes"] == PERSONAL_SCHEDULE_NOTES, "먼저 온 앱 DB row의 notes가 남아야 합니다."
    assert deduped[1]["member_name"] == "철수", "사람이 다르면 같은 일정이라도 각자 바쁜 시간입니다."
    check("_dedupe_schedule_rows: 소괄호·end_time 차이를 넘어 중복 제거, 앞선 row 유지")


# ---------------------------------------------------------------- 추가 과제(심화)


def test_shared_schedule_create_list_delete_round_trip() -> None:
    created = payload_of(
        create_shared_schedule,
        {
            "member_name": "철수",
            "title": "점검용 공유 일정",
            "date": "2026-08-05",
            "start_time": "15:00",
            "end_time": "16:00",
            "notes": "week05_checks",
            "source_conversation_id": "week05-checks-req-1",
        },
    )
    assert created["ok"] is True
    row = created["shared_schedule"]
    schedule_id = row["schedule_id"]
    assert row["sync_status"] == "created"
    assert row["source_conversation_id"] == "week05-checks-req-1"
    check("create_shared_schedule: schedule_id/source_conversation_id 보존")

    try:
        listed = payload_of(
            list_shared_schedules,
            {"member_names": ["철수"], "date_from": "2026-08-05", "date_to": "2026-08-05"},
        )
        assert schedule_id in {r["schedule_id"] for r in listed["rows"]}
        check("create_shared_schedule: 등록한 row가 list_shared_schedules에 노출")

        updated = payload_of(
            create_shared_schedule,
            {
                "member_name": "철수",
                "title": "점검용 공유 일정(시간 변경)",
                "date": "2026-08-05",
                "start_time": "17:00",
                "end_time": "18:00",
                "schedule_id": schedule_id,
            },
        )
        assert updated["shared_schedule"]["sync_status"] == "updated"
        assert updated["shared_schedule"]["schedule_id"] == schedule_id
        check("create_shared_schedule: 같은 schedule_id 재호출은 갱신(updated)")
    finally:
        deleted = payload_of(delete_shared_schedule, {"schedule_id": schedule_id})

    assert deleted["ok"] is True
    assert deleted["deleted_count"] == 1
    remaining = payload_of(
        list_shared_schedules,
        {"member_names": ["철수"], "date_from": "2026-08-05", "date_to": "2026-08-05"},
    )
    assert schedule_id not in {r["schedule_id"] for r in remaining["rows"]}
    check("delete_shared_schedule: 삭제 후 조회에서 사라짐")


def test_delete_shared_schedule_guard() -> None:
    payload = payload_of(delete_shared_schedule, {})
    assert payload["ok"] is False, "조건 없는 삭제는 ok=False로 끊어야 합니다."
    assert payload["deleted_count"] == 0
    assert payload["error"]
    check("delete_shared_schedule: 조건 없는 호출을 ok=False로 차단")


def test_delete_shared_schedule_by_source_conversation_id() -> None:
    created = payload_of(
        create_shared_schedule,
        {
            "member_name": "영희",
            "title": "source 기준 삭제 점검",
            "date": "2026-08-06",
            "start_time": "09:00",
            "end_time": "10:00",
            "source_conversation_id": "week05-checks-req-2",
        },
    )
    deleted = payload_of(
        delete_shared_schedule,
        {"source_conversation_id": "week05-checks-req-2"},
    )
    assert deleted["deleted_count"] >= 1
    assert created["shared_schedule"]["schedule_id"] in {r["schedule_id"] for r in deleted["deleted"]}
    check("delete_shared_schedule: source_conversation_id로도 삭제 가능")


# ------------------------------------------- 중복 호출 차단 middleware(멘토 리뷰 반영)


def collect_tool_message(rows: list[dict[str, Any]]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {"ok": True, "tool_name": "collect_member_schedules", "rows": rows},
            ensure_ascii=False,
        ),
        tool_call_id="call-collect",
        name="collect_member_schedules",
    )


ROWS = [
    {"member_name": "나", "title": "내 일정", "date": "2026-08-05", "start_time": "10:00"},
    {"member_name": "철수", "title": "남의 일정", "date": "2026-08-05", "start_time": "14:00"},
]


def run_middleware(tool_name: str, messages: list[Any], tool_call_id: str = "call-1") -> tuple[Any, list[str]]:
    """middleware를 직접 호출하고, 원래 tool이 실행됐는지도 함께 돌려줍니다."""

    handled: list[str] = []

    def handler(request: ToolCallRequest) -> ToolMessage:
        handled.append(str(request.tool_call["name"]))
        return ToolMessage(content="{}", tool_call_id=tool_call_id, name=tool_name)

    request = ToolCallRequest(
        tool_call={"name": tool_name, "args": {}, "id": tool_call_id},
        tool=None,
        state={"messages": messages},
        runtime=None,
    )
    return skip_personal_list_already_collected.wrap_tool_call(request, handler), handled


def test_middleware_blocks_duplicate_personal_list_in_same_turn() -> None:
    for tool_name in ("personal_list_schedules", "personal_list_saved_schedules"):
        messages = [
            HumanMessage(content="다음 주 나랑 철수 일정 정리해 줘"),
            AIMessage(content=""),
            collect_tool_message(ROWS),
        ]
        result, handled = run_middleware(tool_name, messages)
        payload = json.loads(result.content)
        assert handled == [], f"{tool_name}이 실제로 실행됐습니다(차단 실패)."
        assert payload["skipped"] is True
        assert payload["tool_name"] == tool_name
        assert payload["row_count"] == 1
        assert payload["already_collected_rows"][0]["member_name"] == PERSONAL_SHARED_MEMBER_NAME
        check(f"middleware: 같은 턴의 중복 {tool_name} 차단 + '나' row만 회신")


def test_middleware_passes_through_without_collect_call() -> None:
    messages = [HumanMessage(content="내 일정 보여줘"), AIMessage(content="")]
    result, handled = run_middleware("personal_list_saved_schedules", messages)
    assert handled == ["personal_list_saved_schedules"], "collect 호출이 없으면 그대로 실행돼야 합니다."
    assert json.loads(result.content) == {}
    check("middleware: collect 호출이 없는 턴은 정상 통과")


def test_middleware_respects_turn_boundary() -> None:
    """이전 턴의 collect 결과 때문에 이번 턴 '내 일정 보여줘'가 막히면 안 됩니다."""

    messages = [
        HumanMessage(content="나랑 철수 일정 정리해 줘"),
        AIMessage(content=""),
        collect_tool_message(ROWS),
        AIMessage(content="정리했습니다."),
        HumanMessage(content="그럼 내 일정만 다시 보여줘"),
        AIMessage(content=""),
    ]
    _, handled = run_middleware("personal_list_saved_schedules", messages)
    assert handled == ["personal_list_saved_schedules"], "턴 경계를 넘어 차단하면 정상 조회가 막힙니다."
    check("middleware: 이전 턴의 collect 결과는 이번 턴을 막지 않음")


def test_middleware_blocks_parallel_tool_call_in_same_response() -> None:
    """한 응답에서 collect와 함께 병렬 호출된 경우도 막습니다.

    이때는 형제 tool의 ToolMessage가 아직 state에 없으므로 호출을 지시한 AI message를 봅니다.
    """

    messages = [
        HumanMessage(content="나랑 철수 다음 주 일정 정리해 줘"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "collect_member_schedules", "args": {}, "id": "call-collect"},
                {"name": "personal_list_saved_schedules", "args": {}, "id": "call-dup"},
            ],
        ),
    ]
    result, handled = run_middleware("personal_list_saved_schedules", messages, tool_call_id="call-dup")
    payload = json.loads(result.content)
    assert handled == [], "병렬 호출된 중복 조회가 실행됐습니다."
    assert payload["skipped"] is True
    assert "collect_member_schedules" in payload["skipped_reason"]
    check("middleware: 같은 응답의 병렬 중복 호출도 차단")


def test_middleware_allows_solo_parallel_call() -> None:
    """collect 없이 병렬 호출된 내 일정 조회는 정상 실행돼야 합니다."""

    messages = [
        HumanMessage(content="내 일정과 참고자료 같이 보여줘"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search_personal_references", "args": {}, "id": "call-ref"},
                {"name": "personal_list_saved_schedules", "args": {}, "id": "call-mine"},
            ],
        ),
    ]
    _, handled = run_middleware("personal_list_saved_schedules", messages, tool_call_id="call-mine")
    assert handled == ["personal_list_saved_schedules"]
    check("middleware: collect가 없는 병렬 호출은 정상 통과")


def test_middleware_ignores_unrelated_tools() -> None:
    messages = [HumanMessage(content="철수 일정"), AIMessage(content=""), collect_tool_message(ROWS)]
    _, handled = run_middleware("search_previous_conversations", messages)
    assert handled == ["search_previous_conversations"]
    check("middleware: 대상 외 tool은 개입하지 않음")


def main() -> None:
    tests = [
        test_search_previous_conversations,
        test_load_conversation_messages,
        test_extract_schedules_from_history,
        test_list_shared_schedules_without_filter,
        test_collect_member_schedules_merges_both_sources,
        test_collect_member_schedules_filters_out_of_range_dates,
        test_other_members_only_when_i_am_not_requested,
        test_group_schedule_is_my_busy_time,
        test_dedupe_schedule_rows_matches_differently_trimmed_rows,
        test_shared_schedule_create_list_delete_round_trip,
        test_delete_shared_schedule_guard,
        test_delete_shared_schedule_by_source_conversation_id,
        test_middleware_blocks_duplicate_personal_list_in_same_turn,
        test_middleware_passes_through_without_collect_call,
        test_middleware_respects_turn_boundary,
        test_middleware_blocks_parallel_tool_call_in_same_response,
        test_middleware_allows_solo_parallel_call,
        test_middleware_ignores_unrelated_tools,
    ]
    for test in tests:
        print(f"\n[{test.__name__}]")
        test()
    print(f"\n총 {len(CHECKS)}개 점검 통과")


if __name__ == "__main__":
    main()
