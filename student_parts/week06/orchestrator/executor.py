from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from student_parts.week06.orchestrator.schemas import AgentTask, TaskResult
from student_parts.week06.orchestrator.workers import invoke_worker, route


Worker = Callable[[AgentTask, str], TaskResult]


def build_delegated_query(
    task: AgentTask,
    results_by_id: dict[str, TaskResult],
) -> str:
    """현재 작업 query에 직접 의존한 선행 결과만 주입한다."""

    if not task.depends_on and not task.external_members:
        return task.query

    blocks: list[str] = []
    if task.external_members:
        blocks.extend(
            [
                "[외부 데이터 대상 멤버]",
                ", ".join(task.external_members),
            ]
        )

    if task.depends_on:
        blocks.append("[선행 작업 결과]")
        for dependency_id in task.depends_on:
            result = results_by_id[dependency_id]
            lines = [
                f"task_id: {result.task_id}",
                f"agent: {result.agent}",
                f"status: {result.status}",
                f"answer: {result.answer or ''}",
            ]
            if result.final_slot_payload is not None:
                lines.append(
                    "final_slot_payload: "
                    + json.dumps(result.final_slot_payload, ensure_ascii=False, default=str)
                )
            if result.final_decision_payload is not None:
                lines.append(
                    "final_decision_payload: "
                    + json.dumps(result.final_decision_payload, ensure_ascii=False, default=str)
                )
            blocks.append("\n".join(lines))

    blocks.extend(["[현재 작업]", task.query])
    return "\n\n".join(blocks)


def execute_tasks(
    ordered_tasks: Sequence[AgentTask],
    worker: Worker = invoke_worker,
) -> list[TaskResult]:
    """위상순서대로 한 번씩 실행하고 fail/skip을 후손에게 전파한다."""

    results_by_id: dict[str, TaskResult] = {}
    ordered_results: list[TaskResult] = []

    for task in ordered_tasks:
        blocked_dependencies = [
            dependency_id
            for dependency_id in task.depends_on
            if results_by_id[dependency_id].status != "ok"
        ]

        if blocked_dependencies:
            result = TaskResult(
                task_id=task.id,
                agent=route(task),
                status="skipped",
                error=(
                    "선행 작업 실패 또는 보류로 실행하지 않았습니다: "
                    + ", ".join(blocked_dependencies)
                ),
            )
        else:
            delegated_query = build_delegated_query(task, results_by_id)
            try:
                result = worker(task, delegated_query)
            except Exception as exc:
                result = TaskResult(
                    task_id=task.id,
                    agent=route(task),
                    status="fail",
                    error=f"{type(exc).__name__}: {exc}",
                )

            if result.task_id != task.id or result.agent != route(task):
                result = TaskResult(
                    task_id=task.id,
                    agent=route(task),
                    status="fail",
                    error="worker가 현재 task와 일치하지 않는 TaskResult를 반환했습니다.",
                )

        results_by_id[task.id] = result
        ordered_results.append(result)

    return ordered_results


def accumulate_trace(task_results: Sequence[TaskResult]) -> list[dict]:
    """작업 순서대로 trace를 합치고 각 이벤트의 출처를 표시한다."""

    accumulated: list[dict] = []
    for result in task_results:
        for event in result.trace:
            accumulated.append(
                {
                    **event,
                    "orchestrator_task_id": result.task_id,
                    "orchestrator_agent": result.agent,
                }
            )
        accumulated.append(
            {
                "event": "orchestrator_task_result",
                "task_id": result.task_id,
                "agent": result.agent,
                "status": result.status,
                "error": result.error,
            }
        )
    return accumulated
