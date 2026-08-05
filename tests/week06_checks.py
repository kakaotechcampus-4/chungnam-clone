"""Week 6 supervisor/하위 agent 헤드리스 점검 스크립트입니다.

레포에 pytest 의존성이 없고 자동 테스트 하네스도 없으므로, tests/week05_checks.py와 같은
실행 가능한 assert 스크립트로 관리합니다. 하위 agent는 stub으로 갈아끼워 LLM 키 없이도
위임 wrapper tool의 반환 계약과 trace 승격 로직을 확인합니다.

실행:
    .venv/bin/python -m tests.week06_checks
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from student_parts import week06_kanamate_decides_schedule as week06

CHECKS: list[str] = []


def check(name: str) -> None:
    CHECKS.append(name)
    print(f"  ok  {name}")


class StubSubAgent:
    """create_agent(...)가 만든 하위 agent 대신 정해진 실행 결과를 돌려줍니다."""

    def __init__(self, messages: list[Any]):
        self.messages = messages
        self.queries: list[str] = []

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.queries.append(payload["messages"][-1]["content"])
        return {"messages": self.messages}


def tool_result_messages(tool_name: str, content: Any, answer: str) -> list[Any]:
    """tool 한 번 호출 + 최종 답변으로 끝나는 하위 agent 실행 결과를 흉내 냅니다."""

    return [
        HumanMessage(content="supervisor가 넘긴 query"),
        AIMessage(content="", tool_calls=[{"name": tool_name, "args": {}, "id": "call-1"}]),
        ToolMessage(content=json.dumps(content, ensure_ascii=False), tool_call_id="call-1", name=tool_name),
        AIMessage(content=answer),
    ]


def payload_of(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    raw = tool.invoke(args)
    assert isinstance(raw, str), f"{tool.name}은 JSON 문자열을 반환해야 합니다: {type(raw)}"
    return json.loads(raw)


# ------------------------------------------------------------- tool 노출 범위


def test_supervisor_sees_only_two_delegation_tools() -> None:
    assert week06.agent_tool_names("supervisor") == ["nana_agent", "kana_agent"]
    check("supervisor_tools: nana_agent/kana_agent 두 개만 노출")


def test_subagent_tool_split() -> None:
    nana = week06.agent_tool_names("nana_agent")
    kana = week06.agent_tool_names("kana_agent")

    assert "personal_create_schedule" in nana and "search_personal_references" in nana
    assert "collect_member_schedules" not in nana, "그룹 조율 tool이 Nana에 섞이면 위임이 무의미해집니다."
    check("nana_agent: Week 4 개인 일정/RAG tool만 보유")

    assert {"search_previous_conversations", "collect_member_schedules", "list_shared_schedules"} <= set(kana)
    assert "personal_create_schedule" not in kana, "개인 일정 저장 tool은 Nana 담당입니다."
    check("kana_agent: 외부 대화/멤버 일정 tool만 보유")

    # 추가 과제 미구현 상태에서는 두 tool을 노출하지 않습니다(구현하면 이 점검을 반대로 바꿉니다).
    assert "find_common_available_slots" not in kana
    assert "decide_final_slot" not in kana
    check("kana_tools: 미구현 추가 과제 tool은 노출하지 않음")


def test_prompts_are_separate_and_non_empty() -> None:
    supervisor = week06.supervisor_system_prompt()
    nana = week06.nana_system_prompt()
    kana = week06.kana_system_prompt()

    assert "nana_agent" in supervisor and "kana_agent" in supervisor
    check("supervisor prompt: 두 하위 agent 위임 규칙 포함")

    # 하위 agent는 supervisor prompt를 공유하지 않으므로 위임 규칙이 새어 들어가면 안 됩니다.
    assert "nana_agent" not in kana and "kana_agent" not in kana
    assert "오늘 날짜는" in kana, "Kana는 다른 주차 prompt를 누적하지 않으므로 날짜 기준을 스스로 가져야 합니다."
    check("kana prompt: 누적 없이 역할·날짜 기준을 자체 보유")

    assert "Kana 담당" in nana, "Nana는 담당이 아닌 요청을 짧게 넘길 수 있어야 합니다."
    assert "Nana 담당" in kana, "Kana는 개인 일정 저장을 Nana에게 넘길 수 있어야 합니다."
    check("하위 prompt: 담당이 아닌 요청을 서로에게 안내")

    assert "find_common_available_slots" not in kana and "decide_final_slot" not in kana
    check("kana prompt: 미구현 추가 과제 tool을 언급하지 않음")


# ------------------------------------------------------------- 위임 wrapper tool


def test_nana_agent_returns_answer_trace_and_inner_tool_names() -> None:
    stub = StubSubAgent(
        tool_result_messages(
            "personal_list_saved_schedules",
            {"ok": True, "tool_name": "personal_list_saved_schedules", "schedules": []},
            "저장된 일정은 없습니다.",
        )
    )
    week06._NANA_SUBAGENT = stub
    try:
        payload = payload_of(week06.nana_agent, {"query": "내 일정 보여줘"})
    finally:
        week06._NANA_SUBAGENT = None

    assert stub.queries == ["내 일정 보여줘"], "supervisor가 넘긴 query가 그대로 하위 agent로 가야 합니다."
    assert payload["ok"] is True
    assert payload["selected_agent"] == "nana_agent"
    assert payload["answer"] == "저장된 일정은 없습니다."
    assert payload["inner_tool_names"] == ["personal_list_saved_schedules"]
    assert payload["trace"][0]["event"] == "tool_call"
    check("nana_agent: answer/trace/inner_tool_names JSON 계약")


def test_kana_agent_lifts_final_payloads_from_inner_trace() -> None:
    stub = StubSubAgent(
        tool_result_messages(
            "collect_member_schedules",
            {"ok": True, "tool_name": "collect_member_schedules", "rows": []},
            "철수는 화요일 오후가 비어 있습니다.",
        )
    )
    week06._KANA_SUBAGENT = stub
    try:
        payload = payload_of(week06.kana_agent, {"query": "철수 다음 주 일정 알려줘"})
    finally:
        week06._KANA_SUBAGENT = None

    assert payload["selected_agent"] == "kana_agent"
    assert payload["inner_tool_names"] == ["collect_member_schedules"]
    # 추가 과제 tool이 없으면 최종 시간 payload도 없어야 합니다(없는 값을 지어내지 않음).
    assert payload["final_slot_payload"] is None
    assert payload["final_decision_payload"] is None
    check("kana_agent: 최종 시간 결정이 없으면 final payload는 null")


def test_final_payloads_from_events_picks_latest() -> None:
    events = [
        {"event": "tool_result", "tool_name": "collect_member_schedules", "content": {"rows": []}},
        {"event": "tool_result", "tool_name": "decide_final_slot", "content": {"final_slot": None, "reason": "보류"}},
        {
            "event": "tool_result",
            "tool_name": "decide_final_slot",
            "content": {"final_slot": "2026-08-11 14:00-15:00", "reason": "둘 다 비어 있음", "candidates": []},
        },
        {"event": "tool_result", "tool_name": "propose_group_schedule", "content": {"final_decision": {"status": "confirmed"}}},
    ]
    final_slot, final_decision = week06._final_payloads_from_events(events)
    assert final_slot["final_slot"] == "2026-08-11 14:00-15:00", "나중 호출 결과로 갱신돼야 합니다."
    assert final_decision == {"status": "confirmed"}
    check("_final_payloads_from_events: final_slot/final_decision을 최신 값으로 승격")


# ------------------------------------------------------------- supervisor trace


def supervisor_result(agent_tool: str, payload: dict[str, Any], answer: str) -> dict[str, Any]:
    return {
        "messages": [
            HumanMessage(content="다음 주 철수랑 회의 잡아줘"),
            AIMessage(content="", tool_calls=[{"name": agent_tool, "args": {"query": "..."}, "id": "call-sup"}]),
            ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id="call-sup", name=agent_tool),
            AIMessage(content=answer),
        ]
    }


def test_extract_langchain_trace_reports_selected_agent() -> None:
    inner = {
        "ok": True,
        "tool_name": "kana_agent",
        "answer": "정리했습니다.",
        "inner_tool_names": ["collect_member_schedules"],
        "final_slot_payload": None,
        "final_decision_payload": None,
    }
    trace = week06.extract_langchain_trace(supervisor_result("kana_agent", inner, "정리했습니다."))

    assert trace["supervisor_selected_agent"] == "kana_agent"
    assert trace["inner_tool_names"] == ["collect_member_schedules"]
    assert trace["final_slot_payload"] is None
    check("extract_langchain_trace: 선택된 하위 agent와 내부 tool 이름 노출")


def test_extract_langchain_trace_lifts_final_slot_payload() -> None:
    inner = {
        "ok": True,
        "tool_name": "kana_agent",
        "answer": "8월 11일 14시로 정했습니다.",
        "inner_tool_names": ["collect_member_schedules", "decide_final_slot"],
        "final_slot_payload": {"final_slot": "2026-08-11 14:00-15:00", "reason": "둘 다 가능", "candidates": []},
        "final_decision_payload": None,
    }
    trace = week06.extract_langchain_trace(supervisor_result("kana_agent", inner, "8월 11일 14시로 정했습니다."))

    assert trace["final_slot_payload"]["final_slot"] == "2026-08-11 14:00-15:00"
    check("extract_langchain_trace: kana_agent가 올린 final_slot_payload를 그대로 전달")


def main() -> None:
    tests = [
        test_supervisor_sees_only_two_delegation_tools,
        test_subagent_tool_split,
        test_prompts_are_separate_and_non_empty,
        test_nana_agent_returns_answer_trace_and_inner_tool_names,
        test_kana_agent_lifts_final_payloads_from_inner_trace,
        test_final_payloads_from_events_picks_latest,
        test_extract_langchain_trace_reports_selected_agent,
        test_extract_langchain_trace_lifts_final_slot_payload,
    ]
    for test in tests:
        print(f"\n[{test.__name__}]")
        test()
    print(f"\n총 {len(CHECKS)}개 점검 통과")


if __name__ == "__main__":
    main()
