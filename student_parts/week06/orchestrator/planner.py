from __future__ import annotations

from typing import Any

from fixed.llm import chat_model
from student_parts.week06.orchestrator.schemas import DecomposedPlan


PLANNER_SYSTEM_PROMPT = """
사용자의 일정 관련 요청을 실행 가능한 작업 목록으로 분해한다.
에이전트 이름이나 내부 구현 이름을 출력하지 말고 도메인 사실만 구조화한다.

[작업 분할]
- 한 작업에는 한 실행 주체가 처리할 수 있는 원자적 책임만 넣는다.
- 외부 멤버의 결과를 이용해 개인 일정을 조회·생성·수정·삭제해야 한다면,
  외부 데이터 작업과 개인 작업을 별도 작업으로 나누고 depends_on으로 연결한다.
- 각 query는 담당 실행 주체가 원래 사용자 요청을 보지 않아도 실행할 수 있게
  이름, 날짜, 시간, 작업 의도를 빠뜨리지 않는다.

[외부 데이터 판단]
- 외부 멤버의 과거 대화, 일정, 공유 일정, 그룹 일정 데이터가 필요하면
  requires_external_data=true다.
- 사람 이름이 단순히 개인 일정의 제목이나 맥락에 등장하는 것만으로 true가 아니다.
- external_members에는 실제로 외부 데이터가 필요한 사람만 넣는다.
- 개인 일정·할 일·알림·개인 참고자료·이전 앱 대화만 필요하면 false다.

[의존관계]
- id는 t1, t2처럼 계획 안에서 고유하게 만든다.
- depends_on에는 현재 작업이 결과를 직접 필요로 하는 선행 작업 id만 넣는다.
- 존재하지 않는 id, 자기 자신, 순환 의존성을 넣지 않는다.
- 실행 순서를 강제할 이유가 없는 독립 작업에는 depends_on을 넣지 않는다.
""".strip()


class PlanningError(RuntimeError):
    """두 번의 structured-output 생성이 모두 실패했을 때 발생한다."""


class LLMPlanner:
    """자연어 요청을 ``DecomposedPlan``으로 한 번에 고정하는 LLM 분해기."""

    def __init__(self, structured_model: Any | None = None) -> None:
        self._structured_model = structured_model or chat_model(
            temperature=0,
        ).with_structured_output(
            DecomposedPlan,
            method="function_calling",
        )

    def plan(self, user_request: str) -> DecomposedPlan:
        last_error: Exception | None = None

        for attempt in range(2):
            retry_instruction = ""
            if attempt == 1:
                retry_instruction = (
                    "\n\n직전 출력은 스키마로 해석되지 않았다. "
                    "설명 없이 지정된 구조만 다시 반환한다."
                )

            try:
                raw = self._structured_model.invoke(
                    [
                        {"role": "system", "content": PLANNER_SYSTEM_PROMPT + retry_instruction},
                        {"role": "user", "content": user_request},
                    ]
                )
                return DecomposedPlan.model_validate(raw)
            except Exception as exc:
                last_error = exc

        assert last_error is not None
        raise PlanningError(
            f"작업 계획 structured output 생성 실패: {type(last_error).__name__}: {last_error}"
        ) from last_error
