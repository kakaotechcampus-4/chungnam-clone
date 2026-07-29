from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.config import CONFIG
from fixed.conversation_rag_store import ConversationRAGStore
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from fixed.app_store import AppSQLiteStore
from fixed.reference_store import PersonalReferenceStore
from fixed.session_scope import DEFAULT_SESSION_SCOPE, current_session_scope
from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week03_build_nanas_logbook import week03_prompt_parts, week03_tools


REFERENCE_STORE = PersonalReferenceStore(CONFIG.chroma_dir)
SQLITE_STORE = AppSQLiteStore(CONFIG.app_db_path)
CONVERSATION_RAG_STORE = ConversationRAGStore(CONFIG.chroma_dir)
_WEEK04_AGENT: Any | None = None


def _decode_attendees(raw_attendees: str | None) -> list[str]:
    try:
        decoded = json.loads(raw_attendees or "[]")
    except Exception:
        return []
    return decoded if isinstance(decoded, list) else []


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


def safe_limit(limit: int, default: int = 5, maximum: int = 50) -> int:
    """사용자/LLM이 넘긴 limit 값을 안전한 양의 정수 범위로 보정합니다."""

    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


class AddPersonalReferenceInput(BaseModel):
    """
    개인 참고자료 추가 입력입니다.

    입력 필드:

    ```python
    title: str
    content: str
    tags: list[str] | None = None
    ```
    """

    title: str
    content: str
    tags: list[str] | None = None


class SearchPersonalReferencesInput(BaseModel):
    """
    개인 참고자료 검색 입력입니다.

    입력 필드:

    ```python
    query: str
    top_k: int = Field(default=2, ge=1, le=20)
    ```
    """

    query: str
    top_k: int = Field(default=2, ge=1, le=20)


class SearchSavedRequestsInput(BaseModel):
    """
    SQLite 저장 요청 검색 입력입니다.

    입력 필드:

    ```python
    query: str
    top_k: int = Field(default=3, ge=1, le=50)
    ```
    """

    query: str
    top_k: int = Field(default=3, ge=1, le=50)


class SearchConversationMessagesInput(BaseModel):
    """
    앱 대화 RAG 검색 입력입니다.

    입력 필드:

    ```python
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    conversation_id: str | None = None
    ```
    """

    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    conversation_id: str | None = None


class SearchNanaMemoryInput(BaseModel):
    """
    Week 4 호환 통합 검색 입력입니다.

    입력 필드:

    ```python
    query: str
    date_from: str | None = None
    date_to: str | None = None
    attendee: str | None = None
    limit: int = Field(default=5, ge=1, le=20)
    ```
    """

    query: str
    date_from: str | None = None
    date_to: str | None = None
    attendee: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


