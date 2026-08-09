from student_parts.week06_kanamate_decides_schedule import (
    _extract_kana_final_payloads,
    nana_system_prompt,
    supervisor_system_prompt,
)


def test_extract_kana_final_payloads_keeps_last_confirmed_slot_over_later_unconfirmed() -> None:
    confirmed = {
        "tool_name": "decide_final_slot",
        "final_slot": "2026-08-07 14:00-15:00",
        "needs_agent_selection": False,
        "reason": "첫 번째 후보가 가장 적합합니다.",
    }
    unconfirmed = {
        "tool_name": "decide_final_slot",
        "final_slot": None,
        "needs_agent_selection": True,
        "reason": "아직 선택이 필요합니다.",
    }

    _, final_decision_payload = _extract_kana_final_payloads(
        [
            {"event": "tool_result", "content": confirmed},
            {"event": "tool_result", "content": unconfirmed},
        ]
    )

    assert final_decision_payload == confirmed


def test_extract_kana_final_payloads_uses_last_unconfirmed_when_no_confirmed_slot_exists() -> None:
    first_unconfirmed = {
        "tool_name": "decide_final_slot",
        "final_slot": None,
        "needs_agent_selection": True,
        "reason": "후보가 더 필요합니다.",
    }
    last_unconfirmed = {
        "tool_name": "decide_final_slot",
        "final_slot": None,
        "needs_agent_selection": True,
        "reason": "사용자 확인이 필요합니다.",
    }

    _, final_decision_payload = _extract_kana_final_payloads(
        [
            {"event": "tool_result", "content": first_unconfirmed},
            {"event": "tool_result", "content": last_unconfirmed},
        ]
    )

    assert final_decision_payload == last_unconfirmed
