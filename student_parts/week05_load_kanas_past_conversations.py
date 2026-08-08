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
    strip_parenthetical_text,
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
#      - 저장된 내 일정은 kind로 거르지 않습니다. 개인 일정과 그룹 일정 모두 "나" row로 합칩니다.
#        그룹 일정은 저장 시 참석자별 공유 row로도 동기화되지만, 이번 조율 대상에 그 참석자가
#        없으면 외부 조회로는 잡히지 않아 앱 DB에서 읽지 않으면 내 바쁜 시간에서 통째로 빠집니다.
#      - 외부 멤버 일정은 call_mcp_tool_sync("extract_schedules_from_history", args) 결과를 이 tool 안에서 읽습니다.
#      - 두 출처를 member_name/title/date/start_time/end_time/notes가 있는 rows 배열로 직접 합칩니다.
#      - 합친 rows는 (member_name, date, start_time, 제목) 기준으로 한 번 중복을 제거합니다.
#        member_names에 "나"가 들어오면 같은 일정이 앱 DB와 공유 저장소 양쪽에서 들어오기 때문입니다.
#        두 경로가 같은 일정을 다르게 다듬으므로 값을 그대로 비교하면 안 됩니다. 제목은 소괄호와
#        공백을 정리해서 비교하고, end_time은 앱 DB 경로만 "미정"을 "18:00"으로 바꾸므로 키에서 뺍니다.
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
#     이번 조율 대상에 없는 멤버와 잡아둔 그룹 일정도 "나" row로 들어오는지 함께 확인합니다.
#   - 추가 과제: create_shared_schedule로 등록한 row가 list_shared_schedules 조회에 나타나고
#     delete_shared_schedule로 삭제되는지 확인합니다.
#
# 함수별 동작 설명 ([메인]/[추가]/[공통]은 각 함수가 속한 과제 티어입니다)
#   - [메인] _schedule_scope(schedule)
#     Week 1 임시 일정이 어느 대화 범위에 속하는지 읽습니다. session_id가 없으면 기본 scope로 처리합니다.
#
#   - [메인] _personal_schedules_for_current_scope()
#     Week 3 이후 SQLite에 저장된 내 일정(개인/그룹)과 현재 대화에만 남아 있는 Week 1 임시 일정을 합칩니다.
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
#     kind는 SQLite row의 request_kind에서 읽고, 그 값이 없는 Week 1 임시 일정은 개인 일정으로 봅니다.
#     뒤에서 내 일정 row를 외부 멤버 row와 같은 구조로 맞출 때 사용합니다.
#
#   - [메인] _my_schedule_notes(request)
#     내 일정 row의 notes를 개인 일정과 그룹 일정으로 구분해 만듭니다.
#     그룹 일정은 참석자를 함께 적어 LLM이 어떤 회의로 시간이 찼는지 설명할 수 있게 합니다.
#
#   - [메인] _dedupe_schedule_rows(rows)
#     앱 DB 경로와 외부 공유 저장소 경로로 같은 일정이 두 번 들어오는 경우를 한 번만 남깁니다.
#     공유 저장소가 제목을 다듬고 앱 DB 경로만 end_time을 채워 넣는 차이를 비교 키에서 흡수합니다.
#     앞에 오는 앱 DB row를 남기므로 my_rows를 external rows보다 먼저 두어야 합니다.
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

