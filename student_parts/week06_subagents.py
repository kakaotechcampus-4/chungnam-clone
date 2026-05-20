from __future__ import annotations

import json
import re
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

from fixed.config import CONFIG
from fixed.runtime_clock import current_app_date_iso
from fixed.stores import AppSQLiteStore
from golden_cases import harness_prompt_examples
from student_parts.week01_tools import (
    ensure_demo_personal_schedule,
    list_personal_schedule_dicts,
    personal_create_schedule,
    personal_delete_schedule,
    personal_list_schedules,
)
from student_parts.week02_structured_output import extract_structured_request
from student_parts.week03_sqlite_store import get_saved_request, list_saved_requests, save_structured_request
from student_parts.week04_agentic_rag import (
    add_personal_reference,
    build_rag_context,
    search_personal_references,
    search_saved_requests,
)
from student_parts.week05_mcp_sqlite import (
    extract_schedules_from_history,
    extract_schedules_from_history_dict,
    load_conversation_messages,
    search_previous_conversations,
)


MEMBER_ALIAS = {"A": "민준", "B": "서연", "C": "지훈"}
_NANA_SUBAGENT: Any | None = None
_KANA_SUBAGENT: Any | None = None
_DELETE_ALL_WORDS = ("전체", "전부", "모든", "모두", "all", "every")


def _normalize_members(member_names: list[str]) -> list[str]:
    normalized = [MEMBER_ALIAS.get(name, name) for name in member_names]
    return normalized or ["민준", "서연", "지훈"]


def _compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value or "").lower()


def _query_mentions_date(query: str) -> bool:
    return bool(
        re.search(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}", query)
        or re.search(r"\d{1,2}월\s*\d{1,2}일", query)
        or any(word in query for word in ["오늘", "내일", "모레", "다음 주", "다음주"])
    )


def _query_wants_all_schedules(query: str) -> bool:
    compact = _compact(query)
    return any(word in compact for word in _DELETE_ALL_WORDS) or bool(re.search(r"(?:총\s*)?\d+\s*건", query))


def _title_filter_from_structured(structured: dict[str, Any]) -> str | None:
    title = str(structured.get("title") or "").strip()
    compact_title = _compact(title)
    if not title or title in {"개인 일정", "그룹 일정", "제목 없음"}:
        return None
    if any(word in compact_title for word in ["삭제", "지워", "취소", "전체", "전부", "모든", "모두"]):
        return None
    return title


def _delete_filter_from_query(query: str) -> tuple[dict[str, Any], dict[str, Any]]:
    structured_model = extract_structured_request(query)
    structured = structured_model.model_dump()
    filters: dict[str, Any] = {}
    if _query_mentions_date(query) and structured.get("date"):
        filters["date"] = structured["date"]
    if structured.get("start_time"):
        filters["start_time"] = structured["start_time"]
    if "시간미정" in _compact(query):
        filters["time_unspecified"] = True
    title = _title_filter_from_structured(structured)
    if title:
        filters["title"] = title
    return structured, filters


def _memory_schedule_matches(
    schedule: dict[str, Any],
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
) -> bool:
    if schedule_ids is not None and schedule["id"] not in schedule_ids:
        return False
    if date and schedule.get("date") != date:
        return False
    if title and _compact(title) not in _compact(schedule.get("title")):
        return False
    if start_time and schedule.get("start_time") != start_time:
        return False
    if time_unspecified and schedule.get("start_time") not in {None, "", "미정"}:
        return False
    return True


def _delete_memory_schedules(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
) -> list[dict[str, Any]]:
    rows = list(list_personal_schedule_dicts())
    if not delete_all:
        rows = [
            row
            for row in rows
            if _memory_schedule_matches(
                row,
                schedule_ids=schedule_ids,
                date=date,
                title=title,
                start_time=start_time,
                time_unspecified=time_unspecified,
            )
        ]
    deleted: list[dict[str, Any]] = []
    for row in rows:
        result = json.loads(personal_delete_schedule.invoke({"schedule_id": row["id"]}))
        if result.get("deleted"):
            deleted.append(row)
    return deleted


