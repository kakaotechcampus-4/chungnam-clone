from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from fixed.session_scope import current_session_scope
from student_parts.week04.common import json_payload
from student_parts.week04.conversations import (
    search_conversation_messages_dict,
)
from student_parts.week04.memory_router import route_memory_query
from student_parts.week04.references import (
    search_personal_reference_hits,
)
from student_parts.week04.saved_requests import (
    search_saved_request_rows,
)
from student_parts.week04.stores import (
    CONVERSATION_RAG_STORE,
    REFERENCE_STORE,
    SQLITE_STORE,
)


def latest_user_question(state: dict[str, Any]) -> str:
    """현재 state에서 가장 최근 사용자 질문을 반환합니다."""

    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()

    return ""


@tool
def retrieve_memory(
    runtime: ToolRuntime,
) -> Command[Any]:
    """구조화 기록, 일반 대화, 개인 참고자료 중 적절한 출처를 선택해 검색합니다."""

    question = latest_user_question(runtime.state)

    if not question:
        return Command(
            update={
                "allowed_conversation_ids": set(),
                "messages": [
                    ToolMessage(
                        content=json_payload(
                            {
                                "ok": False,
                                "error": "user_question_not_found",
                            }
                        ),
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )

    route = route_memory_query(question)
    allowed_conversation_ids: set[str] = set()

    if route.source == "structured":
        rows = search_saved_request_rows(
            SQLITE_STORE,
            query=route.search_query,
            top_k=3,
        )

        payload = {
            "source": route.source,
            "search_query": route.search_query,
            "rows": rows,
        }

    elif route.source == "reference":
        hits = search_personal_reference_hits(
            REFERENCE_STORE,
            query=route.search_query,
            top_k=2,
        )

        payload = {
            "source": route.source,
            "search_query": route.search_query,
            "hits": hits,
        }

    else:
        conversation_payload = search_conversation_messages_dict(
            SQLITE_STORE,
            CONVERSATION_RAG_STORE,
            query=route.search_query,
            top_k=5,
            conversation_id=current_session_scope(),
        )

        allowed_conversation_ids = {
            str(hit["conversation_id"])
            for hit in conversation_payload.get("hits", [])
            if hit.get("conversation_id")
        }

        payload = {
            **conversation_payload,
            "source": route.source,
            "search_query": route.search_query,
        }

    return Command(
        update={
            "allowed_conversation_ids": allowed_conversation_ids,
            "messages": [
                ToolMessage(
                    content=json_payload(payload),
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )