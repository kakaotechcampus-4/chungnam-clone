from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from fixed.external_mcp import call_external_tool_payload
from fixed.external_people_store import (
    external_schedule_summary,
    normalize_external_member_names,
    normalize_external_schedule_date_bounds,
)
from fixed.llm import chat_model
from fixed.mcp_client import (
    call_local_mcp_tool,
    call_local_mcp_tool_sync,
    load_local_mcp_tools,
    load_local_mcp_tools_sync,
)
from fixed.runtime_clock import current_app_date_iso
from fixed.session_scope import DEFAULT_SESSION_SCOPE, current_session_scope
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES, join_system_prompt
from student_parts.week02_structure_natural_language_requests import StructuredRequest
from student_parts.week04_retrieve_nanas_memory import safe_limit, week04_prompt_parts, week04_tools


_WEEK05_AGENT: Any | None = None


# [5주차 수강생 구현 가이드]
#
# 목표
#   외부 SQLite/MCP 서버에 있는 Kana의 이전 대화와 공유 일정을 LangChain agent가 사용할 수 있게 감쌉니다.
#   학생이 직접 SQL을 작성하는 주차가 아니라, MCP tool을 호출하고 그 결과를 agent용 JSON으로 전달하는
#   wrapper tool을 만드는 주차입니다.
#
# 과제 구성
#   - 메인과제: 외부 SQLite/MCP 서버의 이전 대화를 검색·로드하고 그 대화에서 일정을 추출하는
#     MCP wrapper 세로 슬라이스에 더해, 공유 일정 조회(list_shared_schedules)와
#     내 일정·외부 멤버 busy-time을 한 rows로 합치는 collect_member_schedules까지 완성합니다.
#     이 두 tool은 Week 6 Kana 하위 agent가 그대로 재사용하는 연결 지점이라 메인과제입니다.
#   - 추가 과제: 공유 일정 저장소에 row를 직접 등록·삭제하는 create_shared_schedule/delete_shared_schedule
#     wrapper를 확장합니다. 구현하지 않으려면 week05_tools() 목록에서 이 두 tool을 빼면 됩니다.
#
# 구현 위치와 사용할 코드
#   - 이 파일(student_parts/week05_load_kanas_past_conversations.py)의 @tool wrapper 함수들을 구현합니다.
#   - 실제 외부 SQLite/MCP tool 구현은 mcp_server/sqlite_mcp_server.py에 있으며, 학생은 이 파일을 직접 수정하지 않습니다.
#   - MCP 호출은 fixed/mcp_client.py의 call_local_mcp_tool_sync를 이 파일에서 별칭으로 둔
#     call_mcp_tool_sync(tool_name, args)를 사용합니다.
#   - load_conversation_messages는 fixed/external_mcp.py의 call_external_tool_payload(...)를 사용해
#     외부 tool payload를 dict로 받은 뒤 json_payload()로 감쌉니다.
#   - 멤버 이름/날짜 정규화와 요약은 fixed/external_people_store.py의
#     normalize_external_member_names(), normalize_external_schedule_date_bounds(),
#     external_schedule_summary()를 사용합니다.
#   - 내 일정 수집은 _personal_schedules_for_current_scope()에서 처리합니다. 이 helper는
#     fixed/app_store.py의 AppSQLiteStore(CONFIG.app_db_path).list_schedules(...)와
#     student_parts/week01_wake_up_nana.py의 PERSONAL_SCHEDULES 중 현재 대화 범위 row를 합칩니다.
#   - Week 3+ AppSQLiteStore는 개인/그룹 일정을 저장할 때 공유 일정 저장소에 자동 동기화할 수 있습니다.
#     list_shared_schedules wrapper(메인)는 공유 저장소 row를 직접 확인할 때,
#     create/delete_shared_schedule wrapper(추가)는 row를 직접 등록/삭제해 보정할 때 사용합니다.
#   - week05_tools()는 student_parts/week04_retrieve_nanas_memory.py의 week04_tools() 위에
#     Week 5 MCP wrapper tool들을 누적해 Week 5 단일 agent에 공개합니다.
#     추가 과제(create/delete_shared_schedule)를 구현하지 않으려면 week05_tools() 목록에서 해당 tool을 빼면 됩니다.
#
# 메인과제 구현 대상
#   1. search_previous_conversations
#      - query, member_names, limit를 받습니다.
#      - 이 파일의 call_mcp_tool_sync("search_previous_conversations", args)를 호출하고 결과 문자열을 그대로 반환합니다.
#      - 멤버 이름 정규화는 외부 SQLite store/MCP 경계에서 한 번만 처리하므로 wrapper에서 중복 변환하지 않습니다.
#
#   2. load_conversation_messages
#      - conversation_id로 외부 SQLite/MCP helper에서 이전 대화 메시지를 조회합니다.
#      - call_external_tool_payload("load_conversation_messages", {"conversation_id": conversation_id})를 사용합니다.
#      - 대화 메시지의 sender/content/created_at 순서가 보존되도록 결과를 가공하지 않습니다.
#
#   3. extract_schedules_from_history
#      - member_names, date_from, date_to를 받습니다.
#      - call_mcp_tool_sync("extract_schedules_from_history", args)를 호출합니다.
#      - 날짜 형식 정리는 외부 SQLite store/MCP 경계에서 한 번만 처리합니다.
#      - 결과 rows는 member_name/title/date/start_time/end_time/notes 필드를 유지해야 합니다.
#
#   4. list_shared_schedules
#      - call_mcp_tool_sync("list_shared_schedules", args)를 호출해 공유 일정 저장소 row를 조회합니다.
#      - 공유 저장소 자체를 확인할 때는 "나"를 포함한 등록 row를 조회합니다.
#      - 필터 없이 호출하면 외부 실습용 기본 공유 일정 row가 우선 반환될 수 있습니다.
#      - Week 6 Kana 하위 agent가 공유 저장소 row 조회에 그대로 사용하는 tool입니다.
#
#   5. collect_member_schedules
#      - 3주차 이후 저장된 내 일정은 앱 SQLite에서 읽고, 현재 대화의 임시 일정만 추가로 합칩니다.
#      - 외부 멤버 일정은 call_mcp_tool_sync("extract_schedules_from_history", args) 결과를 이 tool 안에서 읽습니다.
#      - 두 출처를 member_name/title/date/start_time/end_time/notes가 있는 rows 배열로 직접 합칩니다.
#      - schedule_summary도 함께 반환해 LLM이 바쁜 시간을 자연어로 설명할 수 있게 합니다.
#      - PERSONAL_SCHEDULES는 현재 대화 범위의 아직 DB에 없는 임시 일정만 합치고, SQLite에 이미 저장된 일정과 중복하지 않습니다.
#      - Week 6 추가 과제(find_common_available_slots)가 이 tool의 rows를 busy_rows 근거로 사용합니다.
#
# 추가 과제 구현 대상 (구현하지 않으려면 week05_tools() 목록에서 해당 tool을 제거)
#   1. create_shared_schedule / delete_shared_schedule
#      - 각각 call_mcp_tool_sync("create_shared_schedule" / "delete_shared_schedule", args)를 호출합니다.
#      - 공유 일정 저장소 row를 생성/삭제할 때 MCP tool 결과를 그대로 전달합니다.
#      - schedule_id 또는 source_conversation_id를 보존해야 나중에 수정/삭제 동기화가 가능합니다.
#
# 책임 경계
#   mcp_server/sqlite_mcp_server.py의 @mcp.tool 구현은 학생 구현 대상이 아닙니다.
#   이 파일의 wrapper tool은 직접 SQL이나 중복 정규화 helper를 두지 않고 store/MCP helper의 결과 JSON을 전달합니다.
#   week05_tools()는 Week 1-4 도구에 외부 SQLite/MCP 일정 도구를 누적합니다.
#   외부 멤버 busy-time 조회와 공유 저장소 row 조회는 Week 5 범위지만, 여러 사람의 최종 회의 시간 선택은 Week 6 범위입니다.
#
# 검증 방법
#   - 메인과제: ./run.sh --week5에서 외부 팀원 일정 조회 요청을 입력하고, trace에서
#     search_previous_conversations, load_conversation_messages, extract_schedules_from_history 중
#     어떤 tool이 어떤 순서로 호출됐는지 확인합니다.
#     collect_member_schedules 결과 rows에 "나"와 외부 멤버 일정이 같은 구조로 들어 있고,
#     list_shared_schedules 결과에 rows와 schedule_summary가 유지되는지 확인합니다.
#   - 추가 과제: create_shared_schedule로 등록한 row가 list_shared_schedules 조회에 나타나고
#     delete_shared_schedule로 삭제되는지 확인합니다.
#
# 함수별 동작 설명 ([메인]/[추가]/[공통]은 각 함수가 속한 과제 티어입니다)
#   - [메인] _schedule_scope(schedule)
#     Week 1 임시 일정이 어느 대화 범위에 속하는지 읽습니다. session_id가 없으면 기본 scope로 처리합니다.
#
#   - [메인] _personal_schedules_for_current_scope()
#     Week 3 이후 SQLite에 저장된 내 일정과 현재 대화에만 남아 있는 Week 1 임시 일정을 합칩니다.
#     이미 SQLite에 저장된 일정과 임시 일정이 중복되지 않도록 schedule_id/id를 기준으로 한 번 걸러냅니다.
#
#   - [공통] json_payload(payload)
#     외부 MCP 결과나 내부 helper 결과 dict를 한글이 보존되는 JSON 문자열로 바꿉니다.
#
#   - [메인] SearchPreviousConversationsInput / LoadConversationMessagesInput / ExtractSchedulesFromHistoryInput
#     외부 이전 대화 검색, 대화 메시지 로드, 외부 대화에서 일정 추출 tool의 입력 스키마입니다.
#
#   - [메인] ListSharedSchedulesInput / CollectMemberSchedulesInput
#     공유 일정 저장소 row 조회와, 내 일정·외부 멤버 busy-time을 같은 rows 배열로 합치는 tool의 입력 스키마입니다.
#
#   - [추가] CreateSharedScheduleInput / DeleteSharedScheduleInput
#     외부 공유 일정 저장소에 row를 생성, 삭제할 때 쓰는 입력 스키마입니다.
#
#   - [메인] _structured_request_from_schedule_row(row)
#     SQLite schedule row나 Week 1 임시 schedule row를 Week 2 StructuredRequest 모양으로 읽습니다.
#     뒤에서 내 일정 row를 외부 멤버 row와 같은 구조로 맞출 때 사용합니다.
#
#   - [메인] _collect_member_schedules(...)
#     내 일정과 외부 멤버 일정을 같은 member_name/title/date/start_time/end_time/notes row 구조로 합칩니다.
#     외부 멤버 이름과 날짜 범위는 fixed/external_people_store.py helper로 정규화합니다.
#
#   - [메인] search_previous_conversations(...)
#     외부 SQLite/MCP 서버에 저장된 과거 대화를 검색합니다. wrapper는 query/member_names/limit를 넘기고 결과 문자열을 그대로 반환합니다.
#
#   - [메인] load_conversation_messages(conversation_id)
#     검색으로 찾은 특정 외부 대화의 전체 메시지를 불러옵니다. sender/content/created_at 순서를 보존합니다.
#
#   - [메인] extract_schedules_from_history(...)
#     외부 멤버의 이전 대화에서 일정 또는 바쁜 시간 row를 추출합니다.
#
#   - [메인] list_shared_schedules(...)
#     공유 일정 저장소 row를 조회하는 MCP wrapper입니다. Week 6 Kana 하위 agent도 그대로 사용합니다.
#
#   - [메인] collect_member_schedules(...)
#     내 일정과 외부 멤버 busy-time을 한 번에 모으는 Week 5 핵심 tool입니다.
#     Week 6의 공통 가능 시간 결정 tool(추가 과제)이 이 rows를 busy_rows 근거로 사용합니다.
#
#   - [추가] create_shared_schedule(...) / delete_shared_schedule(...)
#     공유 일정 저장소에 row를 등록/삭제하는 MCP wrapper입니다. source_conversation_id와 schedule_id를 보존해 동기화 근거로 씁니다.
#
#   - [공통] week05_tools()
#     Week 4까지의 tool에 외부 대화/MCP/공유 일정 tool을 누적합니다.
#
#   - [공통] week05_system_prompt() / week05_prompt_parts()
#     개인 저장/RAG는 이전 주차 도구로, 외부 멤버 대화와 일정은 MCP wrapper로 처리하도록 agent 역할을 설명합니다.
#
#   - [공통] build_week05_agent() / build_week_agent()
#     Week 1~5 tool을 가진 agent를 한 번만 만들고 재사용합니다.


