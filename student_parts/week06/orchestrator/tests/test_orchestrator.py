from __future__ import annotations

import json
import unittest

from student_parts.week06.orchestrator.entry import (
    Orchestrator,
    OrchestratorAgentAdapter,
    extract_langchain_trace,
)
from student_parts.week06.orchestrator.executor import build_delegated_query, execute_tasks
from student_parts.week06.orchestrator.graph import PlanValidationError, topological_sort
from student_parts.week06.orchestrator.planner import LLMPlanner
from student_parts.week06.orchestrator.schemas import (
    AgentTask,
    DecomposedPlan,
    RunOnlyIfDependency,
    TaskDependency,
    TaskResult,
    UseResultDependency,
)
from student_parts.week06.orchestrator.workers import invoke_worker, route


def task(
    task_id: str,
    *,
    external: bool = False,
    dependencies: list[TaskDependency] | None = None,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        requires_external_data=external,
        external_members=["민준"] if external else [],
        query=f"{task_id} 실행",
        dependencies=dependencies or [],
    )


def use_result(task_id: str) -> UseResultDependency:
    return UseResultDependency(task_id=task_id)


def run_only_if(task_id: str, *, equals: bool = True) -> RunOnlyIfDependency:
    return RunOnlyIfDependency(task_id=task_id, equals=equals)


class FakePlanner:
    def __init__(self, plan: DecomposedPlan) -> None:
        self._plan = plan
        self.inputs: list[str] = []

    def plan(self, user_request: str) -> DecomposedPlan:
        self.inputs.append(user_request)
        return self._plan


class FakeComposer:
    def __init__(self, answer: str = "종합 답변") -> None:
        self.answer = answer
        self.calls = 0

    def compose(self, user_request: str, task_results: list[TaskResult]) -> str:
        del user_request, task_results
        self.calls += 1
        return self.answer


class FakeTool:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.inputs: list[dict] = []

    def invoke(self, input_value: dict) -> str:
        self.inputs.append(input_value)
        return json.dumps(self.payload, ensure_ascii=False)


class InvalidJsonTool:
    def invoke(self, input_value: dict) -> str:
        del input_value
        return "not-json"


class GraphTests(unittest.TestCase):
    def test_topological_sort_orders_dependency_before_consumer(self) -> None:
        plan = DecomposedPlan(
            tasks=[task("t2", dependencies=[use_result("t1")]), task("t1")]
        )

        ordered = topological_sort(plan)

        self.assertEqual([item.id for item in ordered], ["t1", "t2"])

    def test_cycle_is_rejected_before_execution(self) -> None:
        plan = DecomposedPlan(
            tasks=[
                task("t1", dependencies=[use_result("t2")]),
                task("t2", dependencies=[use_result("t1")]),
            ]
        )

        with self.assertRaisesRegex(PlanValidationError, "순환 의존성"):
            topological_sort(plan)

    def test_unknown_dependency_is_rejected(self) -> None:
        plan = DecomposedPlan(
            tasks=[task("t1", dependencies=[use_result("missing")])]
        )

        with self.assertRaisesRegex(PlanValidationError, "존재하지 않는"):
            topological_sort(plan)


class WorkerTests(unittest.TestCase):
    def test_route_uses_domain_fact_not_agent_label(self) -> None:
        self.assertEqual(route(task("personal")), "nana")
        self.assertEqual(route(task("external", external=True)), "kana")

    def test_existing_tool_json_is_normalized(self) -> None:
        fake_nana = FakeTool(
            {
                "answer": "개인 일정입니다.",
                "trace": [{"event": "tool_call", "tool_name": "personal_list"}],
                "inner_tool_names": ["personal_list"],
            }
        )
        fake_kana = FakeTool({"answer": "unused"})

        result = invoke_worker(
            task("t1"),
            "조회해줘",
            tools={"nana": fake_nana, "kana": fake_kana},
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.answer, "개인 일정입니다.")
        self.assertEqual(result.inner_tool_names, ["personal_list"])
        self.assertEqual(fake_nana.inputs, [{"query": "조회해줘"}])

    def test_invalid_worker_contract_becomes_failure(self) -> None:
        result = invoke_worker(
            task("t1"),
            "조회해줘",
            tools={"nana": InvalidJsonTool(), "kana": FakeTool({"answer": "unused"})},
        )

        self.assertEqual(result.status, "fail")
        self.assertIn("JSONDecodeError", result.error or "")

