from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from student_parts.week03.schemas import SavedScheduleDeleteInput
from student_parts.week03.common import _store, json_payload, tool_result,make_validation_error_handler
from student_parts.week03.confirmation import set_pending_action

# def _delete_saved_schedules(
#     *,
#     store: AppSQLiteStore,
#     schedule_ids: list[str] | None = None,
#     date: str | None = None,
#     title: str | None = None,
#     start_time: str | None = None,
#     time_unspecified: bool = False,
#     delete_all: bool = False,
# ) -> dict[str, Any]:
#     """삭제 guard와 DB 호출을 한 곳에 둡니다."""
    
#     filters = {
#         "schedule_ids": schedule_ids,
#         "date": date,
#         "title": title,
#         "start_time": start_time,
#         "time_unspecified": time_unspecified,
#         "delete_all": delete_all,
#     }

#     has_filter = bool(schedule_ids or date or title or start_time or time_unspecified)

#     if not delete_all and not has_filter:
#         return tool_result(
#             "personal_delete_saved_schedules",
#             ok=False,
#             deleted_count=0,
#             filters=filters,
#             deleted=[],
#             error="삭제 조건이 필요합니다.",
#         )

#     if delete_all:
#         deleted = store.delete_all_schedules()
#     else:
#         deleted = store.delete_schedules_by_filter(
#             schedule_ids=schedule_ids,
#             date=date,
#             title=title,
#             start_time=start_time,
#             time_unspecified=time_unspecified,
#         )

#     return tool_result(
#         "personal_delete_saved_schedules",
#         deleted_count=len(deleted),
#         filters=filters,
#         deleted=deleted,
#     )


# def delete_saved_schedules_dict(
#     schedule_ids: list[str] | None = None,
#     date: str | None = None,
#     title: str | None = None,
#     start_time: str | None = None,
#     time_unspecified: bool = False,
#     delete_all: bool = False,
#     app_store: AppSQLiteStore | None = None,
# ) -> dict[str, Any]:
#     """tool invoke 없이 저장 일정 삭제 로직을 직접 호출합니다."""

#     validated = SavedScheduleDeleteInput.model_validate({
#         "schedule_ids": schedule_ids,
#         "date": date,
#         "title": title,
#         "start_time": start_time,
#         "time_unspecified": time_unspecified,
#         "delete_all": delete_all,
#     })
        
#     store = app_store or _store()

#     return _delete_saved_schedules(
#         store=store,
#         **validated.model_dump()
#     )
    
# 기존에는 _delete_saved_schedules()가 삭제 조건 검사와 실제 DB 삭제를 담당하고,
# delete_saved_schedules_dict()가 입력 검증 후 해당 로직을 직접 호출하는 진입점 역할을 했습니다.
# 현재는 삭제 Tool이 삭제 대상을 pending 상태로 저장하고,
# confirm_pending_schedule_action()이 사용자 확인 후 실제 삭제를 수행하도록
# 책임을 이전했으므로 더 이상 사용하지 않습니다.

@tool(args_schema=SavedScheduleDeleteInput)
def personal_delete_saved_schedules(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False
) -> str:
    """삭제 대상을 조회하고 사용자 승인 전까지 삭제 작업을 대기 상태로 저장합니다."""

    filters = {
        "schedule_ids": schedule_ids,
        "date": date,
        "title": title,
        "start_time": start_time,
        "time_unspecified": time_unspecified,
        "delete_all": delete_all,
    }

    has_filter = bool(
        schedule_ids
        or date
        or title
        or start_time
        or time_unspecified
    )

    if not delete_all and not has_filter:
        return json_payload(
            tool_result(
                "personal_delete_saved_schedules",
                ok=False,
                deleted_count=0,
                filters=filters,
                deleted=[],
                error="삭제 조건이 필요합니다.",
            )
        )

    store = _store()

    if delete_all:
        # SQLite의 LIMIT -1은 행 개수 제한 없이 전체를 조회.
        candidates = store.find_schedules(limit=-1)
    else:
        candidates = store.find_schedules(
            schedule_ids=schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
            limit=100,
        )

    if not candidates:
        return json_payload(
            tool_result(
                "personal_delete_saved_schedules",
                ok=False,
                deleted_count=0,
                filters=filters,
                deleted=[],
                error="schedule_not_found",
            )
        )

    set_pending_action({
        "action": "delete",
        "schedule_ids": [
            schedule["schedule_id"]
            for schedule in candidates
        ],
        "filters": filters,
    })

    return json_payload(
        tool_result(
            "personal_delete_saved_schedules",
            ok=True,
            confirmation_required=True,
            deleted_count=0,
            filters=filters,
            deleted=[],
            candidates=candidates,
        )
    )

personal_delete_saved_schedules.handle_validation_error = (
    make_validation_error_handler(
        "personal_delete_saved_schedules"
    )
)
