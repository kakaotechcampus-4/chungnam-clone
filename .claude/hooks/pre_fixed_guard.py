#!/usr/bin/env python3
"""PreToolUse: fixed/ 폴더 수정 차단."""
import sys
import json

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

path = d.get("tool_input", {}).get("file_path", "")
if "fixed/" in path:
    print("blocked: fixed/ 폴더는 수정할 수 없습니다.", file=sys.stderr)
    sys.exit(2)
