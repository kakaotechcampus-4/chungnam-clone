from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from langchain_core.messages import AIMessage

from fixed.langchain_trace import message_content_to_text
from student_parts.week06.orchestrator.composer import (
    ComposerError,
    LLMComposer,
    deterministic_fallback,
)
from student_parts.week06.orchestrator.executor import (
    Worker,
    accumulate_trace,
    execute_tasks,
)
from student_parts.week06.orchestrator.graph import PlanValidationError, topological_sort
from student_parts.week06.orchestrator.planner import LLMPlanner, PlanningError
from student_parts.week06.orchestrator.schemas import DecomposedPlan, TaskResult
from student_parts.week06.orchestrator.workers import invoke_worker


class Planner(Protocol):
    def plan(self, user_request: str) -> DecomposedPlan: ...


class Composer(Protocol):
    def compose(self, user_request: str, task_results: list[TaskResult]) -> str: ...


def _overall_status(task_results: list[TaskResult]) -> str:
    ok_count = sum(result.status == "ok" for result in task_results)
    if ok_count == len(task_results):
        return "ok"
    if ok_count:
        return "partial"
    return "fail"


def _single_task_answer(result: TaskResult) -> str:
    if result.status == "ok" and result.answer:
        return result.answer
    if result.status == "skipped":
        return "선행 작업 문제로 요청을 처리하지 못했습니다."
    return "요청 처리 중 하위 작업이 실패했습니다."


class Orchestrator:
    """Planner 결과를 검증한 뒤 Nana/Kana를 코드로 조율하는 실행 진입점."""

    def __init__(
        self,
        planner: Planner | None = None,
        worker: Worker | None = None,
        composer: Composer | None = None,
    ) -> None:
        self._planner = planner or LLMPlanner()
        self._worker = worker or invoke_worker
        self._composer = composer or LLMComposer()

    def run(
        self,
        user_request: str,
        *,
        planning_input: str | None = None,
    ) -> dict[str, Any]:
        try:
            plan = self._planner.plan(planning_input or user_request)
        except PlanningError as exc:
            return self._fatal_result(
                stage="planner",
                code="plan_generation_failed",
                message=str(exc),
                answer="요청을 작업 계획으로 분해하지 못했습니다.",
            )
        except Exception as exc:
            return self._fatal_result(
                stage="planner",
                code="unexpected_planner_error",
                message=f"{type(exc).__name__}: {exc}",
                answer="요청을 작업 계획으로 분해하지 못했습니다.",
            )

        try:
            ordered_tasks = topological_sort(plan)
        except PlanValidationError as exc:
            return self._fatal_result(
                stage="validation",
                code=exc.code,
                message=str(exc),
                answer="생성된 작업 계획의 의존관계가 올바르지 않습니다.",
                plan=plan,
            )

        task_results = execute_tasks(ordered_tasks, self._worker)
        trace = accumulate_trace(task_results)
        status = _overall_status(task_results)
        composer_error: str | None = None
        composer_error_code: str | None = None

        if len(task_results) == 1:
            answer = _single_task_answer(task_results[0])
        else:
            try:
                answer = self._composer.compose(user_request, task_results)
            except ComposerError as exc:
                composer_error = str(exc)
                composer_error_code = "composition_failed"
                answer = deterministic_fallback(task_results)
                if status == "ok":
                    status = "partial"
                trace.append(
                    {
                        "event": "orchestrator_error",
                        "stage": "composer",
                        "code": "composition_failed",
                        "message": composer_error,
                    }
                )
            except Exception as exc:
                composer_error = f"{type(exc).__name__}: {exc}"
                composer_error_code = "unexpected_composer_error"
                answer = deterministic_fallback(task_results)
                if status == "ok":
                    status = "partial"
                trace.append(
                    {
                        "event": "orchestrator_error",
                        "stage": "composer",
                        "code": "unexpected_composer_error",
                        "message": composer_error,
                    }
                )

        return {
            "status": status,
            "answer": answer,
            "plan": plan.model_dump(mode="json"),
            "task_results": [result.model_dump(mode="json") for result in task_results],
            "execution_order": [task.id for task in ordered_tasks],
            "trace": trace,
            "error": (
                {
                    "stage": "composer",
                    "code": composer_error_code,
                    "message": composer_error,
                }
                if composer_error
                else None
            ),
        }

    @staticmethod
    def _fatal_result(
        *,
        stage: str,
        code: str,
        message: str,
        answer: str,
        plan: DecomposedPlan | None = None,
    ) -> dict[str, Any]:
        error = {"stage": stage, "code": code, "message": message}
        return {
            "status": "fail",
            "answer": answer,
            "plan": plan.model_dump(mode="json") if plan is not None else None,
            "task_results": [],
            "execution_order": [],
            "trace": [{"event": "orchestrator_error", **error}],
            "error": error,
        }


