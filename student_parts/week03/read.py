from __future__ import annotations

from langchain_core.tools import tool
from student_parts.week02_structure_natural_language_requests import RequestKind
from student_parts.week03.schemas import (
    SavedRequestListInput,
    SavedRequestGetInput,
    SavedScheduleListInput,
)
from student_parts.week03.common import _store, json_payload, tool_result,make_validation_error_handler


@tool(args_schema=SavedRequestListInput)
def list_saved_requests(
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """SQLite에 저장된 구조화 요청 목록을 조회합니다."""

    rows = _store().list_saved_requests(
        kind=kind,
        date_from=date_from,
        date_to=date_to,
    )

    return json_payload(
        tool_result(
            "list_saved_requests",
            rows=rows,
        )
    )


@tool(args_schema=SavedRequestGetInput)
def get_saved_request(request_id: str) -> str:
    """request_id로 구조화 요청 행 하나를 조회합니다."""

    row = _store().get_saved_request(request_id)

    return json_payload(
        tool_result(
            "get_saved_request",
            row=row,
        )
    )


@tool(args_schema=SavedScheduleListInput)
def personal_list_saved_schedules(
    limit: int = 50,
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None
) -> str:
    """앱 DB에 저장된 일정 목록을 날짜/종류 필터로 반환합니다. Nana가 조회/수정/삭제 후보를 볼 때 사용합니다."""

    schedules = _store().list_schedules(
        limit=limit,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
    )

    filters = {
        "kind": kind,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
    }

    return json_payload(
        tool_result(
            "personal_list_saved_schedules",
            filters=filters,
            schedules=schedules,
        )
    )

list_saved_requests.handle_validation_error = (
    make_validation_error_handler("list_saved_requests")
)

get_saved_request.handle_validation_error = (
    make_validation_error_handler("get_saved_request")
)

personal_list_saved_schedules.handle_validation_error = (
    make_validation_error_handler(
        "personal_list_saved_schedules"
    )
)