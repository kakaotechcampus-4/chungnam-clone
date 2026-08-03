from __future__ import annotations

import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from mcp_server.sqlite_mcp_server import STORE, mcp


@mcp.tool()
def search_previous_conversations_with_messages(
    query: str,
    member_names: list[str] | None = None,
    limit: int = 5,
    include_messages: bool = False,
) -> str:
    """이전 대화를 검색하고 필요하면 검색된 대화방의 전체 메시지도 반환합니다."""

    rows = STORE.search_previous_conversations(
        query=query,
        member_names=member_names,
        limit=limit,
    )

    if not include_messages:
        return json.dumps({"rows": rows}, ensure_ascii=False)

    expanded_rows = []
    loaded_ids: set[str] = set()

    for row in rows:
        conversation_id = str(row.get("conversation_id") or "").strip()

        if not conversation_id or conversation_id in loaded_ids:
            continue

        loaded_ids.add(conversation_id)

        expanded_rows.append(
            {
                **row,
                "messages": STORE.load_conversation_messages(
                    conversation_id=conversation_id,
                ),
            }
        )

    return json.dumps({"rows": expanded_rows}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")