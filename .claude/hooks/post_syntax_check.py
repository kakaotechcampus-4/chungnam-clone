#!/usr/bin/env python3
"""PostToolUse: student_parts/*.py 구문 오류 검사."""
import sys
import json
import py_compile

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

path = d.get("tool_input", {}).get("file_path", "")
if path.endswith(".py") and "student_parts" in path:
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        print(f"SyntaxError in {path}:", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)
