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
    assert "personal_create_schedule" not in kana, "개인 일정 저장은 개인 담당 하위 agent 몫입니다."
    check("kana_agent: 외부 대화/멤버 일정 tool만 보유")

    assert {"find_common_available_slots", "decide_final_slot"} <= set(kana)
    assert "find_common_available_slots" not in nana and "decide_final_slot" not in nana
    check("kana_tools: 추가 과제 tool 2개를 Kana에게만 노출")


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

    # 리뷰 반영: 하위 agent는 서로의 존재를 몰라야 합니다. 담당이 아니라는 사실만 말하고,
    # 어디로 다시 보낼지는 supervisor가 정합니다. 이래야 agent를 늘리거나 이름을 바꿔도 하위 prompt가 안 흔들립니다.
    assert "Kana" not in nana, "Nana prompt가 다른 하위 agent 이름을 알면 agent 구성이 바뀔 때 함께 고쳐야 합니다."
    assert "Nana" not in kana, "Kana prompt도 마찬가지로 다른 하위 agent 이름을 몰라야 합니다."
    assert "제 담당이 아닙니다" in nana and "제 담당이 아닙니다" in kana
    check("하위 prompt: 서로의 이름을 모른 채 '담당 아님'만 알림")

    assert "다른 하나를 골라 한 번 더 위임한다" in supervisor, "re-routing 판단은 supervisor가 가져야 합니다."
    check("supervisor prompt: 담당 아님 응답의 재위임을 직접 판단")

    assert "find_common_available_slots" in kana and "decide_final_slot" in kana
    check("kana prompt: 추가 과제 tool 사용 지시 포함")


# ------------------------------------------------------------- 추가 과제: 공통 시간 후보/최종 결정

BUSY_ROWS = [
    {"member_name": "나", "title": "사전 미팅", "date": "2026-08-11", "start_time": "10:00", "end_time": "11:00"},
    {"member_name": "철수", "title": "QA 리뷰", "date": "2026-08-11", "start_time": "14:00", "end_time": "15:00"},
]