call_mcp_tool = call_local_mcp_tool
call_mcp_tool_sync = call_local_mcp_tool_sync
load_langchain_mcp_tools = load_local_mcp_tools
load_langchain_mcp_tools_sync = load_local_mcp_tools_sync


def _schedule_scope(schedule: dict[str, Any]) -> str:
    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


_EARLIEST_SCHEDULE_DATE = "0001-01-01"
_LATEST_SCHEDULE_DATE = "9999-12-31"


def _resolve_schedule_date_range(
    date_from: str | None,
    date_to: str | None,
    *,
    fill_open_range: bool,
) -> tuple[str | None, str | None, str | None]:
    """date_from/date_to의 None 처리와 역순 검증을 한 곳에서 처리합니다.

    fill_open_range가 True면 None/빈 문자열을 그 방향 전체 기간(가장 이르거나 늦은 날짜)으로
    채운다. False면 None은 그대로 두어 "필터 없음"이 store에 전달되게 한다(list_shared_schedules는
    None이면 서버가 실습용 기본 공유 일정을 채워 반환하므로 이 방향으로 sentinel을 채우면 안 된다).
    반환값의 세 번째 항목이 None이 아니면 호출자는 MCP tool을 부르지 않고 그 문자열을 에러로
    바로 반환해야 한다.
    """

    resolved_from = date_from or None
    resolved_to = date_to or None
    if fill_open_range:
        resolved_from = resolved_from or _EARLIEST_SCHEDULE_DATE
        resolved_to = resolved_to or _LATEST_SCHEDULE_DATE
    if resolved_from and resolved_to and resolved_to < resolved_from:
        error = f"date_to는 date_from보다 앞설 수 없습니다: date_from={resolved_from} date_to={resolved_to}"
        return resolved_from, resolved_to, error
    return resolved_from, resolved_to, None


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다."""

    saved_schedules = AppSQLiteStore(CONFIG.app_db_path).list_schedules()
    saved_schedule_ids = {row["schedule_id"] for row in saved_schedules}

    current_scope = current_session_scope()
    scoped_temp_schedules = [
        schedule for schedule in PERSONAL_SCHEDULES if _schedule_scope(schedule) == current_scope
    ]
    unsaved_temp_schedules = [
        schedule for schedule in scoped_temp_schedules if schedule.get("id") not in saved_schedule_ids
    ]

    return saved_schedules + unsaved_temp_schedules


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


class SearchPreviousConversationsInput(BaseModel):
    """외부 이전 대화 검색 입력입니다."""

    query: str
    member_names: list[str] | None = None
    limit: int | None = Field(default=5, ge=1, le=50)


class LoadConversationMessagesInput(BaseModel):
    """외부 대화 메시지 조회 입력입니다."""

    conversation_id: str


class ExtractSchedulesFromHistoryInput(BaseModel):
    """외부 멤버 일정 추출 입력입니다."""

    member_names: list[str]
    date_from: str | None = Field(
        default=None,
        description=(
            "조회 시작 날짜, 'YYYY-MM-DD' 형식(예: '2026-07-01'). 이 날짜 이상인 일정만 조회한다. "
            "정확한 하한을 모르면 비워도 되며(None), 그 경우 조회 가능한 가장 이른 날짜부터 조회한다."
        ),
    )
    date_to: str | None = Field(
        default=None,
        description=(
            "조회 종료 날짜, 'YYYY-MM-DD' 형식(예: '2026-07-07'). 이 날짜 이하인 일정만 조회한다. "
            "정확한 상한을 모르면 비워도 되며(None), 그 경우 조회 가능한 가장 늦은 날짜까지 조회한다."
        ),
    )


class CreateSharedScheduleInput(BaseModel):
    """공유 일정 생성 입력입니다."""

    member_name: str
    title: str
    date: str
    start_time: str
    end_time: str = "미정"
    notes: str | None = None
    source_conversation_id: str | None = None
    schedule_id: str | None = None


class DeleteSharedScheduleInput(BaseModel):
    """공유 일정 삭제 입력입니다."""

    schedule_id: str | None = None
    source_conversation_id: str | None = None


class ListSharedSchedulesInput(BaseModel):
    """공유 일정 조회 입력입니다."""

    member_names: list[str] | None = None
    date_from: str | None = Field(
        default=None,
        description=(
            "조회 시작 날짜, 'YYYY-MM-DD' 형식(예: '2026-07-01'). 이 날짜 이상인 일정만 조회한다. "
            "다른 필터도 모두 비우면 실습용 기본 공유 일정이 반환되므로, 필터 없는 전체 조회를 "
            "원하면 None으로 둔다(가장 이른 날짜 같은 값을 임의로 채우지 않는다)."
        ),
    )
    date_to: str | None = Field(
        default=None,
        description=(
            "조회 종료 날짜, 'YYYY-MM-DD' 형식(예: '2026-07-07'). 이 날짜 이하인 일정만 조회한다. "
            "다른 필터도 모두 비우면 실습용 기본 공유 일정이 반환되므로, 필터 없는 전체 조회를 "
            "원하면 None으로 둔다(가장 늦은 날짜 같은 값을 임의로 채우지 않는다)."
        ),
    )
    source_conversation_id: str | None = None
    limit: int | None = Field(default=50, ge=1, le=200)


class CollectMemberSchedulesInput(BaseModel):
    """내 일정과 외부 멤버 busy-time 수집 입력입니다."""

    member_names: list[str]
    date_from: str
    date_to: str


def _structured_request_from_schedule_row(row: dict[str, Any]) -> StructuredRequest:
    """앱 일정 row를 Week 2 StructuredRequest 기준으로 읽습니다."""

    return StructuredRequest(
        kind="personal_schedule",
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

    external_payload = json.loads(
        call_mcp_tool_sync(
            "extract_schedules_from_history",
            {"member_names": member_names, "date_from": date_from, "date_to": date_to},
        )
    )
    external_rows = external_payload.get("rows", [])

    my_rows = [
        {
            "member_name": "나",
            "title": schedule.get("title"),
            "date": schedule.get("date"),
            "start_time": schedule.get("start_time"),
            "end_time": schedule.get("end_time"),
            "notes": None,
        }
        for schedule in personal_schedules
    ]

    rows = my_rows + external_rows
    return {"rows": rows, "schedule_summary": external_schedule_summary(rows)}


@tool(args_schema=SearchPreviousConversationsInput)
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> str:
    """외부 SQLite 데이터베이스에 저장된 이전 대화를 검색합니다. query에는 LLM이 고른 짧은 핵심 명사나 구를 넣습니다."""

    safe = safe_limit(limit, default=5, maximum=50)
    return call_mcp_tool_sync(
        "search_previous_conversations",
        {"query": query, "member_names": member_names, "limit": safe},
    )


@tool(args_schema=LoadConversationMessagesInput)
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다."""

    payload = call_external_tool_payload("load_conversation_messages", {"conversation_id": conversation_id})
    return json_payload(payload)