def _dedupe_schedule_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 일정이 앱 DB와 공유 저장소 양쪽에서 들어와도 한 번만 남깁니다.

    앱 DB에 저장된 내 일정은 공유 저장소에도 자동 동기화되므로, member_names에 "나"가
    들어온 호출에서는 같은 일정이 두 경로로 들어옵니다. 앞에 오는 앱 DB row를 남깁니다.

    두 경로가 같은 일정을 서로 다르게 다듬기 때문에 값을 그대로 비교하면 안 됩니다.
      - 공유 저장소는 제목에서 소괄호를 지우고 공백을 하나로 줄입니다. 앱 DB는 원문을 둡니다.
      - 앱 DB 경로만 end_time "미정"을 "18:00"으로 바꿉니다. 그래서 end_time은 키에서 뺍니다.
        같은 사람이 같은 날 같은 시각에 시작하는 같은 제목의 일정은 하나로 봅니다.
      - start_time이 비어 있으면 공유 저장소는 "미정"으로 저장하므로 같은 값으로 맞춥니다.
    """

    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("member_name") or "").strip(),
            str(row.get("date") or "").strip(),
            str(row.get("start_time") or "").strip() or "미정",
            strip_parenthetical_text(str(row.get("title") or "")),
        )
        deduped.setdefault(key, row)
    return list(deduped.values())

def _my_schedule_notes(request: StructuredRequest) -> str:
    """내 일정 row가 개인 일정인지, 참석자가 있는 그룹 일정인지 설명합니다."""

    if request.kind != "group_schedule":
        return "Nana 개인 일정"
    members = [str(member).strip() for member in (request.members or []) if str(member).strip()]
    return f"Nana 그룹 일정 · 참석자: {', '.join(members)}" if members else "Nana 그룹 일정"

def _schedule_scope(schedule: dict[str, Any]) -> str:
    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite에 저장된 내 일정(개인/그룹)과 현재 대화의 임시 일정을 group 조율 후보로 사용합니다.

    그룹 일정도 owner가 '나'인 내 일정이므로 kind로 거르지 않습니다. 조율 대상에 없는
    멤버와 잡아둔 그룹 일정은 외부 공유 저장소 조회 경로로는 잡히지 않기 때문입니다.
    """

    db_schedules = AppSQLiteStore(CONFIG.app_db_path).list_schedules(limit=200)
    seen_ids = {str(schedule.get("schedule_id")) for schedule in db_schedules if schedule.get("schedule_id")}
    session_id = current_session_scope()
    current_memory_schedules = [
        schedule
        for schedule in PERSONAL_SCHEDULES
        if _schedule_scope(schedule) == session_id and str(schedule.get("id")) not in seen_ids
    ]
    return [*db_schedules, *current_memory_schedules]


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
        kind="group_schedule" if row.get("request_kind") == "group_schedule" else "personal_schedule",
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

    normalized_members = normalize_external_member_names(member_names)
    normalized_date_from, normalized_date_to = normalize_external_schedule_date_bounds(
        normalized_members,
        date_from,
        date_to,
    )
    my_rows: list[dict[str, Any]] = []
    for row in personal_schedules:
        request = _structured_request_from_schedule_row(row)
        schedule_date = request.date
        if not schedule_date:
            continue
        if normalized_date_from and schedule_date < normalized_date_from:
            continue
        if normalized_date_to and schedule_date > normalized_date_to:
            continue
        end_time = row.get("end_time")
        my_rows.append(
            {
                "member_name": "나",
                "title": request.title,
                "date": schedule_date,
                "start_time": request.start_time,
                "end_time": request.end_time if end_time != "미정" else "18:00",
                "notes": _my_schedule_notes(request),
            }
        )

    external_payload = {"rows": []}
    if normalized_members:
        external_payload = json.loads(
            call_mcp_tool_sync(
                "extract_schedules_from_history",
                {
                    "member_names": normalized_members,
                    "date_from": normalized_date_from,
                    "date_to": normalized_date_to,
                },
            )
        )
    rows = _dedupe_schedule_rows([*my_rows, *external_payload.get("rows", [])])
    return {
        "ok": True,
        "tool_name": "collect_member_schedules",
        "members": ["나", *[name for name in normalized_members if name != "나"]],
        "rows": rows,
        "schedule_summary": external_schedule_summary(rows),
    }


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

    safe = safe_limit(limit, default=50, maximum=200)
    return call_mcp_tool_sync(
        "list_shared_schedules",
        {
            "member_names": member_names,
            "date_from": date_from,
            "date_to": date_to,
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

    personal_schedules = _personal_schedules_for_current_scope()
    return json_payload(
        _collect_member_schedules(
            member_names=member_names,
            date_from=date_from,
            date_to=date_to,
            personal_schedules=personal_schedules,
        )
    )


def week05_tools() -> list[Any]:
    """4주차까지의 도구에 외부 SQLite/MCP 일정 도구를 누적한 목록입니다."""

    return [
        *week04_tools(),
        search_previous_conversations,
        load_conversation_messages,
        extract_schedules_from_history,
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
        """개인적인 참고자료 검색·등록이나 저장된 일정/할 일/알림 조회는 Week 4까지의 tool로 계속
처리한다. 이번 주차에서 새로 바뀌지 않는다.""",
        """이번 주차부터는 외부 저장소에 있는 다른 멤버들의 이전 대화와, 그와는 별도로 관리되는 공유
일정 저장소를 함께 다룬다. 다섯 개 tool은 서로 다른 데이터 출처를 겨냥하므로, 요청이 어떤 출처를
묻는지 먼저 구분한 뒤 그에 맞는 tool 하나를 고른다.
- 다른 멤버와 나눈 '대화 내용'을 찾거나 확인해야 하면 search_previous_conversations /
  load_conversation_messages를 쓴다.
- 그 대화에서 '일정·바쁜 시간'을 뽑아내야 하면 extract_schedules_from_history를 쓴다.
- 대화와 별개로 미리 등록해 둔 '공유 일정 저장소' 자체를 확인해야 하면 list_shared_schedules를
  쓴다.
- 나를 포함해 여러 사람의 일정·바쁜 시간을 한 번에 모아야 하면 collect_member_schedules를 쓴다.""",
        """search_previous_conversations는 query/member_names/limit 조건으로 외부 멤버와의 이전
대화를 검색한다. 멤버 이름 정규화는 이 tool을 감싸는 외부 경계에서 한 번만 처리되므로, 여기서
이름을 다시 정리하거나 걸러내지 않고 요청에서 언급된 이름을 그대로 넘긴다. 검색 결과로 관련
대화의 conversation_id를 확인한 뒤, 그 대화 전체 내용을 봐야 하면 load_conversation_messages로
이어간다.""",
        """load_conversation_messages는 search_previous_conversations로 찾은 conversation_id 하나의
전체 메시지를 조회할 때 쓴다. 반환되는 발신자·내용·작성 시각의 순서는 그대로 보존된 것이므로
재정렬하거나 임의로 가공하지 않는다.""",
        """extract_schedules_from_history는 member_names/date_from/date_to 조건으로 외부 멤버의
지난 대화에서 일정·바쁜 시간을 추출한다. 날짜 형식 정규화는 외부 경계에서 한 번만 처리되므로,
정확한 시작/끝 날짜를 모른다고 임의의 날짜 값을 만들어 채우지 않고 모르는 값은 비워 둔다. 이
tool은 대화에서 일정을 뽑아내는 용도이며, 그 일정이 공유 일정 저장소에 등록돼 있는지 확인하는
것은 list_shared_schedules의 역할이다.""",
        """list_shared_schedules는 대화 내용과 별개로 이미 등록된 공유 일정 저장소의 row를 조회할
때 쓴다. "나"를 포함한 row도 조회 대상이며, 나를 제외해야만 쓸 수 있는 tool이 아니다.
member_names/date_from/date_to 등 필터를 전혀 주지 않고 호출하면 저장소가 기본 공유 일정을
우선 채워 반환할 수 있다는 점을 감안해 응답한다. 대화에서 언급된 일정의 세부 내용을 묻는
맥락에는 이 tool 대신 extract_schedules_from_history를 쓴다.""",
        """collect_member_schedules는 나를 포함해 여러 멤버의 일정·바쁜 시간을 한 번에 모아야 하는
맥락(요청에 "나"가 조율 대상으로 포함된 경우)에서 쓴다. 이 tool은 정식으로 저장된 내 일정과 아직
저장되지 않은 이번 대화의 임시 일정을 함께 포함하고, 개인 일정과 그룹 일정을 종류로 가려내지
않으며, 이번 조율 대상에 없는 멤버가 낀 그룹 일정도 내 바쁜 시간으로 포함한다. 외부 멤버의 바쁜
시간도 같은 결과에 합쳐지고, 같은 사람·같은 날짜·같은 시작 시각·같은 제목의 일정은 자동으로
중복 제거된다. 함께 오는 schedule_summary를 활용해 바쁜 시간을 자연어로 설명할 수 있다.""",
        """외부 멤버 busy-time 조회와 공유 일정 저장소 조회는 이번 주차 범위이며, 여러 사람의 일정을
비교해 최종 회의 시간을 고르는 것은 이번 주차 범위가 아니다. 그 판단을 tool 없이 임의로 만들어
답하지 않는다.""",
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
