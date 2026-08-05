from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from student_parts.week06.kana import kana_agent
from student_parts.week06.nana import nana_agent
from student_parts.week06.orchestrator.schemas import AgentTask, TaskResult


AgentName = Literal["nana", "kana"]


def route(task: AgentTask) -> AgentName:
    """도메인 판단값을 실제 에이전트 구현에 매핑한다."""

    return "kana" if task.requires_external_data else "nana"


def _decode_worker_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        decoded = json.loads(raw)
    elif isinstance(raw, dict):
        decoded = raw
    else:
        raise TypeError(f"worker 반환 타입이 JSON 문자열 또는 dict가 아닙니다: {type(raw).__name__}")

    if not isinstance(decoded, dict):
        raise TypeError("worker JSON의 최상위 값은 object여야 합니다.")
    return decoded


def invoke_worker(
    task: AgentTask,
    delegated_query: str,
    *,
    tools: Mapping[AgentName, Any] | None = None,
) -> TaskResult:
    """기존 Nana/Kana tool 호출을 예외 경계로 감싸 ``TaskResult``로 정규화한다."""

    agent = route(task)
    selected_tools = tools or {"nana": nana_agent, "kana": kana_agent}

    try:
        raw = selected_tools[agent].invoke({"query": delegated_query})
        payload = _decode_worker_payload(raw)
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("worker answer가 비어 있거나 문자열이 아닙니다.")

        trace = payload.get("trace") or []
        inner_tool_names = payload.get("inner_tool_names") or []
        if not isinstance(trace, list) or not all(isinstance(event, dict) for event in trace):
            raise TypeError("worker trace는 dict 목록이어야 합니다.")
        if not isinstance(inner_tool_names, list):
            raise TypeError("worker inner_tool_names는 목록이어야 합니다.")

        return TaskResult(
            task_id=task.id,
            agent=agent,
            status="ok",
            answer=answer.strip(),
            inner_tool_names=[str(name) for name in inner_tool_names],
            trace=trace,
            final_slot_payload=payload.get("final_slot_payload"),
            final_decision_payload=payload.get("final_decision_payload"),
        )
    except Exception as exc:
        return TaskResult(
            task_id=task.id,
            agent=agent,
            status="fail",
            error=f"{type(exc).__name__}: {exc}",
        )
