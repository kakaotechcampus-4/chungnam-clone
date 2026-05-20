from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from langchain.tools import tool

from fixed.config import CONFIG, PACKAGE_ROOT
from fixed.stores import ExternalPeopleSQLiteStore


EXTERNAL_STORE = ExternalPeopleSQLiteStore(CONFIG.external_db_path)


@tool
def search_previous_conversations(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> str:
    """외부 SQLite 데이터베이스에 저장된 이전 대화를 검색합니다."""

    # [5주차][학생 구현]
    # 에이전트 코드가 DB를 직접 뒤지지 않고 MCP SQLite 도구를 호출해 이전 대화를 검색하도록 만드세요.
    #
    # [참고 답안]
    rows = EXTERNAL_STORE.search_previous_conversations(query=query, member_names=member_names or None, limit=limit)
    return json.dumps({"ok": True, "tool_name": "search_previous_conversations", "rows": rows}, ensure_ascii=False)


@tool
def load_conversation_messages(conversation_id: str) -> str:
    """외부 SQLite 데이터베이스에서 특정 이전 대화의 모든 메시지를 불러옵니다."""

    # [5주차][학생 구현]
    # conversation_id로 외부 SQLite 대화 메시지를 시간순으로 조회하세요.
    #
    # [참고 답안]
    rows = EXTERNAL_STORE.load_conversation_messages(conversation_id=conversation_id)
    return json.dumps({"ok": True, "tool_name": "load_conversation_messages", "rows": rows}, ensure_ascii=False)


@tool
def extract_schedules_from_history(member_names: list[str], date_from: str, date_to: str) -> str:
    """외부 SQLite 이전 대화에서 멤버별 일정을 추출합니다."""

    # [5주차][학생 구현]
    # 멤버 이름과 날짜 범위로 외부 SQLite에 저장된 각자의 일정을 추출하세요.
    #
    # [참고 답안]
    rows = EXTERNAL_STORE.extract_schedules_from_history(
        member_names=member_names,
        date_from=date_from,
        date_to=date_to,
    )
    return json.dumps({"ok": True, "tool_name": "extract_schedules_from_history", "rows": rows}, ensure_ascii=False)


async def load_langchain_mcp_tools() -> list[Any]:
    """LangChain MCP 어댑터로 로컬 MCP 서버의 SQLite 도구를 불러옵니다."""

    from langchain_mcp_adapters.client import MultiServerMCPClient

    server_path = PACKAGE_ROOT / "mcp_server" / "sqlite_mcp_server.py"
    env = os.environ.copy()
    env["KANANA_EXTERNAL_DB_PATH"] = str(CONFIG.external_db_path)
    client = MultiServerMCPClient(
        {
            "kanana_sqlite": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server_path)],
                "env": env,
            }
        }
    )
    return await client.get_tools()


def load_langchain_mcp_tools_sync() -> list[Any]:
    try:
        return asyncio.run(load_langchain_mcp_tools())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(load_langchain_mcp_tools())
        finally:
            loop.close()


def search_previous_conversations_dict(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return json.loads(
        search_previous_conversations.invoke({"query": query, "member_names": member_names, "limit": limit})
    )["rows"]


def extract_schedules_from_history_dict(member_names: list[str], date_from: str, date_to: str) -> list[dict[str, Any]]:
    return json.loads(
        extract_schedules_from_history.invoke(
            {"member_names": member_names, "date_from": date_from, "date_to": date_to}
        )
    )["rows"]
