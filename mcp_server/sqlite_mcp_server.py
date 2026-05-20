from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from fixed.config import CONFIG
from fixed.stores import ExternalPeopleSQLiteStore


DB_PATH = Path(os.getenv("KANANA_EXTERNAL_DB_PATH", str(CONFIG.external_db_path)))
STORE = ExternalPeopleSQLiteStore(DB_PATH)
mcp = FastMCP("kanana-sqlite-history")


@mcp.tool()
def search_previous_conversations(query: str, member_names: list[str] | None = None, limit: int = 5) -> str:
    """외부 Kanana SQLite 데이터베이스에서 이전 대화를 검색합니다."""

    rows = STORE.search_previous_conversations(query=query, member_names=member_names, limit=limit)
    return json.dumps({"ok": True, "tool_name": "search_previous_conversations", "rows": rows}, ensure_ascii=False)


@mcp.tool()
def load_conversation_messages(conversation_id: str) -> str:
    """특정 이전 대화의 모든 메시지를 불러옵니다."""

    rows = STORE.load_conversation_messages(conversation_id=conversation_id)
    return json.dumps({"ok": True, "tool_name": "load_conversation_messages", "rows": rows}, ensure_ascii=False)


@mcp.tool()
def extract_schedules_from_history(member_names: list[str], date_from: str, date_to: str) -> str:
    """이전 대화 기록에서 멤버별 일정을 추출합니다."""

    rows = STORE.extract_schedules_from_history(member_names=member_names, date_from=date_from, date_to=date_to)
    return json.dumps({"ok": True, "tool_name": "extract_schedules_from_history", "rows": rows}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
