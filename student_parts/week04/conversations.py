from typing import Any
from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from student_parts.week04.conversation_rag_store import ConversationMessageRAGStore
from fixed.session_scope import DEFAULT_SESSION_SCOPE, current_session_scope
from student_parts.week04.common import json_payload, safe_limit
from student_parts.week04.schemas import SearchConversationMessagesInput
from student_parts.week04.stores import CONVERSATION_RAG_STORE, SQLITE_STORE
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command



def search_conversation_messages_dict(
    sqlite_store: AppSQLiteStore,
    conversation_rag_store: ConversationMessageRAGStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """SQLite 대화 목록을 lazy sync한 뒤 ChromaDB conversation RAG 결과를 반환합니다."""

    # TODO: SQLite 대화 기록을 ConversationRAGStore에 lazy sync한 뒤 현재 대화를 제외하고 검색하세요.
    limit = safe_limit(
        top_k,
        default=5,
        maximum=50,
    )

    sync_result = conversation_rag_store.sync_from_sqlite(
        sqlite_store
    )

    current_conversation_id = (
        conversation_id
        if conversation_id is not None
        else current_session_scope()
    )

    if current_conversation_id == DEFAULT_SESSION_SCOPE:
        current_conversation_id = None

    hits = conversation_rag_store.search(
        query=query,
        top_k=limit,
        exclude_conversation_id=current_conversation_id,
    )

    return {
        "hits": hits,
        "rows": hits,
        "context": conversation_rag_store.context_from_hits(hits),
        "rag_backend": conversation_rag_store.backend_info(),
        "sync": sync_result,
    }


def search_conversation_message_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """앱 SQLite에 저장된 일반 채팅 대화 청크를 RAG 검색합니다."""

    # TODO: search_conversation_messages_dict(...) 결과에서 hits만 반환하세요.
    payload = search_conversation_messages_dict(
        sqlite_store,
        CONVERSATION_RAG_STORE,
        query=query,
        top_k=top_k,
        conversation_id=conversation_id,
    )

    return list(payload.get("hits") or [])


@tool(args_schema=SearchConversationMessagesInput)
def search_conversation_messages(
    runtime: ToolRuntime,
    query: str,
    top_k: int = 5,
) -> Command[Any]:
    """conversations와 messages에 저장된 과거 대화 원문을 메시지 단위로 검색합니다. 구조화된 일정, 할 일, 알림 row는 검색하지 않습니다."""

    payload = search_conversation_messages_dict(
        SQLITE_STORE,
        CONVERSATION_RAG_STORE,
        query=query,
        top_k=top_k,
        conversation_id=current_session_scope(),
    )

    hit_conversation_ids = {
        str(hit["conversation_id"])
        for hit in payload.get("hits", [])
        if hit.get("conversation_id")
    }

    return Command(
        update={
            "allowed_conversation_ids": hit_conversation_ids,
            "messages": [
                ToolMessage(
                    content=json_payload(payload),
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )
