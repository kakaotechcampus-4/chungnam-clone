from __future__ import annotations

from langchain_core.tools import tool
from student_parts.week03.schemas import SavedScheduleUpdateInput
from student_parts.week03.common import _store, json_payload, tool_result,make_validation_error_handler
from student_parts.week03.confirmation import set_pending_action


@tool(args_schema=SavedScheduleUpdateInput)
def personal_update_saved_schedule(
    schedule_id: str,
    title: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    attendees: list[str] | None = None
) -> str:
    """일정 수정 대상을 확인하고, 사용자 승인 전까지 수정 작업을 대기 상태로 저장합니다."""
    
    updates = {
        key: value
        for key, value in {
            "title": title,
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "attendees": attendees,
        }.items()
        if value is not None
    }
    
    if not updates:
        return json_payload(
            tool_result(
                "personal_update_saved_schedule",
                ok=False,
                error="update_fields_required",
            )
        )

    matches = _store().find_schedules(
        schedule_ids=[schedule_id],
        limit=1,
    )

    if not matches:
        return json_payload(
            tool_result(
                "personal_update_saved_schedule",
                ok=False,
                error="schedule_not_found",
                updated_schedule=None,
                shared_sync=None,
            )
        )

    set_pending_action({
        "action": "update",
        "schedule_id": schedule_id,
        "updates": updates,
    })

    return json_payload(
    tool_result(
        "personal_update_saved_schedule",
        ok=True,
        confirmation_required=True,
        updated_schedule=None,
        shared_sync=None,
        current_schedule=matches[0],
        requested_updates=updates,
    )
)

personal_update_saved_schedule.handle_validation_error = (
    make_validation_error_handler(
        "personal_update_saved_schedule"
    )
)
