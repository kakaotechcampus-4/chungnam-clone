from __future__ import annotations

"""Week 3 입력 변환과 SQLite 호출을 담당하는 비-tool helper입니다."""

import json
from typing import Any

from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from student_parts.week02_structure_natural_language_requests import StructuredRequest, extract_structured_request
from student_parts.week03_models import SaveStructuredRequestInput


def _store() -> AppSQLiteStore:
    return AppSQLiteStore(CONFIG.app_db_path)


def _tool_name(item: Any) -> str:
    return getattr(item, "name", getattr(item, "__name__", str(item)))


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


def tool_result(tool_name: str, *, ok: bool = True, **payload: Any) -> dict[str, Any]:
    """Week 3 tool들이 공통으로 쓰는 JSON payload 껍데기를 만듭니다."""

    return {"ok": ok, "tool_name": tool_name, **payload}


def _save_input_from(
    value: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str,
) -> SaveStructuredRequestInput:
    """저장 입력을 SaveStructuredRequestInput 하나로 모읍니다."""

    # TODO: dict/JSON/자연어/StructuredRequest 입력을 SaveStructuredRequestInput으로 검증하고 정규화하세요.
    if isinstance(value, SaveStructuredRequestInput):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = extract_structured_request(value)
    return SaveStructuredRequestInput.model_validate(value)


def save_structured_request_payload(
    request: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str,
    *,
    store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    """검증된 structured request를 앱 DB에 저장합니다."""

    # TODO: 입력을 검증한 뒤 AppSQLiteStore.save_structured_request(...)로 저장하고 tool 결과를 반환하세요.
    save_input = _save_input_from(request)
    app_store = store if store is not None else _store()
    saved = app_store.save_structured_request(save_input.model_dump(exclude_none=True))
    return tool_result("save_structured_request", **saved)


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

    # TODO: 삭제 조건이 없으면 거부하고, delete_all 또는 명시 필터에 맞는 store 메서드를 호출하세요.
    # TODO: deleted_count, filters, deleted가 포함된 tool 결과 dict를 반환하세요.
    filters = {
        "schedule_ids": schedule_ids,
        "date": date,
        "title": title,
        "start_time": start_time,
        "time_unspecified": time_unspecified,
        "delete_all": delete_all,
    }
    normalized_schedule_ids = schedule_ids or None
    has_filter = bool(normalized_schedule_ids) or bool(date or title or start_time or time_unspecified)
    if not delete_all and not has_filter:
        return tool_result(
            "personal_delete_saved_schedules",
            ok=False,
            deleted_count=0,
            filters=filters,
            deleted=[],
            error=(
                "삭제할 일정의 ID나 날짜, 제목, 시간 조건을 지정하거나, "
                "전체 삭제라면 delete_all=true로 설정해야 합니다."
            ),
        )

    deleted = (
        store.delete_all_schedules()
        if delete_all
        else store.delete_schedules_by_filter(
            schedule_ids=normalized_schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
        )
    )
    return tool_result(
        "personal_delete_saved_schedules",
        deleted_count=len(deleted),
        filters=filters,
        deleted=deleted,
    )


def structured_request_from_week01_schedule(schedule: dict[str, Any]) -> SaveStructuredRequestInput:
    """Week 1 임시 일정 dict를 Week 3 저장 입력으로 변환합니다."""

    # TODO: Week 1 schedule의 attendees/id를 Week 3 members/source_schedule_id에 맞춰 변환하세요.
    return SaveStructuredRequestInput(
        kind="personal_schedule",
        title=schedule["title"],
        date=schedule["date"],
        start_time=schedule["start_time"],
        end_time=schedule["end_time"],
        members=schedule["attendees"],
        source_schedule_id=schedule["id"],
    )

    # 다음 field는 채우지 않는게 맞는 것 같음.
    # reason: str | None = Field(
    #     default=None, description="요청을 이렇게 구조화한 판단 근거."
    # )
    # original_text: str = Field(
    #     default="", description="사용자가 입력한 요청 원문. 원문 보존용 필드다."
    # )
    #
    # Week 1 일정의 personal_... ID는 source_schedule_id로 전달되고,
    # AppSQLiteStore가 이를 schedules.schedule_id로 그대로 사용한다.
    # source_schedule_id가 없는 일반 저장 일정은 sch_... ID가 생성되므로
    # 현재 ID 규칙에서는 schedule_id로 Week 1 변환 여부를 구분할 수 있다.
    #
    # 또, reason은 llm이 구조화한 근거를 담는 필드이므로..
    # original_text는 원문 보존용 필드인데, Week 1의 schedule에는 원문이 없으므로 그냥 빈 문자열로 두는게 맞는 것 같다.

    # SaveStructuredRequestInput
    # kind: RequestKind = "unknown",
    # title: str | None = None,
    # date: str | None = None,
    # start_time: str | None = None,
    # end_time: str | None = None,
    # members: list[str] = list,
    # priority: str | None = None,
    # reason: str | None = None,
    # original_text: str = "",
    # source_schedule_id: str | None = None


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

    store = app_store if app_store is not None else _store()
    return _delete_saved_schedules(
        store=store,
        schedule_ids=schedule_ids,
        date=date,
        title=title,
        start_time=start_time,
        time_unspecified=time_unspecified,
        delete_all=delete_all,
    )
