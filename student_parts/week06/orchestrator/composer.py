from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from fixed.langchain_trace import message_content_to_text
from fixed.llm import chat_model
from student_parts.week06.orchestrator.schemas import TaskResult


COMPOSER_SYSTEM_PROMPT = """
사용자의 원래 요청과 작업 실행 결과를 근거로 최종 답변을 작성한다.
- 결과에 없는 사실을 만들지 않는다.
- 성공 결과는 자연스럽게 합쳐 전달한다.
- fail 또는 skipped 작업이 있으면 무엇을 완료하지 못했는지 숨기지 않는다.
- 예외 클래스, DB 오류 등 내부 기술 오류 문자열은 그대로 노출하지 않는다.
- 내부 task id, agent 이름, trace, JSON 구조는 사용자에게 노출하지 않는다.
- 한국어로 간결하게 답한다.
""".strip()


class ComposerError(RuntimeError):
    """복합 결과의 최종 멘트 생성에 실패했을 때 발생한다."""


class LLMComposer:
    def __init__(self, model: Any | None = None) -> None:
        self._model = model or chat_model(temperature=0)

    def compose(self, user_request: str, task_results: Sequence[TaskResult]) -> str:
        summarized_results = [
            {
                "task_id": result.task_id,
                "agent": result.agent,
                "status": result.status,
                "answer": result.answer,
                "final_slot_payload": result.final_slot_payload,
                "final_decision_payload": result.final_decision_payload,
                "error": result.error,
            }
            for result in task_results
        ]

        try:
            response = self._model.invoke(
                [
                    {"role": "system", "content": COMPOSER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "original_request": user_request,
                                "task_results": summarized_results,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ]
            )
            answer = message_content_to_text(response)
            if not answer:
                raise ValueError("멘트 생성 결과가 비어 있습니다.")
            return answer
        except Exception as exc:
            raise ComposerError(f"{type(exc).__name__}: {exc}") from exc


def deterministic_fallback(task_results: Sequence[TaskResult]) -> str:
    """Composer 장애 시 이미 얻은 결과를 버리지 않는 최소 fallback."""

    lines: list[str] = []
    for result in task_results:
        if result.status == "ok" and result.answer:
            lines.append(result.answer)
        elif result.status == "fail":
            lines.append("일부 작업을 처리하지 못했습니다.")
        else:
            lines.append("선행 작업 문제로 일부 작업을 보류했습니다.")
    return "\n".join(lines) or "요청을 처리하지 못했습니다."
