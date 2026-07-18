from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from student_parts.week03.schemas import SavedScheduleDeleteInput
from student_parts.week03.common import _store, json_payload, tool_result,make_validation_error_handler


def _delete_saved_schedules(
    *,
    store: AppSQLiteStore,
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
) -> dict[str, Any]:
    """삭제 guard와 DB 호출을 한 곳에 둡니다."""
    
    filters = {
        "schedule_ids": schedule_ids,
        "date": date,
        "title": title,
        "start_time": start_time,
        "time_unspecified": time_unspecified,
        "delete_all": delete_all,
    }

    has_filter = bool(schedule_ids or date or title or start_time or time_unspecified)

    if not delete_all and not has_filter:
        return tool_result(
            "personal_delete_saved_schedules",
            ok=False,
            deleted_count=0,
            filters=filters,
            deleted=[],
            error="삭제 조건이 필요합니다.",
        )

    if delete_all:
        deleted = store.delete_all_schedules()
    else:
        deleted = store.delete_schedules_by_filter(
            schedule_ids=schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
        )

    return tool_result(
        "personal_delete_saved_schedules",
        deleted_count=len(deleted),
        filters=filters,
        deleted=deleted,
    )


def delete_saved_schedules_dict(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
    app_store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    """tool invoke 없이 저장 일정 삭제 로직을 직접 호출합니다."""

    validated = SavedScheduleDeleteInput.model_validate({
        "schedule_ids": schedule_ids,
        "date": date,
        "title": title,
        "start_time": start_time,
        "time_unspecified": time_unspecified,
        "delete_all": delete_all,
    })
        
    store = app_store or _store()

    return _delete_saved_schedules(
        store=store,
        **validated.model_dump()
    )
    

@tool(args_schema=SavedScheduleDeleteInput)
def personal_delete_saved_schedules(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False
) -> str:
    """Nana가 고른 일정 ID나 날짜/제목/시간 필터로 저장 일정을 삭제합니다."""

    result = _delete_saved_schedules(
        store=_store(),
        schedule_ids=schedule_ids,
        date=date,
        title=title,
        start_time=start_time,
        time_unspecified=time_unspecified,
        delete_all=delete_all,
    )

    return json_payload(result)

personal_delete_saved_schedules.handle_validation_error = (
    make_validation_error_handler(
        "personal_delete_saved_schedules"
    )
)