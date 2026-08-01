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
from student_parts.week03_build_nanas_logbook import (
    _ensure_content_dedup_key,
    structured_request_from_week01_schedule,
)
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


def _schedule_scope(schedule: dict[str, Any]) -> str:
    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다.

    list_schedules에 kind를 넘기지 않는 것은 의도한 선택입니다.
    앱 schedules 테이블은 모든 row가 owner='me'로 저장되므로(fixed/app_store.py:92, :358)
    personal_schedule과 group_schedule을 구분하지 않고 **모두 내 busy-time으로 봅니다.**
    내가 잡아둔 회의를 빼면 Week 6이 이미 일정이 있는 시각을 "가능"으로 제안하게 됩니다.
    그룹 일정의 참석자 목록은 그 row의 소유자를 바꾸지 않습니다. 참석자 쪽 busy-time은
    공유 저장소 동기화가 별도 row로 만들고, 그건 외부 조회 경로(MCP)로 들어옵니다.
    limit=200은 조회 상한일 뿐이며 kind 필터는 걸지 않습니다.
    """

    db_rows = AppSQLiteStore(CONFIG.app_db_path).list_schedules(limit=200)
    saved_ids = {str(row["schedule_id"]) for row in db_rows}

    scope = current_session_scope()
    temporary_rows: list[dict[str, Any]] = []
    for schedule in PERSONAL_SCHEDULES:
        if _schedule_scope(schedule) != scope:
            continue
        if str(schedule.get("id") or "") in saved_ids:
            continue
        # Week 3+ personal_create_schedule은 한 번 호출로 임시 일정(id=personal_...)과
        # SQLite 일정(schedule_id=sch_...)을 함께 만들어 두 식별자가 서로 겹치지 않습니다.
        # 저장 경로와 같은 helper로 같은 키를 되짚어야 중복을 정확히 걸러냅니다.
        save_input = structured_request_from_week01_schedule(schedule)
        payload = {key: value for key, value in save_input.model_dump().items() if value is not None}
        if str(_ensure_content_dedup_key(payload).get("source_schedule_id") or "") in saved_ids:
            continue
        temporary_rows.append(schedule)

    return [*db_rows, *temporary_rows]


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
    """내 일정과 외부 멤버 일정을 같은 row 구조로 합칩니다.

    두 출처는 원래 모양이 다릅니다. 내 일정은 앱 SQLite row(schedule_id/attendees_json/...)나
    Week 1 임시 dict이고, 외부 멤버 일정은 MCP가 돌려준 row입니다. 이 함수의 일은 둘을
    member_name/title/date/start_time/end_time/notes 여섯 키를 가진 **하나의 rows 배열**로
    맞추는 것입니다. Week 6의 공통 가능 시간 계산이 이 rows를 busy_rows 근거로 그대로 받습니다.
    """

    # 1) 외부 멤버 일정: 정규화 helper를 거쳐 MCP tool을 호출합니다.
    #    이름 alias와 날짜 형식 정리는 fixed/external_people_store.py의 helper가 담당합니다.
    #    (같은 정규화를 여기서 또 구현하지 않습니다 — 경계에서 한 번만 처리하는 것이 규칙입니다.)
    members = normalize_external_member_names(member_names)
    normalized_date_from, normalized_date_to = normalize_external_schedule_date_bounds(
        member_names, date_from, date_to
    )
    # 내 일정의 출처는 앱 SQLite로 이미 정해져 있습니다(가이드 :99). 공유 저장소에 있는 "나" row는
    # 앱 저장 시 자동 동기화된 그 일정의 **사본**이라 외부 조회 대상이 아닙니다. 그대로 조회하면
    # 같은 일정이 앱 원본 + 공유 사본 2행으로 나오므로, 응답을 나중에 걸러내는 대신 입력 단계에서 뺍니다.
    external_members = [name for name in members if name != PERSONAL_SHARED_MEMBER_NAME]
    payload = json.loads(
        call_mcp_tool_sync(
            "extract_schedules_from_history",
            {
                "member_names": external_members,
                "date_from": normalized_date_from,
                "date_to": normalized_date_to,
            },
        )
    )
    # MCP가 이미 멤버·날짜로 걸러 준 결과라 여기서 다시 필터하지 않습니다.
    # 이 row들에는 source_conversation_id도 붙어 있는데, 근거 추적에 쓰이므로 떼지 않습니다.
    external_rows = payload.get("rows", [])

    # 2) 내 일정: 두 가지 모양(SQLite row / Week 1 임시 dict)이 섞여 들어옵니다.
    #    _structured_request_from_schedule_row가 attendees·members 같은 키 차이를 흡수해 주므로
    #    여기서 dict 키를 직접 꺼내 쓰지 않고 그 helper를 통해 읽습니다.
    my_rows: list[dict[str, Any]] = []
    for schedule in personal_schedules:
        request = _structured_request_from_schedule_row(schedule)
        # 날짜가 없는 일정은 "언제 바쁜지"를 말해 주지 못하므로 busy-time 후보에서 뺍니다.
        if not request.date:
            continue
        # 외부 일정과 달리 내 일정은 아직 안 걸러졌으므로 요청 범위로 직접 좁힙니다.
        # 날짜가 YYYY-MM-DD 고정 형식이라 문자열 비교로 충분합니다.
        if normalized_date_from and request.date < normalized_date_from:
            continue
        if normalized_date_to and request.date > normalized_date_to:
            continue
        my_rows.append(
            {
                # 외부 row와 같은 축으로 읽히도록 내 일정도 "나"라는 멤버 이름을 갖습니다.
                "member_name": PERSONAL_SHARED_MEMBER_NAME,
                "title": request.title,
                "date": request.date,
                "start_time": request.start_time,
                "end_time": request.end_time,
                # 앱 일정에는 notes 개념이 없어 대개 None입니다. 없는 값을 지어내지 않고 그대로 둡니다.
                "notes": schedule.get("notes"),
            }
        )

    # 3) 두 출처를 한 배열로 합치고 시간순으로 정렬합니다.
    #    start_time이나 notes가 None일 수 있어 정렬 키에서 None을 ""로 바꿔 비교 오류를 막습니다.
    rows = [*my_rows, *external_rows]
    rows.sort(
        key=lambda row: (
            row.get("date") or "",
            row.get("start_time") or "",
            row.get("member_name") or "",
        )
    )
    # 4) 요약은 "합친 rows 전체"로 다시 만듭니다.
    #    MCP payload의 schedule_summary를 그대로 쓰면 외부 멤버만 요약돼 내 일정이 빠집니다.
    # 5) filters에는 실제로 어떤 조건으로 조회했는지를 남깁니다(Week 3 personal_list_saved_schedules와 같은 관례).
    #    어떤 멤버의 rows가 0건일 때 "조회했는데 일정이 없음"인지 "애초에 조회 대상이 아님"인지
    #    이 값이 없으면 구분할 수 없습니다.
    return {
        "ok": True,
        "tool_name": "collect_member_schedules",
        "rows": rows,
        "schedule_summary": external_schedule_summary(rows),
        "filters": {
            "requested_member_names": member_names,
            "external_member_names": external_members,
            "date_from": normalized_date_from,
            "date_to": normalized_date_to,
            "includes_personal_schedules": True,
        },
    }


@tool(args_schema=SearchPreviousConversationsInput)
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> str:
    """외부 SQLite 데이터베이스에 저장된 이전 대화를 검색합니다. query에는 LLM이 고른 짧은 핵심 명사나 구를 넣습니다.

    query에는 찾으려는 주제 명사 한 단어만 넣습니다. '얘기'·'대화'·'일정'처럼 질문에 들어 있을 뿐
    주제가 아닌 낱말을 그대로 옮기지 않고, 조사와 수식어도 뺍니다. 저장소가 query 문자열을 통째로
    대조하므로 '철수 일정 공유'처럼 여러 단어를 이어 붙이면 0건이 나옵니다.
    사람 이름은 query가 아니라 member_names로 넘깁니다.
    """

    return call_mcp_tool_sync(
        "search_previous_conversations",
        {"query": query, "member_names": member_names, "limit": limit},
    )


@tool(args_schema=LoadConversationMessagesInput)
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다.

    사용자가 대화의 '내용'·'전체'·'그대로'·'오간 메시지'를 요구하면 이 도구를 씁니다.
    conversation_id는 search_previous_conversations 결과에서 얻습니다.
    """

    payload = call_external_tool_payload(
        "load_conversation_messages",
        {"conversation_id": conversation_id},
    )
    return json_payload(payload)


