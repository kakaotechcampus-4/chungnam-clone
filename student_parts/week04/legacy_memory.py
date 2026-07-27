from __future__ import annotations
import json
from langchain_core.tools import tool

from fixed.runtime_clock import current_app_date_iso
from student_parts.week04.common import json_payload, safe_limit
from student_parts.week04.references import search_personal_reference_hits
from student_parts.week04.schemas import SearchNanaMemoryInput
from student_parts.week04.stores import REFERENCE_STORE, SQLITE_STORE

def _decode_attendees(raw_attendees: str | None) -> list[str]:
    try:
        decoded = json.loads(raw_attendees or "[]")
    except Exception:
        return []
    return decoded if isinstance(decoded, list) else []


def _schedule_attendees(schedule: dict) -> list[str]:
    attendees = schedule.get("attendees")
    if isinstance(attendees, list):
        return [str(value) for value in attendees]
    if isinstance(attendees, str):
        return [str(value) for value in _decode_attendees(attendees)]
    return [
        str(value)
        for value in _decode_attendees(schedule.get("attendees_json"))
    ]


def _schedule_chunk(schedule: dict) -> dict:
    attendees = _schedule_attendees(schedule)
    title = str(schedule.get("title") or "")
    date = str(schedule.get("date") or "")
    start_time = str(schedule.get("start_time") or "")
    end_time = str(schedule.get("end_time") or "")

    content_parts = [value for value in [title, date] if value]
    if start_time and end_time:
        content_parts.append(f"{start_time}-{end_time}")
    elif start_time:
        content_parts.append(start_time)
    if attendees:
        content_parts.append(", ".join(attendees))

    return {
        "id": str(schedule.get("schedule_id") or ""),
        "content": " | ".join(content_parts),
        "metadata": {
            "schedule_id": schedule.get("schedule_id"),
            "request_id": schedule.get("request_id"),
            "kind": schedule.get("request_kind"),
            "date": schedule.get("date"),
            "start_time": schedule.get("start_time"),
            "end_time": schedule.get("end_time"),
            "attendees": attendees,
            "source": schedule.get("source"),
        },
    }

@tool(args_schema=SearchNanaMemoryInput)
def search_nana_memory(
    query: str,
    date_from: str | None = None,
    date_to: str | None = None,
    attendee: str | None = None,
    limit: int = 5,
) -> str:
    """개인 참고자료와 SQLite 저장 일정을 한 번에 검색하고 일정 chunk를 반환합니다."""

    normalized_limit = safe_limit(
        limit,
        default=5,
        maximum=20,
    )
    today = current_app_date_iso()
    effective_date_from = date_from or today

    reference_hits = search_personal_reference_hits(
        REFERENCE_STORE,
        query=query,
        top_k=normalized_limit,
    )

    schedules = SQLITE_STORE.list_schedules(
        limit=normalized_limit,
        date_from=effective_date_from,
        date_to=date_to,
    )

    normalized_attendee = str(attendee or "").strip().casefold()
    if normalized_attendee:
        schedules = [
            schedule
            for schedule in schedules
            if any(
                normalized_attendee in value.casefold()
                for value in _schedule_attendees(schedule)
            )
        ]

    schedule_chunks = [
        _schedule_chunk(schedule)
        for schedule in schedules[:normalized_limit]
    ]

    context_parts = [
        str(hit.get("content") or "")
        for hit in reference_hits
        if hit.get("content")
    ]
    context_parts.extend(
        str(chunk.get("content") or "")
        for chunk in schedule_chunks
        if chunk.get("content")
    )

    return json_payload(
        {
            "query": query,
            "today": today,
            "date_from": effective_date_from,
            "date_to": date_to,
            "attendee": attendee,
            "reference_hits": reference_hits,
            "schedule_chunks": schedule_chunks,
            "context": "\n\n".join(context_parts),
            "reference_backend": REFERENCE_STORE.backend_info(),
        }
    )
