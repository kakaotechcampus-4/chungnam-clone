from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from student_parts.week06.orchestrator.schemas import AgentTask, TaskResult
from student_parts.week06.orchestrator.workers import invoke_worker, route


Worker = Callable[[AgentTask, str], TaskResult]


def build_delegated_query(
    task: AgentTask,
    results_by_id: dict[str, TaskResult],
    *,
    expects_condition: bool = False,
) -> str:
    """현재 작업 query에 직접 의존한 선행 결과만 주입한다."""

    if (
        not task.dependencies
        and not task.external_members
        and not expects_condition
    ):
        return task.query

    blocks: list[str] = []

    if task.external_members:
        blocks.extend(
            [
                "[외부 데이터 대상 멤버]",
                ", ".join(task.external_members),
            ]
        )

    if task.dependencies:
        blocks.append("[선행 작업 결과]")
        for dependency in task.dependencies:
            dependency_id = dependency.task_id
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
                    + json.dumps(
                        result.final_slot_payload,
                        ensure_ascii=False,
                        default=str,
                    )
                )
            if result.final_decision_payload is not None:
                lines.append(
                    "final_decision_payload: "
                    + json.dumps(
                        result.final_decision_payload,
                        ensure_ascii=False,
                        default=str,
                    )
                )
            blocks.append("\n".join(lines))

    if expects_condition:
        blocks.extend(
            [
                "[조건 판정 작업]",
                (
                    "도구 결과를 근거로 현재 작업의 질문이 참이면 "
                    "condition_value=true, 거짓이면 false로 반환한다. "
                    "판정할 근거가 부족하면 추측하지 말고 null을 반환한다."
                ),
            ]
        )

    blocks.extend(["[현재 작업]", task.query])
    return "\n\n".join(blocks)


def execute_tasks(
    ordered_tasks: Sequence[AgentTask],
    worker: Worker = invoke_worker,
) -> list[TaskResult]:
    """위상순서대로 실행하고 의존성 및 조건부 실행을 처리한다."""

    results_by_id: dict[str, TaskResult] = {}
    ordered_results: list[TaskResult] = []

    condition_task_ids = {
        dependency.task_id
        for task in ordered_tasks
        for dependency in task.dependencies
        if dependency.type == "run_only_if"
    }

    for task in ordered_tasks:
        blocked_dependencies = [
            dependency.task_id
            for dependency in task.dependencies
            if results_by_id[dependency.task_id].status != "ok"
        ]

        skip_error: str | None = None

        if blocked_dependencies:
            skip_error = (
                "선행 작업 실패 또는 보류로 실행하지 않았습니다: "
                + ", ".join(blocked_dependencies)
            )
        else:
            for dependency in task.dependencies:
                if dependency.type != "run_only_if":
                    continue

                condition_result = results_by_id[dependency.task_id]
                condition_value = condition_result.condition_value

                if condition_value is None:
                    skip_error = (
                        "선행 작업이 조건값을 반환하지 않아 실행하지 않았습니다: "
                        + dependency.task_id
                    )
                    break
                if condition_value != dependency.equals:
                    skip_error = (
                        "실행 조건이 충족되지 않아 실행하지 않았습니다: "
                        f"{dependency.task_id}="
                        f"{condition_value}, expected={dependency.equals}"
                    )
                    break

        if skip_error is not None:
            result = TaskResult(
                task_id=task.id,
                agent=route(task),
                status="skipped",
                error=skip_error,
            )
        else:
            delegated_query = build_delegated_query(
                task,
                results_by_id,
                expects_condition=task.id in condition_task_ids,
            )

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
            elif (
                result.status == "ok"
                and task.id in condition_task_ids
                and result.condition_value is None
            ):
                result = result.model_copy(
                    update={
                        "status": "fail",
                        "error": (
                            "조건 판정 작업이 condition_value를 "
                            "반환하지 않았습니다."
                        ),
                    }
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
                "condition_value": result.condition_value,
                "error": result.error,
            }
        )
    return accumulated
