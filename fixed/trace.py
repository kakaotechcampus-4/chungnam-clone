from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


def to_jsonable(value: Any) -> Any:
    """일반 Python 객체를 Gradio JSON 컴포넌트가 표시할 수 있는 값으로 변환합니다."""

    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    try:
        return json.loads(value)
    except Exception:
        return str(value)


@dataclass
class TraceCollector:
    """수업용 헬퍼와 LangChain 도구에서 사용하는 간단한 trace 수집기입니다."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def add(self, event: str, **payload: Any) -> dict[str, Any]:
        item = {
            "ts": round(time.time(), 3),
            "event": event,
            **{key: to_jsonable(value) for key, value in payload.items()},
        }
        self.events.append(item)
        return item

    def tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self.add("tool_call", tool_name=tool_name, arguments=arguments)

    def tool_result(self, tool_name: str, content: Any) -> None:
        self.add("tool_result", tool_name=tool_name, content=content)

    def section(self, name: str, payload: Any) -> None:
        self.add(name, payload=payload)

    def as_dict(self) -> dict[str, Any]:
        return {"events": self.events}

    def summary_lines(self, limit: int = 8) -> list[str]:
        lines: list[str] = []
        for event in self.events[-limit:]:
            if event["event"] == "tool_call":
                lines.append(f"tool_call: {event.get('tool_name')}")
            elif event["event"] == "tool_result":
                lines.append(f"tool_result: {event.get('tool_name')}")
            else:
                lines.append(event["event"])
        return lines or ["trace 없음"]
