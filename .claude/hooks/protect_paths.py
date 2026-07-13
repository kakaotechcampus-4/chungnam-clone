#!/usr/bin/env python
"""H1 PreToolUse hook: block edits to the read-only `fixed/` directory.

Reads the Claude Code hook event JSON from stdin, extracts the target file
path, and blocks (exit 2) when the path lives under a top-level `fixed/`
directory. Kanana 과제 규칙: `fixed/` 제공 코드는 읽기 전용이다.
"""

from __future__ import annotations

import json
import sys

try:  # emit readable UTF-8 feedback regardless of the Windows console codepage
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def _target_path(event: dict) -> str:
    tool_input = event.get("tool_input") or {}
    # Edit/Write use file_path; be lenient about other shapes.
    return str(tool_input.get("file_path") or tool_input.get("path") or "")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Fail open: if we cannot parse the event, do not block.
        return 0

    raw = _target_path(event)
    if not raw:
        return 0

    # Normalize separators and split into path segments.
    segments = raw.replace("\\", "/").split("/")
    if "fixed" in segments:
        sys.stderr.write(
            f"Blocked: '{raw}' 는 읽기 전용 fixed/ 디렉토리에 속합니다. "
            "fixed/ 제공 코드는 수정하지 마세요 (student_parts만 편집).\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
