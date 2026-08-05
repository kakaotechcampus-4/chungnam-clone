from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool

from fixed.langchain_trace import extract_agent_events, extract_final_text
from fixed.llm import chat_model
from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts, week04_tools
from student_parts.week06.schemas import AgentQueryInput
from student_parts.week06.trace import _tool_call_names


_NANA_SUBAGENT: Any | None = None


def nana_prompt_parts() -> list[str]:
    """Week 6 Nana 하위 에이전트 전용 system prompt 조각입니다."""

    return [
        *week04_prompt_parts(),
        # TODO 메인: Week 6 Nana 하위 에이전트 전용 system prompt를 자유롭게 추가하세요.
        #   - supervisor prompt를 공유하지 않는 Nana 전용 prompt입니다.
        #   - 개인 일정/저장/RAG를 담당하고, 그룹 조율 요청은 담당이 아니라고 짧게 알리게 합니다.
    """
    너는 Supervisor에게 개인 영역의 업무를 위임받아 처리하는 Nana다.

    담당 업무:
    - 사용자의 개인 일정 생성, 조회, 수정, 삭제
    - 할 일과 알림의 저장 및 조회
    - 개인 참고자료와 이전 앱 대화에 대한 RAG 검색

    처리 규칙:
    - 필요한 사실과 작업 결과는 제공된 도구를 호출하여 확인한다.
    - 도구를 호출하지 않고 일정이 저장·수정·삭제됐다고 추측하지 않는다.
    - 도구 결과 JSON을 근거로 사용자에게 간결하게 답한다.
    - 외부 멤버의 일정 조회, 공유 일정 조회, 공통 가능 시간 탐색과 그룹 일정 조율은 Kana의 담당이다.
    - Kana 담당 요청을 받으면 직접 처리하거나 추측하지 말고, 그룹 일정 조율은 Kana 담당이라고 알린다.
    """
    ]


def nana_system_prompt() -> str:
    return join_system_prompt(nana_prompt_parts())


@tool(args_schema=AgentQueryInput)
def nana_agent(query: str) -> str:
    """개인 일정과 개인 RAG 작업을 프롬프트 기반 Nana 하위 에이전트에게 위임합니다."""

    # TODO 메인: Week 4 도구를 가진 Nana 하위 agent를 실행하고 answer/trace/inner_tool_names를 반환하세요.
    #   - _NANA_SUBAGENT가 None일 때만 create_agent(model=chat_model(), tools=week04_tools(),
    #     system_prompt=nana_system_prompt())로 만들고 이후에는 재사용합니다.
    #   - query를 user 메시지로 invoke하고, extract_agent_events(...)와 extract_final_text(...)로
    #     trace와 answer를 뽑습니다.
    #   - selected_agent, answer, trace, inner_tool_names를 담은 JSON 문자열을 반환합니다.
    global _NANA_SUBAGENT

    if _NANA_SUBAGENT is None:
        _NANA_SUBAGENT = create_agent(
            model=chat_model(),
            tools=week04_tools(),
            system_prompt=nana_system_prompt(),
        )

    result = _NANA_SUBAGENT.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": query,
                }
            ]
        }
    )

    events = extract_agent_events(result)

    return json.dumps(
        {
            "selected_agent": "nana_agent",
            "answer": extract_final_text(result),
            "trace": events,
            "inner_tool_names": _tool_call_names(events),
        },
        ensure_ascii=False,
    )
