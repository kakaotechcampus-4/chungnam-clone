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
    PERSONAL_SHARED_MEMBER_NAME,
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
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts, week04_tools


_WEEK05_AGENT: Any | None = None

# list_schedules의 기본 limit은 12이고 클램프 없이 SQL LIMIT으로 들어가, 맡겨두면 조용히 잘린다.
PERSONAL_SCHEDULE_FETCH_LIMIT = 200


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


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다."""

    store = AppSQLiteStore(CONFIG.app_db_path)

    # kind를 주지 않아 그룹 일정까지 가져온다. 그룹 회의도 내가 바쁜 시간이다.
    saved_schedules = store.list_schedules(limit=PERSONAL_SCHEDULE_FETCH_LIMIT)

    # 두 출처가 id를 담는 키 이름이 다르다. 앱 row는 schedule_id, Week 1 임시 일정은 id다.
    saved_ids = {schedule.get("schedule_id") for schedule in saved_schedules}
    current_scope = current_session_scope()
    pending_schedules = [
        schedule
        for schedule in PERSONAL_SCHEDULES
        if _schedule_scope(schedule) == current_scope and schedule.get("id") not in saved_ids
    ]

    return [*saved_schedules, *pending_schedules]


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

    # 정규화는 외부에 보낼 인자를 직접 조립하는 이 함수에서 한다. wrapper는 받은 값을 전달만 한다.
    normalized_members = normalize_external_member_names(member_names)
    window_from, window_to = normalize_external_schedule_date_bounds(member_names, date_from, date_to)

    external_payload = json.loads(
        call_mcp_tool_sync(
            "extract_schedules_from_history",
            {"member_names": normalized_members, "date_from": window_from, "date_to": window_to},
        )
    )
    external_rows = external_payload.get("rows") or []

    # 구간 밖이거나 날짜가 없는 내 일정은 제외한다. 외부 쪽은 서버가 이미 구간으로 걸러 주므로,
    # 내 일정만 전부 넣으면 7월을 물었는데 8월 일정이 바쁜 시간으로 섞인다.
    has_window = bool(window_from and window_to)
    personal_rows = [
        {
            "member_name": PERSONAL_SHARED_MEMBER_NAME,
            "title": schedule.get("title"),
            "date": schedule.get("date"),
            "start_time": schedule.get("start_time"),
            "end_time": schedule.get("end_time"),
            "notes": schedule.get("notes"),
        }
        for schedule in personal_schedules
        if not has_window or (schedule.get("date") and window_from <= str(schedule["date"]) <= window_to)
    ]

    # 서버는 외부 멤버만 정렬해 주므로 두 출처를 섞은 순서는 여기서 정한다.
    rows = [*personal_rows, *external_rows]
    rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("start_time") or "")))

    # 서버가 준 schedule_summary는 외부 멤버만 기준이라, 내 일정까지 합친 rows로 다시 만든다.
    return {"rows": rows, "schedule_summary": external_schedule_summary(rows)}


@tool(args_schema=SearchPreviousConversationsInput)
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> str:
    """외부 SQLite 데이터베이스에 저장된 이전 대화를 검색합니다.

    query는 대화 문장에 그대로 들어 있는 문자열과 부분 일치로만 대조합니다. 그래서 명사 하나만 넣어야 하고,
    여러 단어를 이어 붙이거나 사용자가 덧붙인 말('준비', '일정' 등)을 함께 넣으면 0건이 됩니다.
    멤버 이름은 query가 아니라 member_names에 넣습니다.
    member_names를 생략하면 전체 멤버에서 찾습니다. 빈 목록은 '대상 멤버가 없다'는 뜻이라 0건이 되므로,
    누구인지 모를 때는 빈 목록을 넣지 말고 생략합니다."""

    # 외부 저장소는 member_names의 None과 []를 다르게 읽는다. None은 "필터 없음"이라 전체를 찾고,
    # []는 "대상 멤버 없음"이라 0건이다. 그래서 member_names or [] 같은 기본값을 채우지 않는다.
    args = {"query": query, "member_names": member_names, "limit": limit}
    return call_mcp_tool_sync("search_previous_conversations", args)


@tool(args_schema=LoadConversationMessagesInput)
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다."""

    # call_external_tool_payload는 json.loads까지 해서 dict를 준다. 그래서 이 tool만 다시 감싼다.
    payload = call_external_tool_payload("load_conversation_messages", {"conversation_id": conversation_id})

    # 대화는 순서가 곧 맥락이라 재정렬하거나 필드를 골라내지 않는다.
    return json_payload(payload)


