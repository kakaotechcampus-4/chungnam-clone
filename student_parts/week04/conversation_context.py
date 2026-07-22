from typing import Any
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from student_parts.week04.common import json_payload
from student_parts.week04.schemas import LoadConversationContextInput
from student_parts.week04.stores import SQLITE_STORE

def load_conversation_context_dict(
    sqlite_store: AppSQLiteStore,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    with sqlite_store.connect() as conn:
        conversation_row = conn.execute(
            """
            SELECT
                conversation_id,
                title,
                status,
                created_at,
                updated_at
            FROM conversations
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()

        if conversation_row is None:
            return {
                "ok": False,
                "error": "conversation_not_found",
                "conversation_id": conversation_id,
            }

        message_rows = conn.execute(
            """
            SELECT
                message_id,
                conversation_id,
                role,
                content,
                created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (conversation_id,),
        ).fetchall()

    conversation = dict(conversation_row)
    conversation["messages"] = [
        dict(message)
        for message in message_rows
    ]

    return {
        "ok": True,
        "conversation": conversation,
    }


@tool(args_schema=LoadConversationContextInput)
def load_conversation_context(
    runtime: ToolRuntime,
    conversation_id: str,
) -> str:
    """검색 결과로 허용된 이전 대화방의 전체 메시지 문맥을 불러옵니다."""

    normalized_id = str(conversation_id or "").strip()
    allowed_ids = runtime.state.get(
        "allowed_conversation_ids",
        set(),
    )

    if normalized_id not in allowed_ids:
        return json_payload(
            {
                "ok": False,
                "error": "conversation_id_not_allowed",
                "conversation_id": normalized_id,
            }
        )

    payload = load_conversation_context_dict(
        SQLITE_STORE,
        conversation_id=normalized_id,
    )

    return json_payload(payload)