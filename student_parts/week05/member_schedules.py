from __future__ import annotations
from typing import Any
from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from fixed.external_people_store import (
    external_schedule_summary,
    normalize_external_member_names,
    normalize_external_schedule_date_bounds,
)
from fixed.runtime_clock import current_app_date_iso
from fixed.session_scope import DEFAULT_SESSION_SCOPE, current_session_scope
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES
from student_parts.week02_structure_natural_language_requests import StructuredRequest
from student_parts.week05.common import call_mcp_tool_sync, json_payload
from student_parts.week05.schemas import CollectMemberSchedulesInput
import json

def _schedule_scope(schedule: dict[str, Any]) -> str:
    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다."""

    # TODO 메인: SQLite 저장 일정과 현재 대화의 임시 일정을 합쳐 반환하세요.
    today = current_app_date_iso()

    stored_schedules = AppSQLiteStore(
        CONFIG.app_db_path
    ).list_schedules(
        limit=200,
    )

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
        and str(schedule.get("id") or schedule.get("schedule_id") or "")
        not in stored_ids
    ]

    return [*stored_schedules, *temporary_schedules]


def _structured_request_from_schedule_row(row: dict[str, Any]) -> StructuredRequest:
    """앱 일정 row를 Week 2 StructuredRequest 기준으로 읽습니다."""

    return StructuredRequest(
        kind="schedule",
        # 전 week02 에서 schedule을 입력받아, member 유무에 따라 personal,group으로 재가공을 하였기때문에,
        # Literal에 Personal_schedule이 빠지고, schedule이 들어가있습니다, 
        # 따라서 기존의 personal_schedule을 kind로 지정하면, Literal 제약에 위반되서 이를 schedule로 바꿨습니다
        title=row.get("title"),
        date=row.get("date"),
        start_time=row.get("start_time"),
        end_time=row.get("end_time"),
        members=row.get("attendees") or row.get("members") or [],
        original_text=str(row.get("title") or ""),
    )


def _collect_member_schedules(
    *,
    member_names: list[str],
    date_from: str,
    date_to: str,
    personal_schedules: list[dict[str, Any]],
) -> dict[str, Any]:
    """내 일정과 외부 멤버 일정을 같은 row 구조로 합칩니다."""

    # TODO 메인: 내 SQLite/임시 일정과 외부 MCP 일정 rows를 같은 구조로 합치세요.
    normalized_members = normalize_external_member_names(member_names)

    normalized_date_from, normalized_date_to = (
        normalize_external_schedule_date_bounds(
            normalized_members,
            date_from,
            date_to,
        )
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
                "notes": None,
            }
        )

    rows = [*personal_rows, *external_rows]

    return {
        "ok": True,
        "tool_name": "collect_member_schedules",
        "rows": rows,
        "schedule_summary": external_schedule_summary(rows),
    }


@tool(args_schema=CollectMemberSchedulesInput)
def collect_member_schedules(member_names: list[str], date_from: str, date_to: str) -> str:
    """내 일정과 다른 사람들의 일정을 MCP SQLite 기록에서 모읍니다."""

    # TODO 메인: 내 일정과 외부 멤버 busy-time rows를 모아 JSON 문자열로 반환하세요.
    payload = _collect_member_schedules(
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        personal_schedules=_personal_schedules_for_current_scope(),
    )

    return json_payload(payload)
