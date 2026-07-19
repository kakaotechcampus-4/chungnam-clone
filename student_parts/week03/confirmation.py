from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from fixed.session_scope import current_session_scope
from student_parts.week03.common import (
    _store,
    json_payload,
    tool_result,
    make_validation_error_handler,
)


PENDING_ACTIONS: dict[str, dict[str, Any]] = {}


def set_pending_action(action: dict[str, Any]) -> None:
    conversation_id = current_session_scope()
    PENDING_ACTIONS[conversation_id] = action


def pop_pending_action() -> dict[str, Any] | None:
    conversation_id = current_session_scope()
    return PENDING_ACTIONS.pop(conversation_id, None)


@tool
def confirm_pending_schedule_action(confirm: bool) -> str:
    """확인 대기 중인 일정 수정·삭제를 승인하거나 취소합니다."""

    pending = pop_pending_action()

    if pending is None:
        return json_payload(
            tool_result(
                "confirm_pending_schedule_action",
                ok=False,
                error="pending_action_not_found",
            )
        )

    # 사용자가 "그 일정 아니야 등, 거절 흐름"
    if not confirm:
        return json_payload(
            tool_result(
                "confirm_pending_schedule_action",
                ok=True,
            )
        )

    store = _store()

    if pending["action"] == "update":
        result = store.update_schedule(
            pending["schedule_id"],
            **pending["updates"],
        )

        if result is None:
            return json_payload(
                tool_result(
                    "confirm_pending_schedule_action",
                    ok=False,
                    error="schedule_not_found",
                    updated_schedule=None,
                    shared_sync=None,
                )
            )

        return json_payload(
            tool_result(
                "confirm_pending_schedule_action",
                ok=True,
                updated_schedule=result["schedule"],
                shared_sync=result["shared_sync"],
            )
        )

    elif pending["action"]=="delete":
        deleted = store.delete_schedules_by_filter(
            schedule_ids=pending["schedule_ids"],
            limit=len(pending["schedule_ids"]),
        )

        return json_payload(
            tool_result(
                "confirm_pending_schedule_action",
                ok=True,
                deleted_count=len(deleted),
                filters=pending["filters"],
                deleted=deleted,
            )
        )
    else:
        return json_payload(
            tool_result(
                "confirm_pending_schedule_action",
                ok=False,
                error="invalid_pending_action",
            )
        )


confirm_pending_schedule_action.handle_validation_error = (
    make_validation_error_handler(
        "confirm_pending_schedule_action"
    )
)