@tool(args_schema=ExtractSchedulesFromHistoryInput)
def extract_schedules_from_history(member_names: list[str], date_from: str, date_to: str) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다.

    member_names는 필수입니다. 빈 목록은 '대상 멤버가 없다'는 뜻이라 0건이 되므로, 누구의 일정인지
    정해지지 않았다면 이 tool을 부르기 전에 search_previous_conversations로 대상을 먼저 확인합니다."""

    args = {"member_names": member_names, "date_from": date_from, "date_to": date_to}
    return call_mcp_tool_sync("extract_schedules_from_history", args)


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

    나중에 이 일정을 찾아 수정·삭제할 수 있도록 source_conversation_id에 출처를 함께 남깁니다.
    같은 schedule_id로 다시 등록하면 새로 만들지 않고 갱신하므로, 재시도해도 일정이 중복되지 않습니다."""

    # schedule_id가 같으면 저장소가 새로 만들지 않고 갱신한다. 비워 보내면 재시도마다 새 row가 쌓인다.
    args = {
        "member_name": member_name,
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "notes": notes,
        "source_conversation_id": source_conversation_id,
        "schedule_id": schedule_id,
    }

    return call_mcp_tool_sync("create_shared_schedule", args)


@tool(args_schema=DeleteSharedScheduleInput)
def delete_shared_schedule(
    schedule_id: str | None = None,
    source_conversation_id: str | None = None,
) -> str:
    """외부 MCP 공유 일정 저장소에서 일정을 삭제합니다."""

    # 두 조건은 AND가 아니라 OR다. 둘을 함께 주면 어느 한쪽이라도 맞는 row가 모두 지워진다.
    # 참석자별로 여러 row가 저장된 그룹 일정을 source_conversation_id 하나로 정리하기 위한 설계다.
    args = {"schedule_id": schedule_id, "source_conversation_id": source_conversation_id}
    return call_mcp_tool_sync("delete_shared_schedule", args)


@tool(args_schema=ListSharedSchedulesInput)
def list_shared_schedules(
    member_names: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source_conversation_id: str | None = None,
    limit: int = 50,
) -> str:
    """외부 MCP 공유 일정 저장소에 등록된 일정을 조회합니다.

    필터를 하나도 넘기지 않으면 기본 실습 멤버와 기본 날짜 구간으로 대체해 돌려줍니다. 그러므로
    "등록된 일정 보여줘"처럼 조건이 없는 질문에는 날짜를 임의로 채우지 말고 그대로 비워 호출합니다.
    member_names를 생략하면 전체 멤버를, 빈 목록을 넘기면 0건을 돌려줍니다."""

    # 필터가 하나도 없으면 저장소가 기본 실습 멤버·기본 구간으로 대체한다. wrapper가 값을 채우면
    # 그 분기가 사라져, 조건 없이 "공유 일정 보여줘"라고 물었을 때 결과가 비어버린다.
    args = {
        "member_names": member_names,
        "date_from": date_from,
        "date_to": date_to,
        "source_conversation_id": source_conversation_id,
        "limit": limit,
    }

    return call_mcp_tool_sync("list_shared_schedules", args)


