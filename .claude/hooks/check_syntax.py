#!/usr/bin/env python
"""H2 PostToolUse hook: syntax-check edited student_parts/*.py files.

Reads the Claude Code hook event JSON from stdin. When the edited file is a
`.py` under `student_parts/`, runs `py_compile` on it. On a syntax error the
hook exits 2 so the compiler message is fed back to Claude. Otherwise exit 0.

자동 테스트 하네스가 없는 저장소에서 편집 즉시 구문 오류를 잡기 위한 hook.
"""

from __future__ import annotations

import json
import py_compile
import sys

try:  # emit readable UTF-8 feedback regardless of the Windows console codepage
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def _target_path(event: dict) -> str:
    tool_input = event.get("tool_input") or {}
    return str(tool_input.get("file_path") or tool_input.get("path") or "")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    raw = _target_path(event)
    if not raw:
        return 0

    norm = raw.replace("\\", "/")
    if not norm.endswith(".py") or "student_parts/" not in norm:
        return 0

    try:
        py_compile.compile(raw, doraise=True)
    except py_compile.PyCompileError as exc:
        sys.stderr.write(f"py_compile 실패: {exc.msg}\n")
        return 2
    except FileNotFoundError:
        # File may have been moved/removed; do not block.
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
