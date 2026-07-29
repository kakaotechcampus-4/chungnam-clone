# student_parts/week05/sync_retry.py
from typing import Any

import fixed.external_mcp as external_mcp


_original_call = external_mcp.call_external_tool_payload


def call_with_retry(
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    try:
        return _original_call(tool_name, args)
    except Exception:
        if tool_name not in {"create_shared_schedule", "delete_shared_schedule"}:
            raise

        return _original_call(tool_name, args)  # 딱 한 번 재시도


external_mcp.call_external_tool_payload = call_with_retry