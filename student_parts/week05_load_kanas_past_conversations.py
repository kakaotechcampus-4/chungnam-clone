from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.app_store import AppSQLiteStore
from fixed.config import CONFIG
from fixed.external_mcp import call_external_tool_payload
from fixed.external_people_store import (
    PERSONAL_SHARED_MEMBER_NAME,
    external_schedule_summary,
    normalize_external_member_names,
    normalize_external_schedule_date_bounds,
)
from fixed.langchain_trace import normalize_messages_value
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
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts, week04_tools


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

# 조율 후보로 훑을 내 SQLite 일정 개수입니다. list_schedules 기본값(12)은 날짜 범위 필터를
# 적용하기 전 상한이라 그대로 쓰면 최근 12건 밖의 일정이 busy-time에서 빠질 수 있습니다.
PERSONAL_SCHEDULE_SCAN_LIMIT = 200
PERSONAL_SCHEDULE_NOTES = "내 앱 일정"

COLLECT_MEMBER_SCHEDULES_TOOL_NAME = "collect_member_schedules"
# collect_member_schedules가 이미 "나" row로 돌려준 내용을 그대로 다시 조회하는 tool들입니다.
# prompt/docstring 안내로는 100% 막히지 않아서 아래 middleware에서 코드로 한 번 더 걸러냅니다.
PERSONAL_LIST_TOOL_NAMES_COVERED_BY_COLLECT = (
    "personal_list_schedules",
    "personal_list_saved_schedules",
)


def _schedule_scope(schedule: dict[str, Any]) -> str:
    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다."""

    saved_schedules = AppSQLiteStore(CONFIG.app_db_path).list_schedules(limit=PERSONAL_SCHEDULE_SCAN_LIMIT)
    # Week 3 double-write는 Week 1 임시 일정의 id를 그대로 SQLite schedule_id로 쓰므로
    # 같은 일정이 두 출처에 모두 있으면 SQLite row 쪽만 남깁니다.
    saved_schedule_ids = {str(row.get("schedule_id")) for row in saved_schedules if row.get("schedule_id")}
    session_id = current_session_scope()
    temporary_schedules = [
        schedule
        for schedule in PERSONAL_SCHEDULES
        if _schedule_scope(schedule) == session_id and str(schedule.get("id")) not in saved_schedule_ids
    ]
    return [*saved_schedules, *temporary_schedules]


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


class SearchPreviousConversationsInput(BaseModel):
    """외부 이전 대화 검색 입력입니다."""

    query: str
    member_names: list[str] | None = None
    limit: int = Field(default=5, ge=1, le=50)


class LoadConversationMessagesInput(BaseModel):
    """외부 대화 메시지 조회 입력입니다."""

    conversation_id: str


class ExtractSchedulesFromHistoryInput(BaseModel):
    """외부 멤버 일정 추출 입력입니다."""

    member_names: list[str]
    date_from: str
    date_to: str


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
    date_from: str | None = None
    date_to: str | None = None
    source_conversation_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


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

    normalized_member_names = normalize_external_member_names(member_names)
    normalized_date_from, normalized_date_to = normalize_external_schedule_date_bounds(
        member_names, date_from, date_to
    )
    # 내 일정은 아래 personal_schedules(앱 DB 원본)에서 읽습니다. 공유 저장소에는 Week 3
    # 동기화가 만든 "나" 복사본도 있어서 외부 조회에 "나"를 그대로 넘기면 같은 일정이 두 번 집계됩니다.
    external_member_names = [name for name in normalized_member_names if name != PERSONAL_SHARED_MEMBER_NAME]

    personal_rows: list[dict[str, Any]] = []
    for schedule in personal_schedules:
        request = _structured_request_from_schedule_row(schedule)
        # 날짜 비교는 외부 store와 같은 YYYY-MM-DD 문자열 기준으로 맞춥니다.
        schedule_date = str(request.date or "").split("T", 1)[0].strip()
        if not schedule_date:
            continue
        if normalized_date_from and schedule_date < normalized_date_from:
            continue
        if normalized_date_to and schedule_date > normalized_date_to:
            continue
        personal_rows.append(
            {
                "member_name": PERSONAL_SHARED_MEMBER_NAME,
                "title": request.title or "제목 없음",
                "date": schedule_date,
                "start_time": request.start_time or "미정",
                "end_time": request.end_time or "미정",
                "notes": PERSONAL_SCHEDULE_NOTES,
            }
        )

    # 외부 멤버 busy-time은 MCP tool 결과를 그대로 읽습니다. rows는 이미
    # member_name/title/date/start_time/end_time/notes 구조라 다시 매핑하지 않습니다.
    external_payload = json.loads(
        call_mcp_tool_sync(
            "extract_schedules_from_history",
            {
                "member_names": external_member_names,
                "date_from": date_from,
                "date_to": date_to,
            },
        )
    )
    external_rows = external_payload.get("rows") or []

    rows = [*personal_rows, *external_rows]
    rows.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("start_time") or ""),
            str(row.get("member_name") or ""),
        )
    )

    return {
        "member_names": normalized_member_names,
        "external_member_names": external_member_names,
        "date_from": normalized_date_from,
        "date_to": normalized_date_to,
        "rows": rows,
        "personal_row_count": len(personal_rows),
        "external_row_count": len(external_rows),
        "schedule_summary": external_schedule_summary(rows),
    }


@tool(args_schema=SearchPreviousConversationsInput)
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> str:
    """외부 SQLite 데이터베이스에 저장된 이전 대화를 검색합니다. query에는 LLM이 고른 짧은 핵심 명사나 구를 넣습니다."""

    # MCP tool이 이미 ok/tool_name/rows JSON 문자열을 만들어 주므로 다시 감싸지 않습니다.
    # 멤버 이름 정규화도 외부 store 경계에서 한 번만 처리하므로 인자를 그대로 넘깁니다.
    return call_mcp_tool_sync(
        "search_previous_conversations",
        {"query": query, "member_names": member_names, "limit": limit},
    )


@tool(args_schema=LoadConversationMessagesInput)
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다."""

    # 여기만 payload를 dict로 받으므로 json_payload로 다시 문자열로 만듭니다.
    # sender/content/created_at 순서를 바꾸지 않고 그대로 전달합니다.
    payload = call_external_tool_payload(
        "load_conversation_messages",
        {"conversation_id": conversation_id},
    )
    return json_payload(payload)


