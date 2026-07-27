from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware


class ConversationContextToolMiddleware(AgentMiddleware):
    def wrap_model_call(
        self,
        request,
        handler,
    ):
        state = request.state or {}
        allowed_ids = state.get(
            "allowed_conversation_ids",
            set(),
        )
        tools = request.tools or []

        if not allowed_ids:
            tools = [
                current_tool
                for current_tool in tools
                if getattr(current_tool, "name", "")
                != "load_conversation_context"
            ]

        return handler(
            request.override(tools=tools)
        )