@tool(args_schema=ExtractSchedulesFromHistoryInput)
def extract_schedules_from_history(
    member_names: list[str],
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다.

    date_from/date_to는 zero-padded 'YYYY-MM-DD' 형식(예: '2026-07-01')으로 입력해야 합니다.
    저장소가 문자열 그대로 비교하므로 자리수가 다르면(예: '2026-7-1') 범위가 잘못 잡힙니다.
    """

    resolved_from, resolved_to, error = _resolve_schedule_date_range(date_from, date_to, fill_open_range=True)
    if error:
        return json_payload({"ok": False, "tool_name": "extract_schedules_from_history", "error": error})
    return call_mcp_tool_sync(
        "extract_schedules_from_history",
        {"member_names": member_names, "date_from": resolved_from, "date_to": resolved_to},
    )


@tool(args_schema=CreateSharedScheduleInput)
def create_shared_schedule(
    member_name: str,
    title: str,
    date: str,
    start_time: str,
    end_time: str = "미정",
    notes: str | None = None,
    source_conversation_id: str | None = None,
    schedule_id: str | None = None,
) -> str:
    """외부 MCP 공유 일정 저장소에 일정을 등록하거나 갱신합니다.

    date는 zero-padded 'YYYY-MM-DD' 형식(예: '2026-07-01')으로 입력해야 합니다.
    저장소가 문자열 그대로 비교/저장하므로 다른 형식(예: '2026-7-1')은 이후 조회 시 누락될 수 있습니다.
    """

    # TODO: call_mcp_tool_sync("create_shared_schedule", args)로 공유 일정 row를 생성/갱신하세요.
    ...


@tool(args_schema=DeleteSharedScheduleInput)
def delete_shared_schedule(
    schedule_id: str | None = None,
    source_conversation_id: str | None = None,
) -> str:
    """외부 MCP 공유 일정 저장소에서 일정을 삭제합니다."""

    # TODO: call_mcp_tool_sync("delete_shared_schedule", args)로 공유 일정을 삭제하세요.
    ...


@tool(args_schema=ListSharedSchedulesInput)
def list_shared_schedules(
    member_names: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source_conversation_id: str | None = None,
    limit: int = 50,
) -> str:
    """외부 MCP 공유 일정 저장소에 등록된 일정을 조회합니다. 필터가 없으면 기본 공유 일정을 반환합니다.

    date_from/date_to는 zero-padded 'YYYY-MM-DD' 형식(예: '2026-07-01')으로 입력해야 합니다.
    저장소가 문자열 그대로 비교하므로 자리수가 다르면(예: '2026-7-1') 범위가 잘못 잡힙니다.
    """

    resolved_from, resolved_to, error = _resolve_schedule_date_range(date_from, date_to, fill_open_range=False)
    if error:
        return json_payload({"ok": False, "tool_name": "list_shared_schedules", "error": error})
    safe = safe_limit(limit, default=50, maximum=200)
    return call_mcp_tool_sync(
        "list_shared_schedules",
        {
            "member_names": member_names,
            "date_from": resolved_from,
            "date_to": resolved_to,
            "source_conversation_id": source_conversation_id,
            "limit": safe,
        },
    )


@tool(args_schema=CollectMemberSchedulesInput)
def collect_member_schedules(member_names: list[str], date_from: str, date_to: str) -> str:
    """내 일정과 다른 사람들의 일정을 MCP SQLite 기록에서 모읍니다.

    date_from/date_to는 zero-padded 'YYYY-MM-DD' 형식(예: '2026-07-01')으로 입력해야 합니다.
    저장소가 문자열 그대로 비교하므로 자리수가 다르면(예: '2026-7-1') 범위가 잘못 잡힙니다.
    """

    resolved_from, resolved_to, error = _resolve_schedule_date_range(date_from, date_to, fill_open_range=True)
    if error:
        return json_payload({"ok": False, "tool_name": "collect_member_schedules", "error": error})

    personal_schedules = _personal_schedules_for_current_scope()
    collected = _collect_member_schedules(
        member_names=member_names,
        date_from=resolved_from,
        date_to=resolved_to,
        personal_schedules=personal_schedules,
    )
    return json_payload(
        {
            "ok": True,
            "tool_name": "collect_member_schedules",
            "rows": collected["rows"],
            "schedule_summary": collected["schedule_summary"],
        }
    )


def week05_tools() -> list[Any]:
    """4주차까지의 도구에 외부 SQLite/MCP 일정 도구를 누적한 목록입니다."""

    return [
        *week04_tools(),
        search_previous_conversations,
        load_conversation_messages,
        extract_schedules_from_history,
        create_shared_schedule,
        delete_shared_schedule,
        list_shared_schedules,
        collect_member_schedules,
    ]


def week05_system_prompt() -> str:
    """5주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week05_prompt_parts())