@tool(args_schema=CollectMemberSchedulesInput)
def collect_member_schedules(member_names: list[str], date_from: str, date_to: str) -> str:
    """내 일정과 다른 사람들의 일정을 한 번에 모읍니다.

    member_names에는 외부 멤버 이름만 넣습니다. 내 일정은 항상 자동으로 포함되므로 '나'나 비서 이름을
    넣지 않고, 나를 위해 따로 호출하거나 빈 목록을 넘기지 않습니다.
    질문 하나에 한 번만 호출합니다. 여러 멤버를 물어도 이름을 배열에 모두 넣어 한 번에 처리합니다.
    '각각'이나 '따로'는 답변을 나눠 적으라는 뜻이지 호출을 나누라는 뜻이 아닙니다."""

    merged = _collect_member_schedules(
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
        personal_schedules=_personal_schedules_for_current_scope(),
    )

    return json_payload(merged)


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

    today = current_app_date_iso()

    # 규칙 번호는 4주차의 ①~⑧에 이어 ⑨부터 쓴다.
    return [
        *week04_prompt_parts(),
        (
            f"너는 5주차부터 외부 기록까지 불러오는 카나이기도 하다. 오늘은 {today}이다. "
            "다른 멤버의 지난 대화와 공유 일정은 이 앱이 아니라 외부 MCP 서버가 가지고 있어서 "
            "MCP tool을 호출해야만 볼 수 있다. 호출하지 않고 아는 것처럼 답하지 않는다. "
            "⑨ 출처를 먼저 구분한다. 내가 사용자와 나눈 지난 대화는 search_conversation_messages로 찾고, "
            "다른 멤버(팀원)의 지난 대화나 일정은 search_previous_conversations와 "
            "extract_schedules_from_history로 찾는다. 두 이름이 비슷하므로 누구의 대화인지 매번 확인한다. "
            "사용자가 ext_로 시작하는 대화 id를 대면 그것은 외부 멤버의 대화이므로 "
            "load_conversation_messages로 조회한다. search_conversation_messages는 앱에 저장된 내 대화만 "
            "찾으므로, 그 결과를 외부 대화의 내용인 것처럼 옮겨 적지 않는다. "
            "사용자가 특정 대화 id를 지정했는데 검색 결과의 conversation_id가 그 id와 다르면 그 결과는 답이 "
            "아니다. 다른 대화를 요약해 보여주지 말고 그 id의 대화를 찾지 못했다고 답한다. "
            "⑩ 외부 멤버 정보는 search_previous_conversations로 관련 대화를 찾고, 그 결과의 "
            "conversation_id를 모아 extract_schedules_from_history로 일정을 확인하는 순서를 권장한다. "
            "사용자가 원문·전문·'그대로'를 요구하면 먼저 search_previous_conversations로 그 대화의 "
            "conversation_id를 찾고, 검색 결과에 있던 그 id로 load_conversation_messages를 불러 답한다. "
            "검색 결과의 content로 대신하지 않는다. conversation_id를 지어내서 부르지 않는다. "
            "search_previous_conversations의 query에는 사용자가 덧붙인 말을 빼고 핵심 명사 하나만 넣는다. "
            "검색이 0건이면 없다고 결론짓기 전에 더 짧은 키워드로 한 번 더 찾아보고, 그래도 0건이면 "
            "list_shared_schedules로 같은 멤버·날짜를 조회해 확인한 뒤 결론을 낸다. "
            "⑪ '우리 언제 만날 수 있어?'처럼 내 일정과 여러 멤버의 일정을 함께 봐야 하는 질문은 "
            "collect_member_schedules로 모은다. 결과의 rows에는 나와 외부 멤버가 같은 형태로 "
            "들어 있고 schedule_summary가 함께 오므로, 그 내용을 근거로 답한다. "
            "⑫ 공유 일정 저장소에 실제로 등록된 내용을 확인할 때는 list_shared_schedules를 쓴다. "
            "등록과 갱신은 create_shared_schedule, 취소는 delete_shared_schedule을 쓴다. "
            "⑬ 지난 대화에서 누군가 한 말은 그 시점의 진술이지 확정된 일정이 아니다. 대화에서 얻은 일정을 "
            "'확정됐다'고 말하지 않는다. '대화에서 언급된 내용'이라고 밝히고, 확정 여부를 묻는 질문에는 "
            "list_shared_schedules로 공유 저장소를 확인한 뒤 답한다. 근거가 어느 출처에서 나왔는지 함께 적는다. "
            "⑭ 조회 구간(date_from·date_to)은 사용자가 말한 날짜를 그대로 쓰고, 상대 표현이면 오늘 기준으로 "
            "환산해 어떤 구간을 확인했는지 답변에 적는다. 날짜를 알 수 없으면 추측하지 말고 되묻는다. "
            "⑮ 여러 사람이 모두 가능한 최종 회의 시간을 확정하는 일은 다음 주차 범위다. 이번 주에는 "
            "누가 언제 바쁜지와 그 근거를 정리해 후보까지만 제시한다. "
            "⑯ 일정·대화·저장 기록을 묻는 질문에는 반드시 해당 tool을 호출해 확인한 뒤 답한다. "
            "앞선 답변이나 이미 받은 결과를 근거로 새 질문에 답하지 않는다. 날짜나 대상이 조금이라도 "
            "달라지면 다시 조회한다. 조회 없이 '없습니다'라고 단정하지 않는다. "
            "⑰ 대상을 지정하지 않은 질문(예: '그날 바쁜 사람 있어?')은 앞 대화에 나온 멤버로 좁히지 않는다. "
            "member_names를 넘기지 말고 list_shared_schedules로 전체를 조회한 뒤 답한다. "
            "⑱ create_shared_schedule 결과의 schedule_id는 이후 삭제·수정에 그대로 쓴다. 방금 등록한 일정을 "
            "지워 달라는 요청에 id를 사용자에게 되묻지 않는다."
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
        )
    return _WEEK05_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week05_agent()