@tool(args_schema=ExtractSchedulesFromHistoryInput)
def extract_schedules_from_history(member_names: list[str], date_from: str, date_to: str) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다."""

    # 날짜 형식 정리도 외부 store 경계에서 한 번만 처리하므로 wrapper에서 중복 변환하지 않습니다.
    return call_mcp_tool_sync(
        "extract_schedules_from_history",
        {"member_names": member_names, "date_from": date_from, "date_to": date_to},
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
    """외부 MCP 공유 일정 저장소에 다른 멤버의 일정을 등록하거나 갱신합니다.

    내 앱 일정을 만들 때는 이 tool이 아니라 personal_create_schedule을 사용합니다.
    같은 schedule_id로 다시 호출하면 갱신되고, source_conversation_id를 넘기면
    나중에 앱 원본 기준으로 삭제/동기화할 수 있습니다.
    """

    # MCP tool이 ok/tool_name/shared_schedule JSON 문자열을 만들어 주므로 다시 감싸지 않습니다.
    # 멤버 이름/제목/날짜 정규화도 외부 store 경계에서 처리하므로 wrapper에서 중복 변환하지 않습니다.
    return call_mcp_tool_sync(
        "create_shared_schedule",
        {
            "member_name": member_name,
            "title": title,
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "notes": notes,
            "source_conversation_id": source_conversation_id,
            "schedule_id": schedule_id,
        },
    )


@tool(args_schema=DeleteSharedScheduleInput)
def delete_shared_schedule(
    schedule_id: str | None = None,
    source_conversation_id: str | None = None,
) -> str:
    """외부 MCP 공유 일정 저장소에서 schedule_id 또는 source_conversation_id로 일정을 삭제합니다.

    두 값 중 하나는 반드시 필요합니다. 무엇을 지울지 모르면 먼저 list_shared_schedules로
    대상 row의 schedule_id를 확인한 뒤 호출합니다.
    """

    # 조건 없이 호출되면 store는 아무것도 지우지 않고 빈 목록만 돌려줍니다. 그대로 전달하면
    # deleted_count=0 + ok=True가 되어 "삭제 성공"처럼 읽히므로 Week 3 삭제 가드와 같은 방식으로
    # 여기서 ok=False로 끊습니다.
    if not schedule_id and not source_conversation_id:
        return json_payload(
            {
                "ok": False,
                "tool_name": "delete_shared_schedule",
                "deleted_count": 0,
                "error": "schedule_id 또는 source_conversation_id 중 하나는 반드시 필요합니다.",
                "hint": "list_shared_schedules로 대상 row의 schedule_id를 먼저 확인하세요.",
            }
        )

    return call_mcp_tool_sync(
        "delete_shared_schedule",
        {"schedule_id": schedule_id, "source_conversation_id": source_conversation_id},
    )


@tool(args_schema=ListSharedSchedulesInput)
def list_shared_schedules(
    member_names: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source_conversation_id: str | None = None,
    limit: int = 50,
) -> str:
    """외부 MCP 공유 일정 저장소에 등록된 일정을 조회합니다. 필터가 없으면 기본 공유 일정을 반환합니다."""

    # member_names=None(전체)과 []("멤버를 지정했는데 유효한 이름이 없음")은 store에서 뜻이 다르므로
    # 빈 list를 None으로 바꾸지 않고 받은 값을 그대로 넘깁니다.
    return call_mcp_tool_sync(
        "list_shared_schedules",
        {
            "member_names": member_names,
            "date_from": date_from,
            "date_to": date_to,
            "source_conversation_id": source_conversation_id,
            "limit": limit,
        },
    )


@tool(args_schema=CollectMemberSchedulesInput)
def collect_member_schedules(member_names: list[str], date_from: str, date_to: str) -> str:
    """내 일정과 다른 사람들의 일정을 MCP SQLite 기록에서 모읍니다.

    내 일정(앱 SQLite + 현재 대화의 임시 일정)은 항상 member_name "나" row로 이미 포함되므로,
    member_names에는 나를 제외한 다른 멤버 이름만 넣고 호출 뒤에 내 일정 조회 tool을 따로 부르지 않습니다.
    """

    collected = _collect_member_schedules(
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        personal_schedules=_personal_schedules_for_current_scope(),
    )
    return json_payload({"ok": True, "tool_name": "collect_member_schedules", **collected})


def _message_kind(message: Any) -> str:
    """message 객체와 dict 두 모양 모두에서 human/ai/tool 구분값을 읽습니다."""

    if isinstance(message, dict):
        return str(message.get("type") or message.get("role") or "")
    return str(getattr(message, "type", "") or "")


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _messages_in_current_turn(state: Any) -> list[Any]:
    """마지막 사용자 발화 이후의 message만 남깁니다.

    같은 턴 안의 중복 호출만 걸러내기 위한 범위 제한입니다. 대화 전체를 보면
    이전 턴의 collect_member_schedules 때문에 다음 턴의 정상적인 "내 일정 보여줘"까지
    막히므로, 턴 경계를 기준으로 판단합니다.
    """

    if isinstance(state, dict):
        messages = normalize_messages_value(state.get("messages"))
    else:
        messages = normalize_messages_value(getattr(state, "messages", None))
    for index in range(len(messages) - 1, -1, -1):
        if _message_kind(messages[index]) in {"human", "user"}:
            return messages[index + 1 :]
    return messages


def _collected_personal_rows_in_current_turn(state: Any) -> list[dict[str, Any]] | None:
    """이번 턴에 collect_member_schedules가 이미 반환한 "나" row를 찾습니다.

    호출 기록이 없으면 None을 돌려주고, 그때는 원래 tool을 정상 실행합니다.
    """

    for message in reversed(_messages_in_current_turn(state)):
        if _message_kind(message) != "tool":
            continue
        try:
            payload = json.loads(_message_text(message))
        except (TypeError, ValueError):
            continue
        # tool 이름은 message.name이 비어 오는 구현도 있어서 payload의 tool_name까지 함께 봅니다.
        if not isinstance(payload, dict) or payload.get("tool_name") != COLLECT_MEMBER_SCHEDULES_TOOL_NAME:
            continue
        rows = payload.get("rows") or []
        return [
            row
            for row in rows
            if isinstance(row, dict) and row.get("member_name") == PERSONAL_SHARED_MEMBER_NAME
        ]
    return None


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls")
    else:
        tool_calls = getattr(message, "tool_calls", None)
    return [call for call in (tool_calls or []) if isinstance(call, dict)]


def _requested_with_collect_in_same_response(state: Any, tool_call_id: str) -> bool:
    """이 tool call을 낸 AI message가 collect_member_schedules도 같이 호출했는지 봅니다.

    모델이 한 응답에서 여러 tool을 병렬로 호출하면 형제 tool의 ToolMessage가 아직 state에
    없어서 위 결과 기반 판단이 통하지 않습니다. 그때는 호출을 지시한 쪽을 보고 걸러냅니다.
    """

    if not tool_call_id:
        return False
    for message in reversed(_messages_in_current_turn(state)):
        tool_calls = _message_tool_calls(message)
        if not tool_calls:
            continue
        if tool_call_id not in {str(call.get("id") or "") for call in tool_calls}:
            continue
        return any(call.get("name") == COLLECT_MEMBER_SCHEDULES_TOOL_NAME for call in tool_calls)
    return False


@wrap_tool_call
def skip_personal_list_already_collected(request: Any, handler: Any) -> Any:
    """collect_member_schedules 직후의 중복 내 일정 조회를 코드 레벨에서 차단합니다.

    prompt와 docstring은 모델이 따를 수도, 무시할 수도 있는 soft constraint라
    같은 턴에서 personal_list_schedules / personal_list_saved_schedules가 다시 호출되는 일을
    막지 못합니다. 이 middleware는 tool 실행 직전에 끼어들어 SQLite/임시 일정을 다시 읽지 않고
    "이미 collect_member_schedules 결과에 들어 있다"는 ToolMessage로 대신 응답합니다.
    """

    tool_name = str(request.tool_call.get("name") or "")
    if tool_name not in PERSONAL_LIST_TOOL_NAMES_COVERED_BY_COLLECT:
        return handler(request)

    tool_call_id = str(request.tool_call.get("id") or "")
    skipped_payload: dict[str, Any] = {
        "ok": True,
        "tool_name": tool_name,
        "skipped": True,
    }

    already_collected_rows = _collected_personal_rows_in_current_turn(request.state)
    if already_collected_rows is not None:
        skipped_payload["skipped_reason"] = (
            "이번 턴의 collect_member_schedules 결과에 내 일정이 이미 "
            f'member_name "{PERSONAL_SHARED_MEMBER_NAME}" row로 들어 있어 다시 조회하지 않았습니다. '
            "아래 already_collected_rows를 그대로 근거로 답하세요."
        )
        skipped_payload["already_collected_rows"] = already_collected_rows
        skipped_payload["row_count"] = len(already_collected_rows)
    elif _requested_with_collect_in_same_response(request.state, tool_call_id):
        skipped_payload["skipped_reason"] = (
            "같은 응답에서 collect_member_schedules를 함께 호출했고 그 결과에 내 일정이 "
            f'member_name "{PERSONAL_SHARED_MEMBER_NAME}" row로 포함되므로 이 조회는 생략했습니다. '
            "collect_member_schedules 결과 rows를 근거로 답하세요."
        )
    else:
        # 이번 턴에 collect_member_schedules를 부른 적이 없으면 평소대로 조회합니다.
        return handler(request)

    return ToolMessage(
        content=json_payload(skipped_payload),
        tool_call_id=tool_call_id,
        name=tool_name,
    )


def week05_middleware() -> list[Any]:
    """Week 5 agent에 붙는 tool 호출 가드 목록입니다."""

    return [skip_personal_list_already_collected]


def week05_tools() -> list[Any]:
    """4주차까지의 도구에 외부 SQLite/MCP 일정 도구를 누적한 목록입니다."""

    # 구현이 끝난 tool만 노출합니다(Week 3 "완성 tool만 노출" 규칙 계승).
    #   - search_previous_conversations / load_conversation_messages : 외부 이전 대화 검색·로드
    #   - extract_schedules_from_history                             : 외부 멤버 busy-time 추출
    #   - list_shared_schedules                                      : 공유 일정 저장소 row 조회
    #   - collect_member_schedules                                   : 내 일정 + 외부 busy-time 통합
    #   - create_shared_schedule / delete_shared_schedule            : 공유 저장소 row 등록·삭제(심화)
    return [
        *week04_tools(),
        search_previous_conversations,
        load_conversation_messages,
        extract_schedules_from_history,
        list_shared_schedules,
        collect_member_schedules,
        create_shared_schedule,
        delete_shared_schedule,
    ]


def week05_system_prompt() -> str:
    """5주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week05_prompt_parts())