def add_personal_reference_dict(
    reference_store: PersonalReferenceStore,
    *,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """
    개인 참고자료를 vector store에 추가하고 저장 결과를 그대로 반환합니다.

    반환 형식
    ```python
        {
            "reference_id": str,
            "title": str,
            "content": str,
            "tags": list[str],
            "backend": dict[str, Any],
        }
    ```
    """

    store_result = reference_store.add_personal_reference(
        title=title,
        content=content,
        tags=tags or [],
    )
    return store_result


def search_personal_reference_hits(
    reference_store: PersonalReferenceStore,
    *,
    query: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """
    ChromaDB 개인 참고자료 검색 결과를 tool이 사용할 hit 구조로 정리합니다.

    반환 list의 각 dict 형식:

    ```python
    {
        "id": str,
        "content": str,
        "distance": float,
        "metadata": {
            "title": str,
            "tags": str,
        },
    }
    ```
    """
    search_results: list[dict[str, Any]] = reference_store.search_personal_references(
        query=query,
        limit=top_k,
    )

    hits: list[dict[str, Any]] = []
    for result in search_results:
        hits.append(
            {
                "id": result["id"],
                "content": result["content"],
                "distance": result["distance"],
                "metadata": {
                    "title": result["title"],
                    "tags": result["tags"],
                },
            }
        )

    return hits


def search_saved_request_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """SQLite 저장 요청을 검색하고 실제 검색 결과만 반환합니다."""

    return sqlite_store.search_saved_requests(query=query, kind=None, limit=top_k)


def search_conversation_messages_dict(
    sqlite_store: AppSQLiteStore,
    conversation_rag_store: ConversationRAGStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    SQLite 대화 목록을 lazy sync한 뒤 ChromaDB conversation RAG 결과를 반환합니다.

    반환 형식
    ```python
    {
        "hits": list[dict[str, Any]],
        "rows": list[dict[str, Any]],
        "context": str,
        "rag_backend": dict[str, Any],
        "sync": dict[str, Any],
    }
    """

    # SQLite 대화 기록을 ConversationRAGStore에 lazy sync한 뒤 현재 대화를 제외하고 검색.
    sync = conversation_rag_store.sync_from_sqlite(sqlite_store)

    if conversation_id:
        hits = conversation_rag_store.search(
            query=query,
            top_k=top_k,
            conversation_id=conversation_id,
        )
    else:
        hits = conversation_rag_store.search(
            query=query,
            top_k=top_k,
            exclude_conversation_id=current_session_scope(),
        )
    return {
        "hits": hits,
        "rows": hits,
        "context": conversation_rag_store.context_from_hits(hits),
        "rag_backend": conversation_rag_store.backend_info(),
        "sync": sync,
    }


def search_conversation_message_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """앱 SQLite에 저장된 일반 채팅 대화 청크를 RAG 검색합니다."""

    # search_conversation_messages_dict(...) 결과에서 hits만 반환.
    return search_conversation_messages_dict(
        sqlite_store,
        CONVERSATION_RAG_STORE,
        query=query,
        top_k=top_k,
        conversation_id=conversation_id,
    )["hits"]


@tool(args_schema=AddPersonalReferenceInput)
def add_personal_reference(
    title: str, content: str, tags: list[str] | None = None
) -> str:
    """개인 참고자료를 ChromaDB에 추가합니다."""

    store_result: dict[str, Any] = add_personal_reference_dict(
        REFERENCE_STORE,
        title=title,
        content=content,
        tags=tags,
    )

    # "backend" key가 없다면 에러 나도록
    reference_backend = store_result.pop("backend")

    return json_payload(
        {
            "reference_backend": reference_backend,
            "reference": store_result,
        }
    )


@tool(args_schema=SearchPersonalReferencesInput)
def search_personal_references(query: str, top_k: int = 2) -> str:
    """
    사용자가 저장한 선호, 규칙, 메모, 개인 참고자료를 검색합니다.
    실제로 등록된 일정,할 일,알림 검색에는 사용하지 않습니다.
    """
    top_k = safe_limit(top_k, default=2, maximum=20)
    hits: list[dict[str, Any]] = search_personal_reference_hits(
        REFERENCE_STORE,
        query=query,
        top_k=top_k,
    )

    return json_payload({"hits": hits})


@tool(args_schema=SearchSavedRequestsInput)
def search_saved_requests(query: str, top_k: int = 3) -> str:
    """
    SQLite에 저장된 구조화 일정/할 일/알림 row를 검색합니다. query에는 LLM이 고른 일정/할 일/알림 핵심어를 넣습니다.
    사용자가 저장한 선호, 규칙, 메모, 개인 참고자료 검색에는 사용하지 않습니다.
    """

    top_k = safe_limit(top_k, default=3, maximum=50)
    rows: list[dict[str, Any]] = search_saved_request_rows(
        SQLITE_STORE,
        query=query,
        top_k=top_k,
    )

    return json_payload({"rows": rows})


@tool(args_schema=SearchConversationMessagesInput)
def search_conversation_messages(
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> str:
    """
    앱 SQLite 대화 목록을 대화 단위 ChromaDB RAG로 검색합니다. query에는 LLM이 고른 짧은 핵심 명사나 구를 넣습니다.
    개인 참고자료나 구조화된 일정 기록을 검색하는 Tool이 아닙니다.
    """

    # SQLite 대화 목록을 대화 단위(conversation_id)로 ChromaDB RAG에 검색하고 JSON 문자열로 반환.
    result = search_conversation_messages_dict(
        SQLITE_STORE,
        CONVERSATION_RAG_STORE,
        query=query,
        top_k=top_k,
        conversation_id=conversation_id,
    )
    return json_payload(result)


@tool(args_schema=SearchNanaMemoryInput)
def search_nana_memory(
    query: str,
    date_from: str | None = None,
    date_to: str | None = None,
    attendee: str | None = None,
    limit: int = 5,
) -> str:
    """개인 참고자료와 SQLite 저장 일정을 한 번에 검색하고 일정 chunk를 반환합니다."""

    # compatibility 통합 검색이 필요, 개인 참고자료와 SQLite 일정 chunk를 함께 구성
    normalized_limit = safe_limit(limit, default=5, maximum=20)
    reference_hits = REFERENCE_STORE.search_personal_references(
        query=query, limit=min(normalized_limit, 5)
    )

    clauses: list[str] = []
    params: list[Any] = []
    if query.strip():
        clauses.append(
            "(title LIKE ? OR date LIKE ? OR start_time LIKE ? OR end_time LIKE ? OR attendees_json LIKE ?)"
        )
        token = f"%{query.strip()}%"
        params.extend([token, token, token, token, token])
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if attendee:
        clauses.append("attendees_json LIKE ?")
        params.append(f"%{attendee}%")

    sql = """
        SELECT schedule_id, request_id, owner, title, date, start_time, end_time,
               attendees_json, source, created_at
        FROM schedules
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += """
        ORDER BY (date IS NULL), date ASC, (start_time IS NULL), start_time ASC, created_at DESC
        LIMIT ?
    """
    params.append(normalized_limit)

    with SQLITE_STORE.connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]

    schedule_chunks: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        raw_attendees = row.pop("attendees_json", "[]")
        attendees = _decode_attendees(raw_attendees)
        schedule_id = row.get("schedule_id") or f"schedule_{index}"
        start_time = row.get("start_time") or "시간 미정"
        end_time = row.get("end_time")
        time_range = f"{start_time}-{end_time}" if end_time else start_time
        attendee_text = ", ".join(attendees) if attendees else "참석자 미정"
        date = row.get("date") or "날짜 미정"
        title = row.get("title") or "제목 없음"
        schedule_chunks.append(
            {
                "chunk_id": f"schedule:{schedule_id}:0",
                "schedule_id": schedule_id,
                "title": title,
                "date": row.get("date"),
                "time_range": time_range,
                "attendees": attendees,
                "content": f"{date} {time_range} | {title} | 참석자: {attendee_text}",
                "metadata": {
                    "request_id": row.get("request_id"),
                    "owner": row.get("owner"),
                    "source": row.get("source"),
                    "created_at": row.get("created_at"),
                },
            }
        )

    lines = ["[개인 참고자료]"]
    for hit in reference_hits:
        lines.append(f"- {hit.get('title', '참고자료')}: {hit.get('content')}")
    lines.append("[SQLite 일정 chunk]")
    if not schedule_chunks:
        lines.append("- 검색된 저장 일정이 없습니다.")
    for chunk in schedule_chunks:
        source = (chunk.get("metadata") or {}).get("source") or "unknown"
        lines.append(
            f"- {chunk.get('chunk_id')} | {chunk.get('content')} | source={source}"
        )
    context = "\n".join(lines)
    return json.dumps(
        {
            "ok": True,
            "tool_name": "search_nana_memory",
            "reference_backend": REFERENCE_STORE.backend_info(),
            "reference_hits": reference_hits,
            "schedule_chunks": schedule_chunks,
            "context": context,
        },
        ensure_ascii=False,
    )


def week04_tools() -> list[Any]:
    """3주차까지의 도구에 4주차 RAG 도구를 누적한 목록입니다."""

    return [
        *week03_tools(),
        add_personal_reference,
        search_personal_references,
        search_saved_requests,
        search_conversation_messages,
    ]


def week04_system_prompt() -> str:
    """4주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week04_prompt_parts())


def week04_prompt_parts() -> list[str]:
    """1~4주차 system prompt 조각을 누적합니다."""

    return [
        *week03_prompt_parts(),
        """

        너는 Week 4 Nana memory agent다.

        Week 3 지침의 "RAG 검색은 수행하지 않는다"는 제한은 Week 4부터 적용하지 않는다.
        Week 4에서는 사용자의 질문에 따라 다음 세 가지 데이터 출처를 구분해서 검색한다.

        1. 사용자가 직접 남긴 선호, 메모, 개인 참고자료를 찾을 때는
           search_personal_references를 사용한다.

        2. SQLite에 저장된 일정, 할 일, 알림 같은 구조화 기록을 찾을 때는
           search_saved_requests를 사용한다.

        3. 사용자가 "예전에 내가 뭐라고 했지?"처럼 과거 채팅 발화를 찾을 때는
           search_conversation_messages를 사용한다.

        사용자가 개인 참고자료를 새로 기억하거나 저장해 달라고 명확히 요청하면
        add_personal_reference를 사용한다. 사용자의 명시적인 요청 없이 참고자료를
        임의로 추가하지 않는다.

        질문이 여러 출처와 관련되면 필요한 검색 tool을 모두 호출한다.
        검색 query에는 질문 전체보다 일정 이름, 인물, 주제 같은 핵심 단어나 짧은 구를 넣는다.

        tool이 반환한 실제 hits, rows, context를 답변의 근거로 사용한다.
        검색 결과가 없으면 내용을 추측하거나 만들어내지 말고 찾지 못했다고 알린다.
        과거 assistant 발화만으로 사용자의 사실이나 선호를 확정하지 않는다.
        """,
    ]


def build_week04_agent() -> object:
    """Week 1-4 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK04_AGENT
    if _WEEK04_AGENT is None:
        _WEEK04_AGENT = create_agent(
            model=chat_model(),
            tools=week04_tools(),
            system_prompt=week04_system_prompt(),
        )
    return _WEEK04_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week04_agent()