def week05_prompt_parts() -> list[str]:
    """1~5주차 system prompt 조각을 누적합니다."""

    return [
        *week04_prompt_parts(),
        """search_previous_conversation 툴은 인물, 키워드와 '메시지' 탐색 요청 시 사용된다 * 나 외의 인물과 공유한 메시지나 대화기록이 있는지 찾는 맥락  
ex)철수가 남긴 메시지 , 영희가 남긴 일정  
* member_names는 나를 제외한 언급된 사람이름을 채운다.
* query는 핵심 키워드가 포함된 짧은 키워드만 작성 ex)철수랑 야식약속한 메시지 있어? => 야식, 
영희가 공유한 일정있어? => 일정 공유  
* 조회결과에 대해서 조회된 row의 내용을 모두 말하지 말고, 관련된 부분만 말한다.
* 조회결과가 아예 관련 없는 내용만이 조회되면 일치하는 조회결과가 없다고 답한다.
* 이 툴은 쿼리가 매칭된 대화의 일부분 만을 조회하며, 대화의 전문이 확인 필요한 경우 load_conversation_messages에서 conversation_id를 통해 조회한다.""",
        """load_conversation_messages 툴은 search_previous_conversations로 찾은 특정 대화의 자세한 내용을 확인 할 필요가 있을때 이어서 사용한다.
필요한 경우가 아니면 바로 직전 대화에서 완전히 파악된 대화와 같은 대화를
다시 검색하지 않는다. """,
        """extract_schedules_from_history 툴은 '공유 저장소'에서 '단일 멤버'와 '특정 기간'의 일정을 묻는 맥락에서 사용된다.
ex)철수 일정좀 알려줘, 이번달 영희 바빠?
날짜를 모르면 date_from/date_to를 비워도 되며 그 방향 전체 기간이 조회되고, 종료일이 시작일보다
빠르면 다른 tool을 더 부르지 않고 그 응답의 에러 메시지로 바로 답한다.
답변은 단순히 일정을 나열하는 것이 아닌, 질문에서 제공된 의문점 위주로 답변한다.
""",
        """list_shared_schedules는 내가 포함되지 않은 일정의 조율 및 공유 일정 저장소에 등록된 일정 자체를 확인하고 응답하기 위한 툴이다.  
        ex)민수, 철수는 이번달 일정 비는 날있어?
        공유 저장소에서, '여러명의 인물'(혹은 인물이 명확치 않을때)의 일정을 조회할 맥락에서 사용된다.
특히 이전 대화 맥락에서 명확한 source_conversation_id가 드러나고, 이를 이용해야한 공유저장소의 일정 조회시에 사용한다.
* source_conversation_id는 search_previous_conversations나 load_conversation_messages로 실제 확인한
conversation_id가 있을 때만 채우고, 그런 근거가 없으면 항상 비워둔다.  
답변은 단순히 일정을 나열하는 것이 아닌, 질문에서 제공된 의문점 위주로 답변한다.
멤버에 내가 포함되지 않은 경우 일정 조율 요청은 여기서 확인후 답변한다.
""",
        """collect_member_schedules는 내일정 조회 및 나를 포함한 경우의 extract_schedules_from_history,list_shared_schedules과 같은 일정 조회,조율 용도이다.하지만 비슷하지만 반드시 맥락상 '나 자신'이 포함되어야한다.  
        ex)나랑 민수, 영희랑 7월 20일 날 약속 잡아도될까, 나랑 영희의 이번달 겹치는 일정 조회해줘    
""",
    ]


def build_week05_agent() -> object:
    """Week 1-5 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK05_AGENT
    if _WEEK05_AGENT is None:
        _WEEK05_AGENT = create_agent(
            model=chat_model(),
            tools=week05_tools(),
            system_prompt=week05_system_prompt(),
        )
    return _WEEK05_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week05_agent()
