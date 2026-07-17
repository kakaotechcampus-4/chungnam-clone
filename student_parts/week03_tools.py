from __future__ import annotations

"""Week 3에서 LangChain agent에 공개하는 SQLite tool 모음입니다."""

import json
from typing import Any

from langchain_core.tools import tool

from student_parts.week01_wake_up_nana import (
    personal_create_schedule as week01_personal_create_schedule,
    week01_tools,
)
from student_parts.week02_structure_natural_language_requests import RequestKind, extract_schedule_request
from student_parts.week03_helpers import (
    _delete_saved_schedules,
    _store,
    _tool_name,
    json_payload,
    save_structured_request_payload,
    structured_request_from_week01_schedule,
    tool_result,
)
from student_parts.week03_models import (
    SaveStructuredRequestInput,
    SavedRequestGetInput,
    SavedRequestListInput,
    SavedScheduleDeleteInput,
    SavedScheduleListInput,
    SavedScheduleUpdateInput,
)

################################
## Week 3 structured request tools
################################

@tool(args_schema=SaveStructuredRequestInput)
def save_structured_request(
    kind: RequestKind = "unknown",
    title: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    members: list[str] | None = None,
    priority: str | None = None,
    reason: str | None = None,
    original_text: str = "",
    source_schedule_id: str | None = None,
) -> str:
    """Week 2 structured_request 필드를 검증한 뒤 SQLite에 저장합니다."""

    fields = {
        "kind": kind,
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "members": members,
        "priority": priority,
        "reason": reason,
        "original_text": original_text,
        "source_schedule_id": source_schedule_id,
    }
    payload = {key: value for key, value in fields.items() if value is not None}
    saved = _store().save_structured_request(payload)
    return json_payload(tool_result(_tool_name(save_structured_request), **saved))