def _latest_user_request(input_value: dict[str, Any]) -> str:
    direct_query = input_value.get("query")
    if isinstance(direct_query, str) and direct_query.strip():
        return direct_query.strip()

    messages = input_value.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
        else:
            role = getattr(message, "type", None)
        if role in {"user", "human"}:
            text = message_content_to_text(message)
            if text:
                return text
    raise ValueError("실행할 최신 user 메시지가 없습니다.")


def _planning_input(input_value: dict[str, Any], current_request: str) -> str:
    """이전 대화는 해석 문맥으로만 표시하고 현재 요청과 분리한다."""

    if isinstance(input_value.get("query"), str):
        return current_request

    messages = input_value.get("messages") or []
    current_index: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
        else:
            role = getattr(message, "type", None)
        if role in {"user", "human"}:
            current_index = index
            break

    if current_index is None or current_index == 0:
        return current_request

    context_lines: list[str] = []
    for message in messages[:current_index]:
        if isinstance(message, dict):
            role = str(message.get("role") or message.get("type") or "unknown")
        else:
            role = str(getattr(message, "type", "unknown"))
        text = message_content_to_text(message)
        if text:
            context_lines.append(f"{role}: {text}")

    if not context_lines:
        return current_request

    return (
        "[이전 대화 - 현재 요청 해석에만 사용하고 이전 작업을 다시 실행하지 말 것]\n"
        + "\n".join(context_lines)
        + "\n\n[현재 사용자 요청]\n"
        + current_request
    )


class OrchestratorAgentAdapter:
    """Week registry와 같은 ``invoke/stream`` 모양을 제공하는 얇은 어댑터."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator

    def invoke(
        self,
        input_value: dict[str, Any],
        config: Any | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del config
        request = _latest_user_request(input_value)
        orchestration = self._orchestrator.run(
            request,
            planning_input=_planning_input(input_value, request),
        )
        answer_message = AIMessage(
            content=orchestration["answer"],
            additional_kwargs={"orchestrator_result": orchestration},
        )
        return {
            "messages": [answer_message],
            "orchestrator_result": orchestration,
        }

    def stream(
        self,
        input_value: dict[str, Any],
        config: Any | None = None,
        *,
        stream_mode: str | None = None,
        **_: Any,
    ) -> Iterator[dict[str, Any]]:
        del config, stream_mode
        result = self.invoke(input_value)
        yield {
            "orchestrator": {
                "messages": result["messages"],
                "orchestrator_result": result["orchestrator_result"],
            }
        }


_ORCHESTRATOR_AGENT: OrchestratorAgentAdapter | None = None


def build_orchestrator_agent() -> OrchestratorAgentAdapter:
    global _ORCHESTRATOR_AGENT
    if _ORCHESTRATOR_AGENT is None:
        _ORCHESTRATOR_AGENT = OrchestratorAgentAdapter(Orchestrator())
    return _ORCHESTRATOR_AGENT


def build_week_agent() -> OrchestratorAgentAdapter:
    """``fixed.week_agent_registry``가 찾는 표준 builder 이름."""

    return build_orchestrator_agent()


def extract_langchain_trace(result: dict[str, Any]) -> dict[str, Any]:
    """어댑터 결과를 UI가 소비할 수 있는 trace payload로 변환한다."""

    orchestration = result.get("orchestrator_result") or {}
    if not orchestration:
        for value in result.values():
            if isinstance(value, dict) and value.get("orchestrator_result"):
                orchestration = value["orchestrator_result"]
                break
    if not orchestration:
        for message in reversed(result.get("messages", [])):
            if isinstance(message, dict):
                additional_kwargs = message.get("additional_kwargs") or {}
            else:
                additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
            if additional_kwargs.get("orchestrator_result"):
                orchestration = additional_kwargs["orchestrator_result"]
                break

    return {
        "events": orchestration.get("trace", []),
        "orchestrator_status": orchestration.get("status"),
        "plan": orchestration.get("plan"),
        "task_results": orchestration.get("task_results", []),
        "execution_order": orchestration.get("execution_order", []),
        "error": orchestration.get("error"),
    }
