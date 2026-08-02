from __future__ import annotations

from datetime import date
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
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts, week04_tools


_WEEK05_AGENT: Any | None = None


call_mcp_tool = call_local_mcp_tool
call_mcp_tool_sync = call_local_mcp_tool_sync
load_langchain_mcp_tools = load_local_mcp_tools
load_langchain_mcp_tools_sync = load_local_mcp_tools_sync


def _schedule_scope(schedule: dict[str, Any]) -> str:
    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


def _personal_schedules_for_current_scope() -> list[dict[str, Any]]:
    """SQLite 저장 일정과 현재 대화의 임시 일정만 group 조율 후보로 사용합니다."""

    sqlite_schedules = AppSQLiteStore(CONFIG.app_db_path).list_schedules(
        limit=100, kind="personal_schedule"
    )
    sqlite_schedule_ids: set[str] = set()

    for schedule in sqlite_schedules:
        schedule_id = schedule.get("schedule_id") or schedule.get("id")
        # week1에서는 저장할 때 id로 쓰고, week3이후 sqlite에 저장할때는 schedule_id로 저장하기에 두 경우 모두 고려해야 한다.

        if schedule_id:
            sqlite_schedule_ids.add(str(schedule_id))

    # week1의 코드만 실행하여 일정을 생성하였을 경우
    # 메모리에만 있고 SQLite에는 아직 저장되지 않은 현재 대화의 임시 일정이 있을 수 있다.
    session_id = current_session_scope()
    temporary_schedules: list[dict[str, Any]] = []

    for schedule in PERSONAL_SCHEDULES:
        # 다른 대화 세션에서 만들어진 임시 일정을 제외
        if _schedule_scope(schedule) != session_id:
            continue
        temporary_schedule_id = schedule["id"]

        if temporary_schedule_id in sqlite_schedule_ids:
            continue
        temporary_schedules.append(schedule)

    return [*sqlite_schedules, *temporary_schedules]


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
    date_from: date
    date_to: date


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
    date_from: date,
    date_to: date,
    personal_schedules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    내 일정과 외부 멤버 일정을 같은 row 구조로 합칩니다.
    멤버와 일정 조율을 위해서는 서로의 일정을 알 필요가 있습니다.
    이를 위해 인자로 입력한 멤버들의 일정들과 나의 일정을 합쳐 조회합니다.
    """

    normalized_member_names = normalize_external_member_names(member_names)

    normalized_date_from, normalized_date_to = normalize_external_schedule_date_bounds(
        normalized_member_names,
        date_from,
        date_to,
    )
    external_member_names = [
        member_name for member_name in normalized_member_names if member_name != "나"
    ]
    try:
        raw_payload = call_mcp_tool_sync(
            "extract_schedules_from_history",
            {
                "member_names": external_member_names,
                "date_from": normalized_date_from,
                "date_to": normalized_date_to,
            },
        )
        external_payload = json.loads(raw_payload)

        if not isinstance(external_payload, dict):
            raise ValueError("MCP 응답을 json.load한 결과가 dict가 아닙니다.")

        external_rows = external_payload.get("rows") or []

        if not isinstance(external_rows, list):
            raise ValueError("MCP 응답의 rows가 list가 아닙니다.")

    except Exception as e:
        return {
            "ok": False,
            "rows": [],
            "schedule_summary": None,
            "member_names": member_names,
            "date_from": date_from,
            "date_to": date_to,
            "error": {
                "code": "external_schedule_lookup_failed",
                "type": type(e).__name__,
                "message": "외부 멤버 일정 조회에 실패했습니다.",
                "detail": str(e),
            },
        }

    personal_rows: list[dict[str, Any]] = []

    for schedule in personal_schedules:
        structured_request = _structured_request_from_schedule_row(schedule)
        schedule_date = structured_request.date

        if not schedule_date or not (
            normalized_date_from <= schedule_date <= normalized_date_to
        ):
            continue

        # 외부 일정과 동일한 필드 구조로 변환
        personal_rows.append(
            {
                "member_name": "나",
                "title": structured_request.title,
                "date": schedule_date,
                "start_time": structured_request.start_time,
                "end_time": structured_request.end_time,
                "notes": None,
            }
        )

    # 내 일정과 외부 멤버 일정을 하나의 목록으로 합침
    rows = [*personal_rows, *external_rows]

    return {
        "ok": True,
        "rows": rows,
        "schedule_summary": external_schedule_summary(rows),
        "member_names": member_names,
        "date_from": date_from,
        "date_to": date_to,
        "error": None,
    }


@tool(args_schema=SearchPreviousConversationsInput)
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> str:
    """외부 SQLite 데이터베이스에 저장된 이전 대화를 검색합니다. query에는 LLM이 고른 짧은 핵심 명사나 구를 넣습니다."""

    args = {
        "query": query,
        "member_names": member_names,
        "limit": limit,
    }
    return call_mcp_tool_sync("search_previous_conversations", args=args)


@tool(args_schema=LoadConversationMessagesInput)
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다."""

    payload = call_external_tool_payload(
        "load_conversation_messages", {"conversation_id": conversation_id}
    )
    return json_payload(payload)


@tool(args_schema=ExtractSchedulesFromHistoryInput)
def extract_schedules_from_history(
    member_names: list[str], date_from: str, date_to: str
) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다."""

    args = {
        "member_names": member_names,
        "date_from": date_from,
        "date_to": date_to,
    }
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
    """외부 MCP 공유 일정 저장소에 일정을 등록하거나 갱신합니다."""

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
    """
    외부 MCP 공유 일정 저장소에서 일정을 삭제합니다.
    식별자는 반드시 직전 list_shared_schedules 결과에서 그대로 가져와야 합니다.
    식별자를 추측하거나 생성해서는 안 되며, 사용자 확인 없이 호출하지 않습니다.
    schedule_id와 source_conversation_id 중 하나만 전달합니다.
    """

    return call_mcp_tool_sync(
        "delete_shared_schedule",
        {
            "schedule_id": schedule_id,
            "source_conversation_id": source_conversation_id,
        },
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

    args = {
        "member_names": member_names,
        "date_from": date_from,
        "date_to": date_to,
        "source_conversation_id": source_conversation_id,
        "limit": limit,
    }
    return call_mcp_tool_sync("list_shared_schedules", args)


@tool(args_schema=CollectMemberSchedulesInput)
def collect_member_schedules(
    member_names: list[str], date_from: date, date_to: date
) -> str:
    """내 일정과 다른 사람들의 일정을 MCP SQLite 기록에서 모읍니다."""

    personal_schedules = _personal_schedules_for_current_scope()

    payload = _collect_member_schedules(
        member_names=member_names,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        personal_schedules=personal_schedules,
    )
    return json_payload(payload)


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
        f"""
        현재 날짜는 {current_app_date_iso()}이다.
        너는 외부 멤버의 과거 대화와 일정을 조회하는 Week 5 agent다.

        사용자가 외부 멤버의 과거 대화 내용을 찾으면
        search_previous_conversations를 먼저 호출한다.

        과거 대화 검색 결과에서 특정 conversation_id의 전체 메시지가
        필요한 경우에만 load_conversation_messages를 호출한다.

        사용자가 외부 멤버 한 명 또는 여러 명의 일정만 요청하면
        extract_schedules_from_history를 호출한다.

        사용자가 내 일정과 외부 멤버 일정을 비교하거나
        여러 사람과의 일정 조율을 요청하면 collect_member_schedules를 호출한다.
        - 이때 반드시 조회 기간을 먼저 확인해야 한다. 
        - 사용자가 오늘, 내일, 이번 주, 다음 주처럼 해석 가능한 기간을 말한 경우 현재 날짜를 기준으로 date_from과 date_to를 구체적인 ISO 날짜로 변환한다. 
        - 조회 기간을 알 수 없는 경우에는 date_from과 date_to를 임의로 생성하지 않으며, collect_member_schedules를 호출하기 전에 먼저 사용자에게 조회 기간을 질문한 뒤 호출한다. 

        collect_member_schedules를 호출할 때 date_from 또는 date_to가
        비어 있거나 유효한 날짜 형식이 아니어서 입력 검증에 실패한 경우:

        - 사용자에게 시스템 오류가 발생했다고 말하지 않는다.
        - date_from과 date_to를 임의로 추측하여 다시 호출하지 않는다.
        - 사용자에게 조회할 기간을 자연스럽게 질문하고 현재 답변을 종료한다.
        - 사용자가 기간을 답하면 현재 날짜를 기준으로 구체적인 ISO 날짜 범위로 변환한 뒤
        collect_member_schedules를 다시 호출한다.

        예시 질문:
        "어느 기간을 기준으로 일정을 확인할까요? 예를 들어 이번 주 또는 8월 3일부터 8월 9일까지처럼 알려주세요."

        사용자가 공유 일정 저장소에 등록된 일정을 직접 확인하려고 하면
        list_shared_schedules를 호출한다.

        사용자가 공유 일정의 등록 또는 삭제를 명시적으로 요청한 경우에만
        create_shared_schedule 또는 delete_shared_schedule을 호출한다.

        공유 일정을 삭제할 때는 다음 절차를 반드시 따른다.

        1. delete_shared_schedule을 호출하기 전에 list_shared_schedules를 호출해 삭제 후보를 조회한다.
        2. schedule_id나 source_conversation_id를 추측하거나 새로 생성하지 않는다.
        3. 반드시 list_shared_schedules의 최신 조회 결과에 포함된 식별자를 그대로 사용한다.
        4. 조회한 일정의 멤버, 제목, 날짜, 시간을 사용자에게 보여주고 삭제 여부를 확인한다.
        5. 사용자가 해당 대상을 명시적으로 확인한 경우에만 delete_shared_schedule을 호출한다.
        6. 후보가 여러 개이거나 대상을 정확히 식별할 수 없으면 삭제하지 않고 사용자가 선택하게 한다.
        7. schedule_id와 source_conversation_id를 동시에 전달하지 않는다.
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