@tool(args_schema=SavedRequestListInput)
def list_saved_requests(
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """SQLite에 저장된 구조화 요청 목록을 조회합니다."""

    rows = _store().list_saved_requests(kind=kind, date_from=date_from, date_to=date_to)
    return json_payload(tool_result(_tool_name(list_saved_requests), rows=rows))


@tool(args_schema=SavedRequestGetInput)
def get_saved_request(request_id: str) -> str:
    """request_id로 구조화 요청 행 하나를 조회합니다."""

    row = _store().get_saved_request(request_id)
    return json_payload(tool_result(_tool_name(get_saved_request), row=row))


###################################
## Week 3 personal schedule tools
###################################

@tool("personal_create_schedule")
def personal_create_schedule(
    title: str,
    date: str,
    start_time: str,
    end_time: str = "미정",
    attendees: list[str] | None = None,
) -> str:
    """사용자의 개인 일정을 생성하고 Week 3+ 앱 SQLite DB에도 저장합니다."""

    # *이 함수는 사실 tool 목록에서 제외해야 하고, 구현 필요가 없는 함수로 판단됨.*
    #
    # 과제 3에서 핵심 흐름은 `extract_schedule_request -> save_structured_request
    # save_structured_request에서 _store().save_structured_request(payload)로 db에 저장.`
    # 이때, 다음 내용처럼 schedule의 경우, request와 schedules 모두 저장됨.
    #
    # app_store.save_structured_request(payload)에서,
    # `payload["kind"]`에 따라 저장 위치가 달라집니다.
        # - personal_schedule/group_schedule: `structured_requests`와 `schedules`에 저장
        # - todo: `structured_requests`와 `todos`에 저장
        # - reminder: `structured_requests`와 `reminders`에 저장
    #
    # => 이 함수(personal_create_schedule)에서처럼 schedule을 생성하고, 그 결과로 부족한(?) structured_request를 만들어서
    # save_structured_request로 저장하는 흐름을 가질 필요가 없음.
    #
    # in memory schedule을 유지/일치시킬 목적이라고 하면, 스케줄 제거할 때,
    # db에 저장된 것과 in memory 둘 다 일치시켜야 할 것 같은데,
    # 하나의 tool에서 두 가지를 다 처리하는 것도 아니고..
    # llm이
    # 1. in memory 수정 : personal_list_schedules -> personal_delete_schedule
    # 2. db 수정 : personal_list_saved_schedules -> personal_delete_saved_schedules
    # 두 가지 모두 tool 흐름을 알아서 호출하길 기대해야 하는건 아닌 것 같음.
    #

    # TODO: Week 1 임시 일정 tool을 호출한 뒤 결과를 StructuredRequest로 바꿔 SQLite에도 저장하세요.
    # TODO: created 결과에 structured_request와 sqlite_save를 합쳐 JSON 문자열로 반환하세요.
    created = json.loads(
        week01_personal_create_schedule.invoke(
            {
                "title": title,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "attendees": attendees,
            }
        )
    )
    structured_request = structured_request_from_week01_schedule(created["created_schedule"])
    sqlite_save = save_structured_request_payload(structured_request)
    return json_payload(
        tool_result(
            _tool_name(personal_create_schedule),
            ok=created["ok"],
            created_schedule=created["created_schedule"],
            structured_request=structured_request.model_dump(),
            sqlite_save=sqlite_save,
        )
    )

@tool(args_schema=SavedScheduleListInput)
def personal_list_saved_schedules(
    limit: int = 50,
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """앱 DB에 저장된 일정 목록을 날짜/종류 필터로 반환합니다. 사용자가 조회/수정/삭제 후보를 볼 때 사용합니다."""

    effective_kind = kind or "personal_schedule"
    filters = {
        "limit": limit,
        "kind": effective_kind,
        "date_from": date_from,
        "date_to": date_to,
    }
    schedules = _store().list_schedules(
        limit=limit,
        kind=effective_kind,
        date_from=date_from,
        date_to=date_to,
    )

    # schedules table은
    # CREATE TABLE IF NOT EXISTS schedules (
    #                 schedule_id TEXT PRIMARY KEY,
    #                 request_id TEXT,
    #                 owner TEXT NOT NULL DEFAULT 'me',
    #                 title TEXT NOT NULL,
    #                 date TEXT,
    #                 start_time TEXT,
    #                 end_time TEXT,
    #                 attendees_json TEXT NOT NULL DEFAULT '[]',
    #                 source TEXT NOT NULL DEFAULT 'structured_output',
    #                 created_at TEXT NOT NULL
    #             );
    # 인데,
    # list_schedules()에서 owner에 대한 필터링 없음..
    # 현재 app_store에 있는 db 함수들은 owner를 항상 'me'로만 이용함..
    # 지금 과제에선 지금처럼 list_schedules() 해도 지금은 상관 없겠지만.
    # 추후에 owner가 'me'가 아닌 경우가 분명히 있을 것 같음.(그렇지 않으면 컬럼이 왜 있는지 모르겠음)
    # => 지금 과제에서 list_schedules()를 쓰는게 아니라 query를 따로 작성해서만하는게 사실은 맞을 것 같음..
    #
    # personal이라는게 'me'를 말하는게 맞겠지..?

    return json_payload(
        tool_result(
            _tool_name(personal_list_saved_schedules),
            filters=filters,
            schedules=schedules,
        )
    )


@tool(args_schema=SavedScheduleUpdateInput)
def personal_update_saved_schedule(
    schedule_id: str,
    title: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    attendees: list[str] | None = None,
) -> str:
    """앱 DB에 저장된 내 일정 원본을 수정하고 공유 일정 복사본을 같은 값으로 갱신합니다."""

    # 이것도 문제가 있지 않나..?
    # personal_create_schedule에서
    # 'in-memory 저장(PERSONAL_SCHEDULES)', 'StructuredRequest 변환 -> db 저장(request, schedule)' 둘 다 하고
    #
    # week3 주요 흐름인 extract_schedule_request -> save_structured_request 에서는
    # request, schedule 둘 다 db에만 저장한다.
    #
    # personal_update_saved_schedule에서는
    # db 수정 todo만 과제에서 요구한다..?
    #
    # personal_create_schedule은 왜 유지하는거지..?

    # TODO: None이 아닌 수정 필드를 AppSQLiteStore.update_schedule(...)에 전달하세요
    # TODO: ID가 없으면 ok=False, 있으면 updated_schedule/shared_sync를 담아 JSON 문자열로 반환하세요.
    fields = {
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "attendees": attendees,
    }
    changes = {key: value for key, value in fields.items() if value is not None}
    if not changes:
        return json_payload(
            tool_result(
                _tool_name(personal_update_saved_schedule),
                ok=False,
                updated_schedule=None,
                shared_sync=None,
                error="수정할 제목, 날짜, 시간 또는 참석자 정보가 필요합니다.",
            )
        )

    updated = _store().update_schedule(schedule_id=schedule_id, **changes)
    if updated is None:
        return json_payload(
            tool_result(
                _tool_name(personal_update_saved_schedule),
                ok=False,
                updated_schedule=None,
                shared_sync=None,
                error="수정할 일정을 찾지 못했습니다.",
            )
        )
    return json_payload(
        tool_result(
            _tool_name(personal_update_saved_schedule),
            updated_schedule=updated["schedule"],
            shared_sync=updated["shared_sync"],
        )
    )


@tool(args_schema=SavedScheduleDeleteInput)
def personal_delete_saved_schedules(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
) -> str:
    """사용자가 고른 일정 ID나 날짜/제목/시간 필터로 저장 일정을 삭제합니다."""

    # TODO: _delete_saved_schedules(...)에 삭제 조건을 전달하고 결과를 JSON 문자열로 반환하세요.
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


def week03_tools() -> list[Any]:
    """Week 1 도구, Week 2 구조화 helper, SQLite 저장/조회/삭제 도구를 조립합니다."""

    base_tools = [
        personal_create_schedule if _tool_name(item) == "personal_create_schedule" else item for item in week01_tools()
    ]
    return [
        *base_tools,
        extract_schedule_request,
        save_structured_request,
        list_saved_requests,
        get_saved_request,
        personal_list_saved_schedules,
        personal_update_saved_schedule,
        personal_delete_saved_schedules,
    ]