def delete_saved_schedules_dict(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
    app_store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    store = app_store or AppSQLiteStore(CONFIG.app_db_path)
    has_filter = schedule_ids is not None or any([date, title, start_time, time_unspecified])
    if not delete_all and not has_filter:
        return {
            "ok": False,
            "tool_name": "personal_delete_saved_schedules",
            "reason": "삭제할 일정 ID나 날짜/제목/시간 필터가 필요합니다.",
            "delete_all": False,
            "bulk_delete": False,
            "deleted_count": 0,
            "filters": {
                "schedule_ids": schedule_ids,
                "date": date,
                "title": title,
                "start_time": start_time,
                "time_unspecified": time_unspecified,
            },
            "deleted": [],
        }
    if delete_all:
        app_deleted = store.delete_all_schedules()
    else:
        app_deleted = store.delete_schedules_by_filter(
            schedule_ids=schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
        )
    memory_deleted = _delete_memory_schedules(
        schedule_ids=schedule_ids,
        date=date,
        title=title,
        start_time=start_time,
        time_unspecified=time_unspecified,
        delete_all=delete_all,
    )

    deleted = [
        *({"source": "app_db", "schedule": row} for row in app_deleted),
        *({"source": "memory", "schedule": row} for row in memory_deleted),
    ]

    return {
        "ok": bool(deleted),
        "tool_name": "personal_delete_saved_schedules",
        "delete_all": delete_all,
        "bulk_delete": bool(not delete_all and deleted),
        "deleted_count": len(deleted),
        "filters": {
            "schedule_ids": schedule_ids,
            "date": date,
            "title": title,
            "start_time": start_time,
            "time_unspecified": time_unspecified,
        },
        "deleted": deleted,
    }


def delete_schedule_by_query_dict(query: str, app_store: AppSQLiteStore | None = None) -> dict[str, Any]:
    structured, filters = _delete_filter_from_query(query)
    delete_all = _query_wants_all_schedules(query) and not filters
    result = delete_saved_schedules_dict(delete_all=delete_all, app_store=app_store, **filters)
    result["tool_name"] = "personal_delete_schedule_by_query"
    result["structured_request"] = structured
    if not result["ok"] and not filters and not delete_all:
        result["reason"] = "삭제할 일정을 충분히 특정하지 못했습니다. 먼저 저장 일정 목록을 확인해 주세요."
    return result


def _chat_model() -> ChatOpenAI:
    if not CONFIG.has_openai_key:
        raise RuntimeError("OPENAI_API_KEY가 .env에 필요합니다.")
    return ChatOpenAI(model=CONFIG.openai_model, temperature=0)


def _harness_examples_text() -> str:
    return json.dumps(harness_prompt_examples(), ensure_ascii=False, indent=2)


def nana_system_prompt() -> str:
    return (
        "너는 Kanana의 Nana 하위 에이전트다. 사용자의 프롬프트를 기준으로 필요한 도구를 직접 선택한다. "
        f"현재 날짜는 앱 시작 시 OS에서 읽은 {current_app_date_iso()}이다. "
        "오늘/내일/다음 주 같은 상대 날짜는 이 날짜를 기준으로 해석한다. "
        "코드가 주차나 기능을 대신 고르지 않으므로 네가 프롬프트를 읽고 필요한 tool chain을 선택한다. "
        "Week 1 개인 일정 생성/조회/삭제는 personal_create_schedule, personal_list_schedules, "
        "personal_delete_schedule을 사용한다. Week 2 날짜/시간/종류/멤버 판단이 필요하면 "
        "extract_schedule_request를 호출한다. Week 3 저장/조회는 save_structured_request, "
        "list_saved_requests, get_saved_request를 사용한다. Week 4 개인 참고자료 추가/검색과 RAG 문맥 구성은 "
        "add_personal_reference, search_personal_references, search_saved_requests, build_rag_context를 사용한다. "
        "개인 일정 생성 요청이면 extract_schedule_request 결과를 바탕으로 personal_create_schedule을 호출하고, "
        "personal_create_schedule 결과의 structured_request를 save_structured_request payload로 전달해 앱 DB에 저장한다. "
        "일정 삭제 요청이면 먼저 "
        "personal_list_saved_schedules로 "
        "저장된 후보를 확인하고, 사용자의 표현과 의미가 같은 후보의 schedule_id나 날짜/제목/시간 필터를 "
        "스스로 골라 personal_delete_saved_schedules를 호출한다. 전체 삭제는 delete_all=true를 쓴다. "
        "personal_delete_schedule_by_query는 이전 하네스 호환용 간편 도구로만 사용한다. "
        "요약, 후보 선택, 자연어 답변은 네가 맡고, 도구 결과에 없는 사실은 만들지 않는다. "
        "그룹 일정 조율, 여러 사람의 공통 가능 시간 계산은 직접 처리하지 말고 그 사실을 짧게 알린다. "
        "하네스 예시는 다음과 같다:\n"
        f"{_harness_examples_text()}"
    )


def kana_system_prompt() -> str:
    return (
        "너는 Kanana의 Kana 하위 에이전트다. 여러 사람의 일정을 조율한다. "
        f"현재 날짜는 앱 시작 시 OS에서 읽은 {current_app_date_iso()}이다. "
        "먼저 extract_schedule_request로 날짜와 멤버를 구조화한다. "
        "그 다음 collect_member_schedules로 나와 멤버들의 바쁜 시간 목록을 받는다. "
        "가능한 시간 판단은 네가 직접 하고, 선택한 시간을 selected_slot으로 만들어 propose_group_schedule에 전달한다. "
        "이전 대화 원문이 필요할 때만 search_previous_conversations나 load_conversation_messages를 추가로 쓴다. "
        "도구 결과에 없는 일정이나 시간을 만들지 않는다. 개인 일정만 요청하면 Nana 담당이라고 짧게 답한다. "
        "하네스 예시는 다음과 같다:\n"
        f"{_harness_examples_text()}"
    )


def supervisor_system_prompt() -> str:
    return (
        "너는 Kanana 일정 비서의 프롬프트 기반 supervisor 에이전트다. 메인 런타임이나 Python 코드가 "
        f"현재 날짜는 앱 시작 시 OS에서 읽은 {current_app_date_iso()}이다. "
        "오늘/내일/다음 주 같은 상대 날짜는 이 날짜를 기준으로 해석한다. "
        "주차, 에이전트, 도구를 미리 고르지 않는다. 너는 사용자 프롬프트와 아래 하네스 예시를 읽고 "
        "Week 1-4의 개인 일정/저장/RAG 흐름은 nana_agent에, Week 5-6의 여러 사람 일정/외부 대화/그룹 조율 흐름은 "
        "kana_agent에 맡긴다. 반드시 nana_agent 또는 kana_agent 도구 중 하나를 직접 호출한 뒤, "
        "그 도구 결과만 근거로 최종 답변을 작성한다. "
        "개인 일정 생성/조회/삭제, todo/reminder 저장, 개인 참고자료 검색은 nana_agent에게 위임한다. "
        "팀원, 그룹, 여러 사람, 모두의 일정 조율은 kana_agent에게 위임한다. "
        "단, 사용자가 '그 시간', '방금 정한 시간', '아까 제안한 일정'처럼 이전 답변의 특정 "
        "후보를 그대로 사용하라고 하면 kana_agent로 다시 재탐색하지 말고, 이전 대화에 나온 "
        "날짜와 시간을 명시적으로 포함해 nana_agent에 위임한다. 사용자가 다시 찾아달라고 "
        "요청한 경우에만 kana_agent로 재계산한다. "
        "최종 답변에서는 도구 결과와 이전 대화에 실제로 나온 시간만 말하고, 도구 결과와 다른 "
        "새 시간이나 상태를 만들어내지 않는다. "
        "사용자에게는 자연스럽게 답변하고, 에이전트 이름이나 도구 이름은 사용자가 묻지 않는 한 "
        "노출하지 않는다. 하네스 예시는 다음과 같다:\n"
        f"{_harness_examples_text()}"
    )


def _message_content_to_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def _extract_final_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        text = _message_content_to_text(message)
        if text:
            return text
    return "응답을 생성하지 못했습니다."


def _extract_agent_trace(result: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            events.append(
                {
                    "event": "tool_call",
                    "tool_name": call.get("name"),
                    "arguments": call.get("args"),
                    "id": call.get("id"),
                }
            )
        if getattr(message, "type", "") == "tool":
            content = getattr(message, "content", "")
            parsed: Any = content
            try:
                parsed = json.loads(content)
            except Exception:
                pass
            events.append(
                {
                    "event": "tool_result",
                    "tool_name": getattr(message, "name", None),
                    "content": parsed,
                    "id": getattr(message, "tool_call_id", None),
                }
            )
    return events


def _tool_call_names(events: list[dict[str, Any]]) -> list[str]:
    return [event["tool_name"] for event in events if event.get("event") == "tool_call" and event.get("tool_name")]


def tool_name(tool_object: Any) -> str:
    return getattr(tool_object, "name", getattr(tool_object, "__name__", str(tool_object)))


@tool
def extract_schedule_request(query: str) -> str:
    """사용자 프롬프트를 일정 앱용 구조화 요청 JSON으로 변환합니다."""

    structured = extract_structured_request(query)
    return json.dumps(
        {
            "ok": True,
            "tool_name": "extract_schedule_request",
            "base_date": current_app_date_iso(),
            "structured_request": structured.model_dump(),
        },
        ensure_ascii=False,
    )


def nana_tools() -> list[Any]:
    return [
        extract_schedule_request,
        personal_create_schedule,
        personal_list_schedules,
        personal_delete_schedule,
        personal_list_saved_schedules,
        personal_delete_saved_schedules,
        personal_delete_schedule_by_query,
        save_structured_request,
        list_saved_requests,
        get_saved_request,
        add_personal_reference,
        search_personal_references,
        search_saved_requests,
        build_rag_context,
    ]


def kana_tools() -> list[Any]:
    return [
        extract_schedule_request,
        search_previous_conversations,
        load_conversation_messages,
        extract_schedules_from_history,
        collect_member_schedules,
        propose_group_schedule,
    ]


def supervisor_tools() -> list[Any]:
    return [nana_agent, kana_agent]


def agent_tool_names(agent_name: str) -> list[str]:
    if agent_name == "nana_agent":
        return [tool_name(item) for item in nana_tools()]
    if agent_name == "kana_agent":
        return [tool_name(item) for item in kana_tools()]
    if agent_name == "supervisor":
        return [tool_name(item) for item in supervisor_tools()]
    return []


def build_nana_subagent() -> object:
    """개인 일정과 RAG 작업을 처리하는 프롬프트 기반 Nana 하위 에이전트를 만듭니다."""

    global _NANA_SUBAGENT
    if _NANA_SUBAGENT is None:
        _NANA_SUBAGENT = create_agent(
            model=_chat_model(),
            tools=nana_tools(),
            system_prompt=nana_system_prompt(),
        )
    return _NANA_SUBAGENT


def build_kana_subagent() -> object:
    """그룹 일정 조율을 처리하는 프롬프트 기반 Kana 하위 에이전트를 만듭니다."""

    global _KANA_SUBAGENT
    if _KANA_SUBAGENT is None:
        _KANA_SUBAGENT = create_agent(
            model=_chat_model(),
            tools=kana_tools(),
            system_prompt=kana_system_prompt(),
        )
    return _KANA_SUBAGENT


@tool
def personal_list_saved_schedules(limit: int = 50) -> str:
    """앱 DB에 저장된 일정 목록을 반환합니다. Nana가 삭제 후보를 직접 고를 때 사용합니다."""

    store = AppSQLiteStore(CONFIG.app_db_path)
    return json.dumps(
        {
            "ok": True,
            "tool_name": "personal_list_saved_schedules",
            "schedules": store.list_schedules(limit=limit),
        },
        ensure_ascii=False,
    )


@tool
def personal_delete_saved_schedules(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
) -> str:
    """Nana가 고른 일정 ID나 날짜/제목/시간 필터로 저장 일정을 삭제합니다."""

    return json.dumps(
        delete_saved_schedules_dict(
            schedule_ids=schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
            delete_all=delete_all,
        ),
        ensure_ascii=False,
    )


@tool
def personal_delete_schedule_by_query(query: str) -> str:
    """일정 ID가 없어도 사용자 프롬프트의 날짜, 시간, 제목 단서로 개인 일정을 찾아 삭제합니다."""

    return json.dumps(delete_schedule_by_query_dict(query), ensure_ascii=False)


@tool
def collect_member_schedules(member_names: list[str], date_from: str, date_to: str) -> str:
    """내 일정과 다른 사람들의 일정을 MCP SQLite 기록에서 모읍니다."""

    # [6주차][학생 구현]
    # 내 개인 일정은 Nana 도구에서, 다른 사람들의 일정은 5주차 MCP SQLite 도구에서 모아오세요.
    #
    # [참고 답안]
    ensure_demo_personal_schedule()
    normalized_members = _normalize_members(member_names)
    my_rows = [
        {
            "member_name": "나",
            "title": row["title"],
            "date": row["date"],
            "start_time": row["start_time"],
            "end_time": row["end_time"] if row["end_time"] != "미정" else "18:00",
            "notes": "Nana 개인 일정",
        }
        for row in list_personal_schedule_dicts(date_from=date_from, date_to=date_to)
    ]
    external_rows = extract_schedules_from_history_dict(
        member_names=normalized_members,
        date_from=date_from,
        date_to=date_to,
    )
    return json.dumps(
        {
            "ok": True,
            "tool_name": "collect_member_schedules",
            "members": ["나", *normalized_members],
            "rows": [*my_rows, *external_rows],
        },
        ensure_ascii=False,
    )


@tool
def propose_group_schedule(
    title: str,
    member_names: list[str],
    candidate_slots: list[dict[str, Any]] | None = None,
    selected_slot: dict[str, Any] | None = None,
    reason: str | None = None,
) -> str:
    """Kana가 고른 후보 시간으로 최종 그룹 일정 결정 페이로드를 만듭니다."""

    # [6주차][학생 구현]
    # 가장 적합한 시간은 Kana가 collect_member_schedules 결과를 보고 직접 고릅니다.
    # 이 도구는 Kana가 고른 selected_slot을 최종 일정 결정 페이로드로 포장합니다.
    #
    # [참고 답안]
    slots = candidate_slots or []
    selected = selected_slot or (slots[0] if slots else None)
    payload = {
        "title": title,
        "members": _normalize_members(member_names),
        "selected_slot": selected,
        "status": "confirmed" if selected else "needs_manual_review",
        "reason": reason or (selected.get("reason") if selected else "공통 가능 시간을 찾지 못했습니다."),
    }
    return json.dumps({"ok": True, "tool_name": "propose_group_schedule", "final_decision": payload}, ensure_ascii=False)


@tool
def nana_agent(query: str) -> str:
    """개인 일정과 개인 RAG 작업을 프롬프트 기반 Nana 하위 에이전트에게 위임합니다."""

    # [6주차][학생 구현]
    # supervisor 에이전트가 개인 일정 요청을 위임하면 Nana 하위 에이전트가 내부 도구들을 프롬프트 기준으로 선택하게 하세요.
    #
    # [참고 답안]
    if not CONFIG.has_openai_key:
        return json.dumps(
            {
                "ok": False,
                "selected_agent": "nana_agent",
                "error": "missing_openai_api_key",
                "answer": "Nana 하위 에이전트는 프롬프트 기반 도구 호출로 동작하므로 OPENAI_API_KEY가 필요합니다.",
                "trace": [],
                "inner_tool_names": [],
                "mode": "prompt_driven_subagent",
            },
            ensure_ascii=False,
        )
    result = build_nana_subagent().invoke({"messages": [{"role": "user", "content": query}]})
    trace = _extract_agent_trace(result)
    return json.dumps(
        {
            "ok": True,
            "selected_agent": "nana_agent",
            "answer": _extract_final_text(result),
            "trace": trace,
            "inner_tool_names": _tool_call_names(trace),
            "mode": "prompt_driven_subagent",
        },
        ensure_ascii=False,
    )


@tool
def kana_agent(query: str) -> str:
    """그룹 일정 종합 작업을 프롬프트 기반 Kana 하위 에이전트에게 위임합니다."""

    # [6주차][학생 구현]
    # Kana 하위 에이전트가 MCP SQLite의 다른 사람 일정과 Nana의 개인 일정을 도구 호출로 종합하게 하세요.
    #
    # [참고 답안]
    if not CONFIG.has_openai_key:
        return json.dumps(
            {
                "ok": False,
                "selected_agent": "kana_agent",
                "error": "missing_openai_api_key",
                "answer": "Kana 하위 에이전트는 프롬프트 기반 도구 호출로 동작하므로 OPENAI_API_KEY가 필요합니다.",
                "trace": [],
                "inner_tool_names": [],
                "final_decision_payload": None,
                "mode": "prompt_driven_subagent",
            },
            ensure_ascii=False,
        )
    result = build_kana_subagent().invoke({"messages": [{"role": "user", "content": query}]})
    trace = _extract_agent_trace(result)
    final_decision = None
    for event in trace:
        content = event.get("content")
        if isinstance(content, dict) and content.get("final_decision"):
            final_decision = content["final_decision"]
    return json.dumps(
        {
            "ok": True,
            "selected_agent": "kana_agent",
            "answer": _extract_final_text(result),
            "trace": trace,
            "inner_tool_names": _tool_call_names(trace),
            "final_decision_payload": final_decision,
            "mode": "prompt_driven_subagent",
        },
        ensure_ascii=False,
    )


def build_langchain_supervisor_agent() -> object:
    """nana_agent와 kana_agent 위임 도구만 노출하는 LangChain v1 슈퍼바이저입니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("OPENAI_API_KEY가 .env에 필요합니다.")
    return create_agent(
        model=_chat_model(),
        tools=supervisor_tools(),
        system_prompt=supervisor_system_prompt(),
    )
