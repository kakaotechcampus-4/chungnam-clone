from __future__ import annotations

from collections import deque

from student_parts.week06.orchestrator.schemas import AgentTask, DecomposedPlan


class PlanValidationError(ValueError):
    """실행할 수 없는 id 또는 의존관계가 포함된 계획."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def topological_sort(plan: DecomposedPlan) -> list[AgentTask]:
    """계획 전체를 검증하고 안정적인 Kahn 순서로 반환한다.

    작업 실행 전에 전체 순서를 만들기 때문에 사이클 계획에서는 어떤 worker도
    호출되지 않는다.
    """

    tasks_by_id: dict[str, AgentTask] = {}
    for task in plan.tasks:
        if task.id in tasks_by_id:
            raise PlanValidationError(
                "duplicate_task_id",
                f"중복 task id: {task.id}",
            )
        tasks_by_id[task.id] = task

    indegree = {task.id: 0 for task in plan.tasks}
    dependents: dict[str, list[str]] = {task.id: [] for task in plan.tasks}

    for task in plan.tasks:
        seen_dependencies: set[str] = set()

        for dependency in task.dependencies:
            dependency_id = dependency.task_id
            if dependency_id in seen_dependencies:
                raise PlanValidationError(
                    "duplicate_dependency",
                    f"{task.id}에 중복 의존성이 있습니다: {dependency_id}",
                )

            seen_dependencies.add(dependency_id)

            if dependency_id not in tasks_by_id:
                raise PlanValidationError(
                    "unknown_dependency",
                    f"{task.id}가 존재하지 않는 task를 참조합니다: {dependency_id}",
                )

            if dependency_id == task.id:
                raise PlanValidationError(
                    "self_dependency",
                    f"{task.id}가 자기 자신을 참조합니다.",
                )

            indegree[task.id] += 1
            dependents[dependency_id].append(task.id)

    ready = deque(task.id for task in plan.tasks if indegree[task.id] == 0)
    ordered_ids: list[str] = []

    while ready:
        task_id = ready.popleft()
        ordered_ids.append(task_id)

        for dependent_id in dependents[task_id]:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)

    if len(ordered_ids) != len(plan.tasks):
        cycle_ids = [task.id for task in plan.tasks if indegree[task.id] > 0]
        raise PlanValidationError(
            "cyclic_dependency",
            f"순환 의존성이 감지되었습니다: {', '.join(cycle_ids)}",
        )

    return [tasks_by_id[task_id] for task_id in ordered_ids]
