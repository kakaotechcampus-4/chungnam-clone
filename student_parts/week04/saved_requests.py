from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from fixed.app_store import AppSQLiteStore
from student_parts.week04.common import json_payload, safe_limit
from student_parts.week04.schemas import SearchSavedRequestsInput
from student_parts.week04.stores import SQLITE_STORE

def search_saved_request_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """SQLite 저장 요청을 검색하고 실제 검색 결과만 반환합니다."""

    # TODO: AppSQLiteStore.search_saved_requests(...)로 저장 요청을 검색하세요.
    return sqlite_store.search_saved_requests(
    query=query,
    limit=top_k,
    )


@tool(args_schema=SearchSavedRequestsInput)
def search_saved_requests(query: str, top_k: int = 3) -> str:
    """save_request를 통해 구조화된 일정, 할 일, 알림 데이터를 검색합니다. conversations와 messages의 일반 대화 원문은 검색하지 않습니다."""
    # TODO: AppSQLiteStore.search_saved_requests(...)로 저장 요청을 검색하고 top-level rows를 반환하세요.
    limit=safe_limit(top_k,default=3,maximum=50)
    rows=search_saved_request_rows(
        SQLITE_STORE,
        query=query,
        top_k=limit
    )
    return json_payload({"rows":rows})
