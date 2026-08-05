from __future__ import annotations

import json
from typing import Any
from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from fixed.session_scope import current_session_scope
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES
from student_parts.week02_structure_natural_language_requests import StructuredRequest
from student_parts.week05.common import (
    call_mcp_tool_sync,
    json_payload,
    _schedule_scope,
)
from student_parts.week05.schemas import CollectMemberSchedulesInput
from fixed.external_people_store import (
    external_schedule_summary,
    normalize_external_member_names,
    normalize_external_schedule_date_bounds,
    strip_parenthetical_text,
)


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다."""

    stored_schedules = AppSQLiteStore(CONFIG.app_db_path).list_schedules(limit=200)

    stored_ids = {
        str(schedule.get("schedule_id"))
        for schedule in stored_schedules
        if schedule.get("schedule_id")
    }

    session_id = current_session_scope()

    temporary_schedules = [
        schedule
        for schedule in PERSONAL_SCHEDULES
        if _schedule_scope(schedule) == session_id
        and schedule["id"] not in stored_ids
    ]

    return [*stored_schedules, *temporary_schedules]


def _structured_request_from_schedule_row(row: dict[str, Any]) -> StructuredRequest:
    """앱 일정 row를 Week 2 StructuredRequest 기준으로 읽습니다."""

    request = StructuredRequest(
        kind="schedule",
        title=row.get("title"),
        date=row.get("date"),
        start_time=row.get("start_time"),
        end_time=row.get("end_time"),
        members=row.get("attendees") or row.get("members") or [],
        original_text=str(row.get("title") or ""),
    )
    stored_kind = (
        "group_schedule"
        if row.get("request_kind") == "group_schedule"
        else "personal_schedule"
    )

    return request.model_copy(update={"kind": stored_kind})


def _collect_member_schedules(
    *,
    member_names: list[str],
    date_from: str,
    date_to: str,
    personal_schedules: list[dict[str, Any]],
) -> dict[str, Any]:
    """내 일정과 외부 멤버 일정을 같은 row 구조로 합칩니다."""

    normalized_members = normalize_external_member_names(member_names)

    normalized_date_from, normalized_date_to = normalize_external_schedule_date_bounds(
        normalized_members,
        date_from,
        date_to,
    )

    external_result = call_mcp_tool_sync(
        "extract_schedules_from_history",
        {
            "member_names": normalized_members,
            "date_from": normalized_date_from,
            "date_to": normalized_date_to,
        },
    )
    external_rows = json.loads(external_result).get("rows", [])

    personal_rows = []

    for schedule in personal_schedules:
        request = _structured_request_from_schedule_row(schedule)

        if (
            not request.date
            or request.date < normalized_date_from
            or request.date > normalized_date_to
        ):
            continue

        personal_rows.append(
            {
                "member_name": "나",
                "title": request.title,
                "date": request.date,
                "start_time": request.start_time,
                "end_time": request.end_time,
                "notes": _my_schedule_notes(request),
            }
        )

    rows = _dedupe_schedule_rows([*personal_rows, *external_rows])

    return {
            "ok": True,
            "tool_name": "collect_member_schedules",
            "members": [
                "나",
                *[name for name in normalized_members if name != "나"],
            ],
            "rows": rows,
            "schedule_summary": external_schedule_summary(rows),
        }
            
def _my_schedule_notes(request: StructuredRequest) -> str:
    """내 일정이 개인 일정인지 그룹 일정인지 설명합니다."""

    if request.kind != "group_schedule":
        return "Nana 개인 일정"

    members = [
        str(member).strip()
        for member in (request.members or [])
        if str(member).strip()
    ]

    return (
        f"Nana 그룹 일정 · 참석자: {', '.join(members)}"
        if members
        else "Nana 그룹 일정"
    )


def _dedupe_schedule_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """앱 DB와 공유 저장소에서 중복으로 들어온 일정을 제거합니다."""

    deduped: dict[tuple[str, ...], dict[str, Any]] = {}

    for row in rows:
        key = (
            str(row.get("member_name") or "").strip(),
            str(row.get("date") or "").strip(),
            str(row.get("start_time") or "").strip() or "미정",
            strip_parenthetical_text(str(row.get("title") or "")),
        )
        deduped.setdefault(key, row)

    return list(deduped.values())


@tool(args_schema=CollectMemberSchedulesInput)
def collect_member_schedules(
    member_names: list[str], date_from: str, date_to: str
) -> str:
    """내 일정과 다른 사람들의 일정을 MCP SQLite 기록에서 모읍니다."""

    payload = _collect_member_schedules(
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        personal_schedules=_personal_schedules_for_current_scope(),
    )

    return json_payload(payload)