class ExecutorTests(unittest.TestCase):
    def test_direct_dependency_answer_and_payload_are_injected_without_trace(self) -> None:
        consumer = task("t2", dependencies=[use_result("t1")])
        predecessor = TaskResult(
            task_id="t1",
            agent="kana",
            status="ok",
            answer="12시에 끝납니다.",
            trace=[{"secret": "raw trace must not be injected"}],
            final_slot_payload={"final_slot": "2026-07-09 12:00-13:00"},
        )

        delegated = build_delegated_query(consumer, {"t1": predecessor})

        self.assertIn("12시에 끝납니다.", delegated)
        self.assertIn("final_slot_payload", delegated)
        self.assertNotIn("raw trace must not be injected", delegated)

    def test_external_members_are_structurally_injected(self) -> None:
        external_task = task("t1", external=True)

        delegated = build_delegated_query(external_task, {})

        self.assertIn("[외부 데이터 대상 멤버]", delegated)
        self.assertIn("민준", delegated)

    def test_failed_predecessor_skips_descendant_but_not_independent_task(self) -> None:
        ordered = [
            task("t1", external=True),
            task("t2", dependencies=[use_result("t1")]),
            task("t3"),
        ]
        called: list[str] = []

        def worker(current: AgentTask, delegated_query: str) -> TaskResult:
            del delegated_query
            called.append(current.id)
            if current.id == "t1":
                return TaskResult(
                    task_id="t1",
                    agent="kana",
                    status="fail",
                    error="lookup failed",
                )
            return TaskResult(
                task_id=current.id,
                agent=route(current),
                status="ok",
                answer="ok",
            )

        results = execute_tasks(ordered, worker)

        self.assertEqual([result.status for result in results], ["fail", "skipped", "ok"])
        self.assertEqual(called, ["t1", "t3"])

    def test_run_only_if_executes_only_when_condition_matches(self) -> None:
        ordered = [task("t1"), task("t2", dependencies=[run_only_if("t1")])]

        for condition_value, expected_status, expected_calls in [
            (True, "ok", ["t1", "t2"]),
            (False, "skipped", ["t1"]),
        ]:
            with self.subTest(condition_value=condition_value):
                called: list[str] = []

                def worker(current: AgentTask, delegated_query: str) -> TaskResult:
                    called.append(current.id)
                    return TaskResult(
                        task_id=current.id,
                        agent=route(current),
                        status="ok",
                        answer="ok",
                        condition_value=(
                            condition_value
                            if "[조건 판정 작업]" in delegated_query
                            else None
                        ),
                    )

                results = execute_tasks(ordered, worker)

                self.assertEqual(results[-1].status, expected_status)
                self.assertEqual(called, expected_calls)

    def test_missing_condition_value_fails_producer_and_skips_consumer(self) -> None:
        ordered = [task("t1"), task("t2", dependencies=[run_only_if("t1")])]

        def worker(current: AgentTask, delegated_query: str) -> TaskResult:
            del delegated_query
            return TaskResult(
                task_id=current.id,
                agent=route(current),
                status="ok",
                answer="ok",
            )

        results = execute_tasks(ordered, worker)

        self.assertEqual([result.status for result in results], ["fail", "skipped"])


class PlannerTests(unittest.TestCase):
    def test_structured_output_is_retried_once(self) -> None:
        class FlakyStructuredModel:
            def __init__(self) -> None:
                self.calls = 0

            def invoke(self, messages: list[dict]) -> dict:
                del messages
                self.calls += 1
                if self.calls == 1:
                    raise ValueError("invalid structured output")
                return {
                    "tasks": [
                        {
                            "id": "t1",
                            "requires_external_data": False,
                            "external_members": [],
                            "query": "내 일정 조회",
                            "dependencies": [],
                        }
                    ]
                }

        model = FlakyStructuredModel()
        plan = LLMPlanner(structured_model=model).plan("내 일정 알려줘")

        self.assertEqual(model.calls, 2)
        self.assertEqual(plan.tasks[0].id, "t1")


class EntryTests(unittest.TestCase):
    def test_single_task_returns_worker_answer_without_composer(self) -> None:
        plan = DecomposedPlan(tasks=[task("t1")])
        composer = FakeComposer()

        def worker(current: AgentTask, delegated_query: str) -> TaskResult:
            del delegated_query
            return TaskResult(
                task_id=current.id,
                agent="nana",
                status="ok",
                answer="단일 답변",
            )

        orchestrator = Orchestrator(FakePlanner(plan), worker, composer)
        result = orchestrator.run("내 일정")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], "단일 답변")
        self.assertEqual(composer.calls, 0)

    def test_compound_tasks_use_composer_and_adapter_contract(self) -> None:
        plan = DecomposedPlan(
            tasks=[
                task("t1", external=True),
                task("t2", dependencies=[use_result("t1")]),
            ]
        )
        composer = FakeComposer("최종 종합")

        def worker(current: AgentTask, delegated_query: str) -> TaskResult:
            del delegated_query
            return TaskResult(
                task_id=current.id,
                agent=route(current),
                status="ok",
                answer=f"{current.id} 결과",
            )

        adapter = OrchestratorAgentAdapter(Orchestrator(FakePlanner(plan), worker, composer))
        result = adapter.invoke({"messages": [{"role": "user", "content": "복합 요청"}]})

        self.assertEqual(result["messages"][-1].content, "최종 종합")
        self.assertEqual(result["orchestrator_result"]["execution_order"], ["t1", "t2"])
        self.assertEqual(composer.calls, 1)

        streamed_chunk = next(adapter.stream({"query": "복합 요청"}, stream_mode="updates"))
        streamed_messages = streamed_chunk["orchestrator"]["messages"]
        streamed_trace = extract_langchain_trace({"messages": streamed_messages})
        self.assertEqual(streamed_trace["execution_order"], ["t1", "t2"])

    def test_adapter_keeps_previous_messages_as_planning_context(self) -> None:
        plan = DecomposedPlan(tasks=[task("t1")])
        planner = FakePlanner(plan)

        def worker(current: AgentTask, delegated_query: str) -> TaskResult:
            del delegated_query
            return TaskResult(
                task_id=current.id,
                agent="nana",
                status="ok",
                answer="저장했습니다.",
            )

        adapter = OrchestratorAgentAdapter(
            Orchestrator(planner, worker, FakeComposer())
        )
        adapter.invoke(
            {
                "messages": [
                    {"role": "user", "content": "내일 3시에 운동할 거야."},
                    {"role": "assistant", "content": "확인했습니다."},
                    {"role": "user", "content": "그걸 저장해줘."},
                ]
            }
        )

        self.assertIn("내일 3시에 운동", planner.inputs[0])
        self.assertIn("[현재 사용자 요청]\n그걸 저장해줘.", planner.inputs[0])


if __name__ == "__main__":
    unittest.main()