def week05_prompt_parts() -> list[str]:
    """1~5주차 system prompt 조각을 누적합니다."""

    return [
        *week04_prompt_parts(),
        (
            "Week 5에서 Nana는 내 기억과 다른 사람의 기억을 다른 저장소에서 찾는다. "
            "내가 남긴 참고자료·저장 기록·앱 대화는 지금까지의 Week 3-4 도구로 찾고, "
            "철수·영희처럼 나 아닌 멤버의 과거 대화와 일정은 외부 SQLite/MCP 도구로 찾는다. "
            "'철수가 저번에 뭐라고 했지'처럼 외부 멤버의 발언을 물으면 "
            "search_previous_conversations로 검색하고, 특정 대화 전체 흐름이 필요하면 "
            "거기서 얻은 conversation_id로 load_conversation_messages를 호출한다. "
            "search_previous_conversations의 query에는 문장 전체가 아니라 '일정', 'QA 리뷰'처럼 "
            "짧은 핵심 명사나 구를 넣는다. 서버는 조사나 불용어를 걸러 주지 않는다. "
            "'철수가'처럼 멤버를 특정한 질문이면 member_names에 그 이름을 넣어 그 사람 대화만 검색하고, "
            "누구인지 지정하지 않은 질문일 때만 member_names를 비워 전체 멤버를 검색한다."
        ),
        (
            "특정 멤버가 언제 바쁜지 물으면 extract_schedules_from_history로 그 멤버의 busy-time을 조회한다. "
            "나까지 포함해 여러 사람의 일정을 한 번에 모아야 하면 collect_member_schedules를 사용한다. "
            "이 tool은 내 앱 일정(SQLite + 현재 대화의 임시 일정)과 외부 멤버 일정을 "
            "member_name/title/date/start_time/end_time/notes 같은 구조의 rows로 합쳐 주므로, "
            "내 일정과 남의 일정을 각각 다른 tool로 따로 모으지 않는다. "
            "내 일정 row의 member_name은 '나'로 표시된다. "
            "collect_member_schedules 결과에는 내 일정이 이미 들어 있으므로 "
            "그 뒤에 personal_list_schedules나 personal_list_saved_schedules를 다시 호출하지 않는다. "
            "같은 턴에서 굳이 다시 호출하면 tool 결과가 skipped=true와 already_collected_rows로 돌아오므로, "
            "그때는 다시 조회를 시도하지 말고 already_collected_rows를 근거로 답한다. "
            "공유 일정 저장소에 실제로 어떤 row가 등록돼 있는지 확인할 때만 list_shared_schedules를 쓴다. "
            "이 tool은 멤버나 날짜 필터 없이도 바로 호출할 수 있고 그때는 기본 공유 일정을 돌려주므로, "
            "'등록된 공유 일정 보여줘'처럼 조건 없는 요청에도 사용자에게 조건을 되묻지 말고 먼저 호출한다."
        ),
        (
            "외부 멤버 일정은 rows와 schedule_summary에 실제로 담긴 내용만 근거로 삼고, "
            "조회되지 않은 시간대를 비어 있다고 단정하지 않는다. "
            "여러 사람이 모두 가능한 회의 시간을 확정하는 일은 아직 Nana의 역할이 아니므로, "
            "요청받으면 모은 일정 rows를 근거로 바쁜 시간만 정리해 주고 최종 확정은 사용자에게 맡긴다."
        ),
        (
            "공유 일정 저장소 row를 직접 손봐야 할 때는 create_shared_schedule과 delete_shared_schedule을 쓴다. "
            "'철수 회의 일정도 공유 일정에 등록해 줘'처럼 나 아닌 멤버의 일정을 공유 저장소에 넣을 때 "
            "create_shared_schedule을 호출하고, 같은 일정을 고칠 때는 조회로 얻은 schedule_id를 그대로 넘겨 갱신한다. "
            "반대로 내 앱 일정을 새로 만드는 요청은 personal_create_schedule로 처리한다. "
            "내 일정은 앱 DB가 원본이고 공유 저장소 '나' row는 그 동기화 복사본이라, "
            "create_shared_schedule로 만들면 앱 DB에는 남지 않는다. "
            "delete_shared_schedule은 schedule_id나 source_conversation_id 중 하나가 반드시 필요하므로 "
            "무엇을 지울지 모르면 먼저 list_shared_schedules로 대상 row를 확인한 뒤 그 schedule_id로 호출하고, "
            "조건 없이 호출해 ok=false가 돌아오면 사용자에게 어떤 일정인지 되묻는다."
        ),
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
            # prompt로 막지 못하는 중복 내 일정 조회를 tool 실행 직전에 차단합니다.
            middleware=week05_middleware(),
        )
    return _WEEK05_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week05_agent()
