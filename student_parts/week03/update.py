from __future__ import annotations

from langchain_core.tools import tool
from student_parts.week03.schemas import SavedScheduleUpdateInput
from student_parts.week03.common import _store, json_payload, tool_result,make_validation_error_handler


@tool(args_schema=SavedScheduleUpdateInput)
def personal_update_saved_schedule(
    schedule_id: str,
    title: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    attendees: list[str] | None = None
) -> str:
    """앱 DB에 저장된 내 일정 원본을 수정하고 공유 일정 복사본을 같은 값으로 갱신합니다."""
    
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

    result = _store().update_schedule(schedule_id, **updates)

    if result is None:
        return json_payload(
            tool_result(
                "personal_update_saved_schedule",
                ok=False,
                updated_schedule=None,
                shared_sync=None,
            )
        )

    return json_payload(
        tool_result(
            "personal_update_saved_schedule",
            updated_schedule=result["schedule"],
            shared_sync=result["shared_sync"],
        )
    )

personal_update_saved_schedule.handle_validation_error = (
    make_validation_error_handler(
        "personal_update_saved_schedule"
    )
)