@tool(args_schema=ExtractSchedulesFromHistoryInput)
def extract_schedules_from_history(member_names: list[str], date_from: str, date_to: str) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다.

    철수·영희처럼 나 이외의 특정 인물이 언제 무엇을 하는지, 언제 바쁜지 물을 때 이 도구를 씁니다.
    그 사람들의 일정은 앱 DB에 없으므로 개인 일정 조회 도구로는 찾을 수 없습니다.
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

    사용자가 '공유 일정 저장소'에 등록해 달라고 지목할 때 씁니다. date는 YYYY-MM-DD,
    start_time과 end_time은 HH:MM 형식입니다.
    끝나는 시각을 말하지 않았으면 end_time 인자를 아예 넣지 않습니다. 생략하면 "미정"으로 채워지며,
    null을 넣으면 문자열이 아니라 거부됩니다. notes도 넣을 내용이 없으면 생략합니다.
    """

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
    """외부 MCP 공유 일정 저장소에서 일정을 삭제합니다.

    사용자가 '공유 일정 저장소'에서 지워 달라고 지목할 때 씁니다.
    schedule_id 또는 source_conversation_id 중 최소 하나는 채워야 합니다.
    둘 다 비운 채 호출하면 아무것도 삭제되지 않습니다.
    """

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
    """외부 MCP 공유 일정 저장소에 등록된 일정을 조회합니다. 필터가 없으면 기본 공유 일정을 반환합니다.

    공유 저장소에 등록된 row 자체를 확인할 때 씁니다.
    date_from과 date_to는 YYYY-MM-DD 형식입니다.
    """

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

    나와 팀원의 바쁜 시간을 한 rows로 모아 회의 시간을 조율할 때 씁니다.
    내 일정은 member_names와 무관하게 항상 포함되므로 member_names에는 나를 빼고 팀원 이름만 넣습니다.
    date_from과 date_to는 YYYY-MM-DD 형식입니다.
    """

    return json_payload(
        _collect_member_schedules(
            member_names=member_names,
            date_from=date_from,
            date_to=date_to,
            personal_schedules=_personal_schedules_for_current_scope(),
        )
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

    external_part = (
        "## Week 5 출처 분리 (Week 3 조회/저장 라우팅 override, 가장 강한 제약)\n"
        "- 앞 주차의 '저장된 일정을 보여달라는 요청이면 personal_list_saved_schedules로 조회한다'와 "
        "'새로 저장 요청이 오면 extract_schedule_request → save_structured_request 경로 하나만 쓴다'는 "
        "모두 '나'의 앱 일정에만 적용된다. 아래 경우에는 그 지시 대신 Week 5 도구를 쓴다.\n"
        "- 판별 기준은 '일정'이라는 단어가 아니라 누구의 일정인가다.\n"
        "- 다른 사람의 일정을 '알려줘/보여줘/어떻게 돼'처럼 묻는 것은 조회 요청이지 저장 요청이 아니다. "
        "extract_schedule_request나 save_structured_request를 거치지 말고 바로 Week 5 조회 도구를 쓴다.\n"
        "- 질문에 나 이외의 사람 이름이 하나라도 나오면(예: '철수 목요일에 뭐 있어?') 그 사람의 일정과 대화는 앱 DB에 없다. "
        "personal_list_saved_schedules를 부르지 말고 Week 5 도구로 조회한다.\n"
        "- Week 1~3 일정 도구는 '나'의 일정 전용이라 다른 사람의 일정을 절대 찾지 못한다. "
        "그 도구가 빈 결과를 준 것을 그 사람에게 일정이 없다는 근거로 삼아 답하지 않는다.\n"
        "- 사용자가 '공유 일정 저장소'를 지목해 등록·삭제를 요청하면 save_structured_request가 아니라 "
        "create_shared_schedule / delete_shared_schedule을 쓴다. 앱 일정 저장은 공유 저장소에 등록되지 않는다.\n"
        "- 사람 이름이 없고 나 자신에 대한 질문이면 Week 1~4 도구만 쓰고, 조회 없이 추측해 말하지 않는다.\n"
        "## Week 5 tool 선택 기준\n"
        "- 어떤 도구를 왜 쓰는지 사용자에게 설명하지 말고 바로 호출한다. 조회 계획만 말하고 답변을 끝내지 않는다.\n"
        "- collect_member_schedules: 나와 팀원의 바쁜 시간을 한 번에 모을 때 쓴다. 이 tool을 부르면 extract_schedules_from_history를 따로 중복 호출하지 않는다.\n"
        "- 둘 중 무엇을 쓸지는 질문에 '나/내'가 들어가는지로 가른다.\n"
        "  · '나랑 철수, 영희 일정 다 모아줘'처럼 나를 포함하면 collect_member_schedules 하나로 처리한다. "
        "personal_list_saved_schedules와 extract_schedules_from_history를 각각 불러 직접 합치지 않는다 "
        "— 그러면 두 출처의 row 구조가 서로 달라진다. 직전 턴에서 그 조합을 썼더라도 이번 요청 기준으로 다시 고른다.\n"
        "  · '내 일정은 빼고 철수랑 영희 일정만'처럼 나를 제외하면 extract_schedules_from_history를 쓴다.\n"
        "- extract_schedules_from_history: 내 일정은 빼고 외부 멤버의 일정만 필요할 때 쓴다.\n"
        "- search_previous_conversations: 질문에 철수·영희 같은 외부 멤버 이름이 나오는 지난 대화를 찾을 때 쓴다. "
        "'철수랑 예전에 나눈 대화'처럼 상대가 외부 멤버면 이 도구다. "
        "결과 rows는 검색어에 걸린 메시지 몇 개일 뿐 대화 전체가 아니다.\n"
        "- 반대로 외부 멤버 이름이 하나도 없이 '아까 우리가 무슨 얘기 했지?'처럼 나와 Kana가 이 앱에서 나눈 "
        "대화를 가리키면 Week 4의 search_conversation_messages로 찾는다.\n"
        "- 대화 검색만으로 답을 끝내지 않는다. 검색 rows에 content가 있어도 그것은 대화 전체가 아니므로 "
        "충분하다고 판단하지 말고 load_conversation_messages로 한 번 더 확인한다.\n"
        "- 공유 일정을 지울 때는 list_shared_schedules로 대상을 먼저 확인하고, 거기서 얻은 schedule_id나 "
        "source_conversation_id로 delete_shared_schedule을 부른다. 조회 없이 바로 삭제하면 대상을 못 찾아 "
        "아무것도 지워지지 않는다.\n"
        "## Week 5 인자 규칙\n"
        "- date_from/date_to는 YYYY-MM-DD로 넣는다. '이번 주', '다음 주' 같은 상대 표현은 오늘 날짜를 기준으로 계산해 변환한다.\n"
        "- 기간을 말하지 않은 질문에는 date_from과 date_to를 같은 날로 좁히지 않는다. 오늘이 포함된 주처럼 "
        "충분히 넓은 범위로 조회한다. 좁게 조회해 rows가 비었을 때 그것을 '일정이 없다'는 근거로 삼지 않는다.\n"
        "- member_names에는 사용자가 실제로 말한 사람만 넣는다. 이름을 지어내지 않는다.\n"
        "- 아무 이름도 언급되지 않았으면 누구의 일정인지 되묻거나 멤버 필터 없이 조회한다. 빈 리스트 []는 '아무도 조회하지 않음'이라 결과가 비므로 '전체'라는 뜻으로 쓰지 않는다.\n"
        "- 예: '철수랑 영희랑 다음 주에 회의 잡고 싶은데 언제 비어?' → collect_member_schedules를 member_names=[\"철수\", \"영희\"], date_from/date_to=다음 주 시작·끝(오늘 기준 계산)로 한 번만 호출한다.\n"
        "- 예: '철수 이번 주 목요일에 뭐 있어?' → 한 사람만 물어도 남의 일정이므로 personal_list_saved_schedules를 부르지 않는다. "
        "extract_schedules_from_history를 member_names=[\"철수\"], date_from/date_to=그 목요일 날짜(오늘 기준 계산)로 호출한다.\n"
        "## Week 5 근거 규칙 (강한 제약)\n"
        "- 답변에는 tool 결과의 rows와 schedule_summary에 실제로 있는 일정만 말한다. 없는 일정이나 시간을 지어내지 않는다.\n"
        "- Week 5의 역할은 바쁜 시간을 모아 정리하는 데까지다. 여러 사람의 최종 회의 시각 확정은 하지 않는다."
    )

    return [
        *week04_prompt_parts(),
        external_part,
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
