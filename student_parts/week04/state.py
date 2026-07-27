from __future__ import annotations

from typing import NotRequired

from langchain.agents import AgentState


class Week04AgentState(AgentState):
    allowed_conversation_ids: NotRequired[set[str]]