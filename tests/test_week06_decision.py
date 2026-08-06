"""Week 6 추가 과제 검증 — 후보 검증과 최종 시간 기록을 LLM 없이 확인한다.

이 두 tool의 설계 전제는 "파이썬이 고르지 않는다"이다. 후보와 최종 시간은 Kana agent가
tool description을 읽고 직접 골라 argument로 넘기고, 여기서는 그 값을 검증·기록만 한다.
그래서 입력과 출력이 전부 결정되고, LLM을 한 번도 부르지 않고 검증할 수 있다.

통과 케이스만 보면 필터가 실제로 동작하는지 알 수 없다. 후보를 넘기지 않아도 빈 목록이
돌아오기 때문이다. 그래서 탈락 경로를 하나씩 따로 확인한다 — 겹침, 근무 시간 밖, 길이 부족,
날짜 범위 밖. 반대로 "맞닿는 시간은 통과해야 한다"도 함께 둔다.

파일 구성은 네 묶음이다.
1. 최종 기록 계약 — decide_final_slot. 완전 순수.
2. 후보 검증 — find_common_available_slots_dict에 busy_rows를 직접 넘기는 갈래. 완전 순수.
3. 수집 갈래 — busy_rows를 넘기지 않아 collect_member_schedules로 직접 모으는 경로.
   앱 SQLite와 MCP 서브프로세스를 쓰지만, conftest가 두 저장소를 임시 경로로 돌린다.
4. tool 배선 — LLM이 실제로 보게 될 형태(JSON 문자열, description)를 확인한다.

실행: uv run --with pytest pytest tests/test_week06_decision.py -v
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.external_people_store import PERSONAL_SHARED_MEMBER_NAME  # noqa: E402
from fixed.schedule_decision import CommonSlotCandidate  # noqa: E402
from student_parts.week06_kanamate_decides_schedule import (  # noqa: E402
    DECIDE_FINAL_SLOT_DESCRIPTION,
    FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION,
    decide_final_slot,
    find_common_available_slots,
    find_common_available_slots_dict,
    kana_tools,
)

DATE_FROM = "2026-07-07"
DATE_TO = "2026-07-09"
# 철수가 7월 7일 10:00-11:00에 바쁘다. 후보 탈락 경로를 이 한 줄 기준으로 확인한다.
BUSY_ROW = {
    "member_name": "철수",
    "title": "API 연동 실습",
    "date": "2026-07-07",
    "start_time": "10:00",
    "end_time": "11:00",
}


def candidate(start_time: str, end_time: str, date: str = "2026-07-07", **extra) -> dict:
    """후보 하나를 만든다. 탈락 경로마다 바꾸는 값만 인자로 받는다."""

    return {"date": date, "start_time": start_time, "end_time": end_time, "reason": "셋 다 비어 있는 시간", **extra}


def find(candidate_slots: list, **overrides) -> dict:
    """busy_rows를 직접 넘기는 순수 갈래로 후보 검증을 부른다."""

    kwargs = {
        "member_names": ["철수"],
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "busy_rows": [BUSY_ROW],
        "candidate_slots": candidate_slots,
    }
    kwargs.update(overrides)
    return find_common_available_slots_dict(**kwargs)


def decide(**kwargs) -> dict:
    """decide_final_slot을 LLM이 부르는 방식(.invoke)으로 호출하고 payload를 돌려준다."""

    return json.loads(decide_final_slot.invoke(kwargs))


# 1. 최종 기록 계약 — decide_final_slot


def test_decide_records_the_slot_agent_chose():
    # agent가 고른 final_slot을 그대로 기록하는지
    payload = decide(
        candidate_slots=[candidate("15:00", "16:00", date="2026-07-14")],
        selected_index=0,
        final_slot="2026-07-14 15:00-16:00",
        needs_agent_selection=False,
    )
    assert payload["final_slot"] == "2026-07-14 15:00-16:00"
    assert payload["needs_agent_selection"] is False


def test_decide_keeps_final_slot_at_top_level():
    # extract_langchain_trace가 top-level final_slot으로 이 payload를 알아본다. 감싸면 trace에서 사라진다
    payload = decide(final_slot="2026-07-14 15:00-16:00")
    assert "final_slot" in payload


def test_decide_resolves_final_slot_from_selected_index():
    # final_slot 문자열 없이 번호만 지목해도 후보를 시간 문자열로 풀어내는지
    payload = decide(candidate_slots=[candidate("14:00", "15:00"), candidate("16:00", "17:00")], selected_index=1)
    assert payload["final_slot"] == "2026-07-07 16:00-17:00"


def test_decide_rejects_out_of_range_index():
    # (대조) 후보 범위를 벗어난 번호는 확정하지 않고 선택이 더 필요하다고 남긴다
    payload = decide(candidate_slots=[candidate("14:00", "15:00")], selected_index=9)
    assert payload["final_slot"] is None
    assert payload["needs_agent_selection"] is True
    assert "범위" in payload["reason"]


def test_decide_without_candidates_reports_no_common_slot():
    # 후보 자체가 없을 때. 위 테스트와 같은 미확정 상태지만 사유가 달라야 supervisor가 구분해 답할 수 있다
    payload = decide()
    assert payload["final_slot"] is None
    assert payload["needs_agent_selection"] is True
    assert payload["reason"] != decide(candidate_slots=[candidate("14:00", "15:00")], selected_index=9)["reason"]


def test_decide_serializes_pydantic_candidates():
    # 스키마가 후보를 Pydantic 객체로 바꿔 넘겨도 candidates에 repr이 박히거나 직렬화가 깨지지 않는지
    payload = decide(
        candidate_slots=[CommonSlotCandidate(date="2026-07-14", start_time="15:00", end_time="16:00")],
        selected_index=0,
    )
    assert payload["candidates"] == ["2026-07-14 15:00-16:00"]
    assert payload["final_slot"] == "2026-07-14 15:00-16:00"


def test_decide_keeps_evidence_fields():
    # 답변에는 안 쓰이지만 왜 그 시간인지 확인하려면 근거가 payload에 남아 있어야 한다
    payload = decide(
        candidate_slots=[candidate("14:00", "15:00")],
        selected_index=0,
        member_names=["나", "철수"],
        date_from="2026-07-07T00:00:00",
        date_to=DATE_TO,
        busy_rows=[BUSY_ROW],
    )
    assert payload["members"] == ["나", "철수"]
    assert payload["busy_rows"] == [BUSY_ROW]
    assert payload["date_from"] == "2026-07-07"


# 2. 후보 검증 — busy_rows를 직접 넘기는 순수 갈래


def test_find_keeps_candidate_without_conflict():
    # 아무와도 겹치지 않는 후보는 남는다
    payload = find([candidate("14:00", "15:00")])
    assert [(slot["start_time"], slot["end_time"]) for slot in payload["candidate_slots"]] == [("14:00", "15:00")]


def test_find_drops_candidate_overlapping_busy_row():
    # (대조) busy 10:00-11:00과 겹치는 후보는 빠지고, 겹치지 않는 후보만 남는다
    payload = find([candidate("10:30", "11:30"), candidate("14:00", "15:00")])
    assert [slot["start_time"] for slot in payload["candidate_slots"]] == ["14:00"]


def test_find_keeps_candidate_touching_busy_edge():
    # busy가 11:00에 끝나면 11:00 시작은 겹치지 않는다. 회의 직후 시간을 못 쓰면 후보가 지나치게 줄어든다
    payload = find([candidate("11:00", "12:00")])
    assert len(payload["candidate_slots"]) == 1


def test_find_treats_unknown_end_time_as_busy_until_midnight():
    # 종료 시각이 "미정"인 일정은 자정까지 바쁜 것으로 본다. 끝을 모르는 일정 뒤를 비었다고 볼 수 없다
    open_ended = {**BUSY_ROW, "end_time": "미정"}
    payload = find([candidate("14:00", "15:00")], busy_rows=[open_ended])
    assert payload["candidate_slots"] == []


def test_find_keeps_other_days_when_one_day_is_fully_busy():
    # (대조) "미정"이 막는 범위는 그 날 하루다. 다음 날 후보까지 사라지면 안 된다
    open_ended = {**BUSY_ROW, "end_time": "미정"}
    payload = find([candidate("14:00", "15:00", date="2026-07-08")], busy_rows=[open_ended])
    assert len(payload["candidate_slots"]) == 1


def test_find_drops_candidate_outside_workday():
    # 근무 시간(기본 09:00~18:00) 밖으로 나가는 후보는 빠진다
    payload = find([candidate("08:00", "09:30")])
    assert payload["candidate_slots"] == []


def test_find_drops_candidate_shorter_than_requested_duration():
    # 요청한 회의 길이를 못 채우는 후보는 빠진다
    payload = find([candidate("14:00", "14:30")], duration_minutes=60)
    assert payload["candidate_slots"] == []


def test_find_drops_candidate_outside_date_range():
    # 요청 범위 밖 날짜는 빠진다
    payload = find([candidate("14:00", "15:00", date="2026-07-20")])
    assert payload["candidate_slots"] == []


def test_find_normalizes_iso_datetime_bounds():
    # ISO datetime이 들어와도 날짜 부분만 쓴다. 안 자르면 날짜 범위 계산이 ValueError로 죽는다
    payload = find([candidate("14:00", "15:00")], date_from="2026-07-07T00:00:00", date_to="2026-07-09T23:59:59")
    assert len(payload["candidate_slots"]) == 1


def test_find_recomputes_duration_from_times():
    # 길이는 agent가 적어 보낸 값이 아니라 실제 시각 차이로 다시 계산한다
    payload = find([candidate("14:00", "15:00", duration_minutes=999)])
    assert payload["candidate_slots"][0]["duration_minutes"] == 60


def test_find_respects_limit():
    # 후보가 많아도 요청한 개수까지만 남긴다
    slots = [candidate("13:00", "14:00"), candidate("14:00", "15:00"), candidate("15:00", "16:00")]
    payload = find(slots, limit=2)
    assert len(payload["candidate_slots"]) == 2


def test_find_includes_me_in_members():
    # 내 일정도 겹침 판단 근거이므로 기록에 내가 들어가야 한다
    payload = find([])
    assert payload["members"] == [PERSONAL_SHARED_MEMBER_NAME, "철수"]


def test_find_does_not_repeat_me_in_members():
    # (대조) agent가 member_names에 "나"를 넣어 보내도 두 번 들어가지 않는다
    payload = find([], member_names=["나", "철수", "나"])
    assert payload["members"] == [PERSONAL_SHARED_MEMBER_NAME, "철수"]


def test_find_blocks_busy_row_whose_date_carries_time():
    # busy_rows는 agent가 텍스트로 복사해 넘긴다. 날짜에 시간이 붙어 오면 겹침 비교가 문자열
    # 완전 일치라 그 일정을 통째로 놓치고, 이미 회의가 있는 시간을 빈 시간으로 추천하게 된다
    iso_row = {**BUSY_ROW, "date": "2026-07-07T00:00:00"}
    payload = find([candidate("10:30", "11:30")], busy_rows=[iso_row])
    assert payload["candidate_slots"] == []


def test_find_drops_candidate_with_unparsable_time():
    # agent가 "14시"처럼 형식을 어겨 보낼 수 있다. 해석할 수 없는 시각은 통과시키지 않는다
    payload = find([candidate("14시", "15시")])
    assert payload["candidate_slots"] == []


def test_find_drops_candidate_with_reversed_times():
    # 종료가 시작보다 앞선 후보. 길이 계산이 음수가 되므로 통과시키면 안 된다
    payload = find([candidate("15:00", "14:00")])
    assert payload["candidate_slots"] == []


def test_find_keeps_busy_rows_as_evidence():
    # 후보가 전부 탈락해도 무엇 때문에 탈락했는지 볼 수 있어야 한다
    payload = find([candidate("10:30", "11:30")])
    assert payload["candidate_slots"] == []
    assert payload["busy_rows"] == [BUSY_ROW]


# 3. 수집 갈래 — busy_rows를 넘기지 않은 경우


def test_find_collects_busy_rows_when_not_given():
    # busy_rows가 없으면 collect_member_schedules로 직접 모은다. agent가 복사를 빠뜨렸을 때의 안전망이다
    payload = find_common_available_slots_dict(
        member_names=["철수"], date_from="2026-07-07", date_to="2026-07-17", busy_rows=None
    )
    assert payload["busy_rows"], "외부 멤버 일정을 수집하지 못했다"
    assert {row["member_name"] for row in payload["busy_rows"]} == {"철수"}


def test_find_collects_nothing_for_unknown_member():
    # (대조) 저장소에 없는 이름이면 0건이어야 한다. 무필터 상위집합이 돌아오면 위 테스트가 무의미해진다
    payload = find_common_available_slots_dict(
        member_names=["없는사람"], date_from="2026-07-07", date_to="2026-07-17", busy_rows=None
    )
    assert payload["busy_rows"] == []


# 4. tool 배선 — LLM이 보게 될 형태


def test_find_tool_returns_json_string():
    # tool은 dict가 아니라 JSON 문자열을 돌려준다
    raw = find_common_available_slots.invoke(
        {
            "member_names": ["철수"],
            "date_from": DATE_FROM,
            "date_to": DATE_TO,
            "busy_rows": [BUSY_ROW],
            "candidate_slots": [candidate("14:00", "15:00")],
        }
    )
    assert isinstance(raw, str)
    assert json.loads(raw)["tool_name"] == "find_common_available_slots"


def test_find_tool_payload_has_no_final_slot_key():
    # 후보 검증 결과에 final_slot이 섞이면 trace가 이걸 최종 결정으로 잘못 잡는다
    raw = find_common_available_slots.invoke(
        {"member_names": ["철수"], "date_from": DATE_FROM, "date_to": DATE_TO, "busy_rows": [BUSY_ROW]}
    )
    assert "final_slot" not in json.loads(raw)


def test_tool_descriptions_are_not_empty():
    # description이 빈 문자열이면 docstring으로 대체되지 않고 그대로 비어 등록된다.
    # 앱은 정상 기동하고 agent만 근거 없이 tool을 부르게 되므로 눈에 띄지 않는다.
    assert FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION.strip()
    assert DECIDE_FINAL_SLOT_DESCRIPTION.strip()
    assert find_common_available_slots.description == FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION
    assert decide_final_slot.description == DECIDE_FINAL_SLOT_DESCRIPTION


def test_kana_tools_expose_both_decision_tools():
    # Kana가 두 tool을 실제로 볼 수 있어야 3단 흐름이 성립한다
    names = [tool_object.name for tool_object in kana_tools()]
    assert {"collect_member_schedules", "find_common_available_slots", "decide_final_slot"} <= set(names)