class StubCollectTool:
    """collect_member_schedules 대신 정해진 rows를 돌려줍니다(MCP 서버 없이 수집 경로만 점검)."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def invoke(self, args: dict[str, Any]) -> str:
        self.calls.append(args)
        return json.dumps({"ok": True, "tool_name": "collect_member_schedules", "rows": self.rows}, ensure_ascii=False)


def test_tool_descriptions_state_that_agent_picks() -> None:
    find_description = week06.FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION
    decide_description = week06.DECIDE_FINAL_SLOT_DESCRIPTION

    # description이 agent가 이 계약을 아는 유일한 통로라, 형식과 "직접 고른다"가 빠지면 인자가 빈 채로 옵니다.
    assert "대신 계산하지 않습니다" in find_description
    assert "candidate_slots" in find_description and "decide_final_slot" in find_description
    for token in ("date", "start_time", "end_time", "duration_minutes", "reason"):
        assert token in find_description, f"candidate_slots 항목 형식에 {token}가 빠졌습니다."
    check("find_common_available_slots description: 후보를 agent가 직접 채우는 계약 명시")

    assert "대신 고르지 않습니다" in decide_description
    assert "'YYYY-MM-DD HH:MM-HH:MM'" in decide_description
    assert "needs_agent_selection=true" in decide_description
    check("decide_final_slot description: 최종 시간 형식과 보류 규칙 명시")


def test_find_common_available_slots_drops_overlapping_candidates() -> None:
    payload = payload_of(
        week06.find_common_available_slots,
        {
            "member_names": ["철수"],
            "date_from": "2026-08-11T00:00:00",
            "date_to": "2026-08-11",
            "busy_rows": BUSY_ROWS,
            "candidate_slots": [
                # 철수의 QA 리뷰와 겹치는 후보 → 제외돼야 합니다.
                {"date": "2026-08-11", "start_time": "14:00", "end_time": "15:00", "duration_minutes": 60, "reason": "겹침"},
                {"date": "2026-08-11", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "둘 다 비어 있음"},
                # 업무 시간(09:00~18:00) 밖 → 제외돼야 합니다.
                {"date": "2026-08-11", "start_time": "19:00", "end_time": "20:00", "duration_minutes": 60, "reason": "야간"},
            ],
        },
    )

    assert payload["ok"] is True and payload["tool_name"] == "find_common_available_slots"
    assert [slot["start_time"] for slot in payload["candidate_slots"]] == ["16:00"]
    assert payload["busy_rows"] == BUSY_ROWS, "판단 근거로 넘긴 busy_rows가 결과에 그대로 남아야 합니다."
    # 내 일정도 후보를 막는 근거이므로 멤버 목록 맨 앞에 "나"가 함께 남아야 합니다.
    assert payload["members"] == ["나", "철수"]
    # date_from에 ISO datetime이 와도 날짜 부분만 쓰므로 같은 날 후보가 살아남습니다.
    check("find_common_available_slots: 겹치거나 업무시간 밖인 후보를 제외")


def test_find_common_available_slots_collects_busy_rows_when_missing() -> None:
    stub = StubCollectTool(BUSY_ROWS)
    original = week06.collect_member_schedules
    week06.collect_member_schedules = stub
    try:
        payload = payload_of(
            week06.find_common_available_slots,
            {
                "member_names": ["나", "철수"],
                "date_from": "2026-08-11",
                "date_to": "2026-08-11",
                "candidate_slots": [
                    {"date": "2026-08-11", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "빈 시간"}
                ],
            },
        )
    finally:
        week06.collect_member_schedules = original

    assert len(stub.calls) == 1, "busy_rows가 없으면 한 번만 수집해야 합니다."
    # collect_member_schedules가 내 일정을 "나" row로 따로 넣어 주므로 넘기는 목록에서는 "나"를 빼야 중복되지 않습니다.
    assert stub.calls[0]["member_names"] == ["철수"]
    assert payload["busy_rows"] == BUSY_ROWS
    assert len(payload["candidate_slots"]) == 1
    check("find_common_available_slots: busy_rows가 없으면 직접 수집(‘나’ 중복 제외)")


def test_decide_final_slot_records_agent_choice() -> None:
    candidates = [
        {"date": "2026-08-11", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "둘 다 비어 있음"},
    ]
    payload = payload_of(
        week06.decide_final_slot,
        {
            "candidate_slots": candidates,
            "selected_index": 0,
            "final_slot": "2026-08-11 16:00-17:00",
            "needs_agent_selection": False,
            "member_names": ["나", "철수"],
            "date_from": "2026-08-11",
            "date_to": "2026-08-11",
            "reason": "두 사람 모두 비어 있는 유일한 시간대입니다.",
            "busy_rows": BUSY_ROWS,
        },
    )

    # course repo 계약: final_slot/reason/candidates는 top-level에 있어야 합니다.
    assert payload["final_slot"] == "2026-08-11 16:00-17:00"
    assert payload["candidates"] == ["2026-08-11 16:00-17:00"]
    assert payload["needs_agent_selection"] is False
    assert payload["reason"] == "두 사람 모두 비어 있는 유일한 시간대입니다."
    assert payload["selected_slot"] == candidates[0]
    check("decide_final_slot: agent가 고른 최종 시간을 top-level 계약으로 기록")


def test_decide_final_slot_keeps_pending_without_selection() -> None:
    payload = payload_of(
        week06.decide_final_slot,
        {
            "candidate_slots": [
                {"date": "2026-08-11", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "빈 시간"}
            ],
            "reason": "사용자 확인이 필요합니다.",
        },
    )

    # tool이 최종 시간을 대신 고르면 "agent가 직접 고른다"는 계약이 깨집니다.
    assert payload["final_slot"] is None
    assert payload["needs_agent_selection"] is True
    check("decide_final_slot: 선택이 없으면 임의로 확정하지 않고 보류")


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


def test_kana_agent_lifts_real_decide_final_slot_output() -> None:
    """추가 과제 tool의 실제 출력이 supervisor 레벨까지 그대로 올라오는지 확인합니다."""

    decided = json.loads(
        week06.decide_final_slot.invoke(
            {
                "candidate_slots": [
                    {"date": "2026-08-11", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "빈 시간"}
                ],
                "selected_index": 0,
                "final_slot": "2026-08-11 16:00-17:00",
                "needs_agent_selection": False,
                "member_names": ["나", "철수"],
                "reason": "두 사람 모두 비어 있습니다.",
            }
        )
    )
    stub = StubSubAgent(tool_result_messages("decide_final_slot", decided, "8월 11일 16시로 정했습니다."))
    week06._KANA_SUBAGENT = stub
    try:
        payload = payload_of(week06.kana_agent, {"query": "나랑 철수 8/11에 회의 잡아줘"})
    finally:
        week06._KANA_SUBAGENT = None

    assert payload["inner_tool_names"] == ["decide_final_slot"]
    assert payload["final_slot_payload"]["final_slot"] == "2026-08-11 16:00-17:00"
    assert payload["final_slot_payload"]["reason"] == "두 사람 모두 비어 있습니다."
    check("kana_agent: 실제 decide_final_slot 결과를 final_slot_payload로 승격")


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
        test_tool_descriptions_state_that_agent_picks,
        test_find_common_available_slots_drops_overlapping_candidates,
        test_find_common_available_slots_collects_busy_rows_when_missing,
        test_decide_final_slot_records_agent_choice,
        test_decide_final_slot_keeps_pending_without_selection,
        test_nana_agent_returns_answer_trace_and_inner_tool_names,
        test_kana_agent_lifts_final_payloads_from_inner_trace,
        test_kana_agent_lifts_real_decide_final_slot_output,
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
