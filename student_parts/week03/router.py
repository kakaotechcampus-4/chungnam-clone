from __future__ import annotations
import json
from typing import Any, Literal
from pydantic import BaseModel, Field
from fixed.llm import chat_model
from student_parts.week03.confirmation import (
    confirm_pending_schedule_action,
    peek_pending_action,
    pop_pending_action,
)
from student_parts.week03.prompts import (
    CONFIRMATION_RESPONSE_PROMPT,
    PENDING_ROUTER_PROMPT,
)
from langchain_core.messages import AIMessage

class PendingRoute(BaseModel):
    decision: Literal[
        "execute",
        "cancel",
        "other",
    ] = Field(
        description=(
    "현재 pending 작업의 실행을 허용하거나 명령하면 execute, "
    "실행을 거절하거나 취소하면 cancel, "
    "실행 여부와 관계없는 발화는 other"
)
    )

class PendingRoutedAgent:
    def __init__(self, main_agent: Any):
        self.main_agent = main_agent

    def route(
        self,
        inputs: dict[str, Any],
    ) -> PendingRoute | None:
        pending = peek_pending_action()
        if pending is None:
            return None
        return route_pending_response(
            pending=pending,
            previous_assistant_message=latest_assistant_message(inputs),
            user_message=latest_user_message(inputs)
        )
    def invoke(
        self,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        route = self.route(inputs)
        if route is None:
            return self.main_agent.invoke(inputs)
        if route.decision == "execute":
            return run_confirmation(confirm=True)
        if route.decision == "cancel":
            return run_confirmation(confirm=False)
        pop_pending_action()
        return self.main_agent.invoke(inputs)
    def stream(
        self,
        inputs: dict[str, Any],
        stream_mode: str = "updates",
    ):
        route = self.route(inputs)
        if route is None:
            yield from self.main_agent.stream(
                inputs,
                stream_mode=stream_mode,
            )
            return
        if route.decision == "execute":
            yield {
                "router": run_confirmation(confirm=True),
            }
            return
        if route.decision == "cancel":
            yield {
                "router": run_confirmation(confirm=False),
            }
            return
        pop_pending_action()
        yield from self.main_agent.stream(
            inputs,
            stream_mode=stream_mode,
        )
        
def latest_user_message(inputs: dict[str, Any]) -> str:
    for message in reversed(inputs.get("messages", [])):
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
        ):return str(message.get("content") or "")

    return ""

def latest_assistant_message(
    inputs: dict[str, Any],
) -> str:
    for message in reversed(inputs.get("messages", [])):
        if (
            isinstance(message, dict)
            and message.get("role") == "assistant"
        ):
            return str(message.get("content") or "")

    return ""

def run_confirmation(
    confirm: bool,
) -> dict[str, Any]:
    pending = peek_pending_action()

    result = confirm_pending_schedule_action.invoke(
        {"confirm": confirm}
    )

    response = chat_model(temperature=0).invoke(
        [
            {
                "role": "system",
                "content": CONFIRMATION_RESPONSE_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "confirmed": confirm,
                        "pending_action": pending,
                        "tool_result": json.loads(result),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]
    )

    return {
        "messages": [response],
    }


def route_pending_response(
    pending: dict[str, Any],
    previous_assistant_message: str,
    user_message: str,
) -> PendingRoute:
    """사용자 말을 승인·거절·그 외로 분류."""

    router_model = chat_model(temperature=0).with_structured_output(
        PendingRoute,
        method="function_calling",
    )

    router_input = {
        "pending_action": pending,
        "previous_assistant_message": previous_assistant_message,
        "latest_user_message": user_message
    }

    result = router_model.invoke(
        [
            {
                "role": "system",
                "content": PENDING_ROUTER_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    router_input,
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]
    )

    validated = PendingRoute.model_validate(result)
    print("\n========== ROUTER ==========")
    print(json.dumps(router_input, ensure_ascii=False, indent=2))
    print("decision:", validated.decision)
    print("============================\n")
    return validated
