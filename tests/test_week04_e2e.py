"""Week 4 e2e 라우팅 테스트 — 자연어 입력 → agent가 실제로 호출하는 tool 확인.

앱에서 사람이 채팅을 치는 것과 같은 경로(build_week_agent().invoke)를 그대로 실행하고,
trace의 tool_call·tool_result를 검증한다. 실제 LLM·임베딩 프록시를 호출하므로 느리다.
빠른 결정적 유닛은 tests/test_week04_memory.py 참고.

회귀 테스트 구획은 docs/week04_scenario_results.md의 수동 검증에서 실제로 실패했던 입력을
그대로 고정한다. 근거 데이터는 테스트가 직접 심고, 만든 row만 id로 지운다.

실행: uv run --with pytest pytest tests/test_week04_e2e.py -v
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.langchain_trace import extract_agent_events, extract_final_text  # noqa: E402
from fixed.runtime_clock import current_app_date_iso  # noqa: E402
from student_parts.week04_retrieve_nanas_memory import (  # noqa: E402
    REFERENCE_STORE,
    SQLITE_STORE,
    build_week_agent,
)

# "저장 기록 조회"는 목록/검색 tool 중 무엇을 골라도 정답으로 본다.
SAVED_SIDE = {"search_saved_requests", "personal_list_saved_schedules", "list_saved_requests"}
FRIDAY_RULE = "나는 금요일 오후엔 외부 미팅을 잡지 않는다"
GROUP_SCHEDULE_TITLE = "회귀검증 팀 리뷰"


def run_agent(query: str) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, Any]], str]:
    """앱과 같은 경로로 질문을 실행하고 (tool_call, tool_result, 최종답변)을 돌려준다."""

    result = build_week_agent().invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)
    calls = [(e["tool_name"], e.get("arguments") or {}) for e in events if e["event"] == "tool_call"]
    results = [(e["tool_name"], e.get("content")) for e in events if e["event"] == "tool_result"]
    return calls, results, extract_final_text(result)


def called_tools(query: str) -> list[str]:
    return [name for name, _ in run_agent(query)[0]]


def tool_contents(results: list[tuple[str, Any]], tool_name: str) -> list[Any]:
    return [content for name, content in results if name == tool_name]


def saved_ids_from_results(results: list[tuple[str, Any]]) -> list[str]:
    """저장 tool 결과에서 이 실행이 만든 request_id/schedule_id만 뽑는다."""

    ids: list[str] = []
    for name, content in results:
        if name != "save_structured_request" or not isinstance(content, dict):
            continue
        if content.get("request_id"):
            ids.append(content["request_id"])
        for row in content.get("saved_rows") or []:
            if isinstance(row, dict) and row.get("id"):
                ids.append(row["id"])
    return ids


def delete_saved_ids(ids: list[str]) -> None:
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    with SQLITE_STORE.connect() as conn:
        conn.execute(f"DELETE FROM schedules WHERE schedule_id IN ({marks})", tuple(ids))
        conn.execute(f"DELETE FROM structured_requests WHERE request_id IN ({marks})", tuple(ids))


@pytest.fixture()
def cleanup_saved_ids():
    """테스트가 만든 row만 id로 정리한다.

    시각 범위(created_at >= 시작시각)로 지우면 테스트 중 다른 경로가 만든 실제 데이터까지
    지울 수 있어, 식별 가능한 id만 삭제한다.
    """

    ids: list[str] = []
    yield ids
    delete_saved_ids(ids)


@pytest.fixture()
def seeded_group_schedule_tomorrow():
    """내일 날짜의 group_schedule을 심는다(종류 불특정 조회 회귀 검증용)."""

    tomorrow = (date.fromisoformat(current_app_date_iso()) + timedelta(days=1)).isoformat()
    saved = SQLITE_STORE.save_structured_request(
        {
            "kind": "group_schedule",
            "title": GROUP_SCHEDULE_TITLE,
            "date": tomorrow,
            "start_time": "16:00",
            "members": ["민수"],
            "reason": "회귀 테스트용 그룹 일정",
        }
    )
    ids = [row["id"] for row in saved["saved_rows"] if isinstance(row, dict) and row.get("id")]
    yield tomorrow
    delete_saved_ids(ids)


@pytest.fixture()
def seeded_friday_rule():
    """'금요일 오후엔 외부 미팅을 잡지 않는다' 참고자료를 심는다(선호 규칙 회귀 검증용)."""

    saved = REFERENCE_STORE.add_personal_reference(
        title="금요일 오후 미팅 규칙", content=FRIDAY_RULE, tags=["preference", "meeting"]
    )
    yield saved
    REFERENCE_STORE.collection.delete(ids=[saved["reference_id"]])


@pytest.mark.parametrize(
    "query",
    [
        "내가 회의 선호하는 시간 알려줘",
        "점심시간에 대한 내 규칙이 뭐였지?",
        "팀 싱크는 어떻게 하는 게 좋다고 했지?",
    ],
)
def test_e2e_routes_to_references(query):
    # 선호/메모/규칙 질문 → 참고자료 의미검색
    assert "search_personal_references" in called_tools(query)


@pytest.mark.parametrize(
    "query",
    [
        "내가 저장한 일정 목록 보여줘",
        "저장된 할 일 중에 보고서 있어?",
        "내일 저장된 일정 있어?",
    ],
)
def test_e2e_routes_to_saved(query):
    # 저장 기록 조회 질문 → 저장기록 계열 tool 중 하나
    assert SAVED_SIDE & set(called_tools(query))


@pytest.mark.parametrize(
    "query",
    [
        "저번 대화에서 무슨 얘기 했는지 찾아줘",
        "이전 대화 내용에서 검색해줘",
        "예전에 나눈 대화 중에 김치찌개 얘기 있었어?",
    ],
)
def test_e2e_routes_to_conversation(query):
    # 지난 대화 질문 → 대화 단위 RAG
    assert "search_conversation_messages" in called_tools(query)


@pytest.mark.parametrize(
    "query",
    [
        "다음 주 화요일 3시에 알고리즘 스터디 저장해줘",
        "내일 오전 10시 치과 정기검진 기록해줘",
    ],
)
def test_e2e_save_flow(query, cleanup_saved_ids):
    # 자연어 저장 요청 → 구조화(extract) 후 저장(save)까지 연쇄
    calls, results, _ = run_agent(query)
    tools = [name for name, _ in calls]
    cleanup_saved_ids.extend(saved_ids_from_results(results))
    assert "extract_schedule_request" in tools
    assert "save_structured_request" in tools


@pytest.mark.parametrize(
    "query",
    [
        "안녕 오늘 기분 어때?",
        "고마워 도움이 됐어",
    ],
)
def test_e2e_no_search_tool(query):
    # 검색이 필요 없는 잡담 → 아무 tool도 호출하지 않음
    assert called_tools(query) == []


# ── 회귀 테스트 — 수동 시나리오 검증에서 실제로 실패했던 입력을 그대로 고정한다 ──
def test_e2e_regression_ambiguous_question_searches_both_sources():
    # 시나리오 #3: 개선 전에는 saved_requests만 호출해 참고자료의 팀 싱크 메모를 놓쳤다.
    calls, results, _ = run_agent("팀 회의 관련 정보 알려줘")
    tools = {name for name, _ in calls}
    assert "search_personal_references" in tools, f"참고자료를 검색하지 않음: {tools}"
    assert SAVED_SIDE & tools, f"저장기록을 검색하지 않음: {tools}"
    reference_payloads = tool_contents(results, "search_personal_references")
    assert any(payload.get("hits") for payload in reference_payloads if isinstance(payload, dict))


def test_e2e_regression_unspecified_kind_includes_group_schedule(seeded_group_schedule_tomorrow):
    # 시나리오 #8: 개선 전에는 personal_list_saved_schedules(기본 kind=personal_schedule)로 끝나
    # 같은 날의 group_schedule을 놓쳤다. 심어 둔 그룹 일정이 조회 결과에 들어와야 한다.
    calls, results, _ = run_agent("내일 일정 뭐야?")
    tools = {name for name, _ in calls}
    assert SAVED_SIDE & tools, f"저장기록 조회 tool을 쓰지 않음: {tools}"
    blob = repr(results)
    assert GROUP_SCHEDULE_TITLE in blob, "내일의 group_schedule이 조회 결과에 없음"


def test_e2e_regression_feasibility_question_checks_preference(seeded_friday_rule):
    # 시나리오 #5: 개선 전에는 참고자료를 건너뛰고 저장 일정이 비었다는 이유로 "가능하다"고 답했다.
    # 이제는 선호 규칙을 먼저 확인하고, 그 규칙이 답변 근거로 쓰여야 한다.
    calls, results, answer = run_agent("금요일 오후에 미팅 잡아도 돼?")
    tools = [name for name, _ in calls]
    assert "search_personal_references" in tools, f"선호 규칙을 확인하지 않음: {tools}"
    assert "save_structured_request" not in tools, "가능 여부 질문인데 일정을 저장함"
    blob = repr(tool_contents(results, "search_personal_references"))
    assert "외부 미팅을 잡지 않는다" in blob, "금요일 규칙 메모가 검색 결과에 없음"
    assert "금요일" in answer, f"답변이 금요일 규칙을 언급하지 않음: {answer}"


def test_e2e_regression_weak_evidence_is_not_fabricated():
    # 리뷰 지적(retrieval_hint는 재호출을 보장하지 않음) 확인용.
    # 근거가 없을 때 ⑴ 질의를 바꿔 재검색하거나 ⑵ 지어내지 않고 못 찾았다고 답해야 한다.
    calls, results, answer = run_agent("내가 좋아하는 음식이 뭐라고 적어뒀지?")
    reference_queries = [args.get("query") for name, args in calls if name == "search_personal_references"]
    assert reference_queries, "참고자료를 검색하지 않음"
    judged_insufficient = any(
        isinstance(payload, dict) and payload.get("retrieval", {}).get("sufficient") is False
        for payload in tool_contents(results, "search_personal_references")
    )
    assert judged_insufficient, "근거 부족(sufficient=false) 판단이 없음"
    retried_with_new_query = len(set(q for q in reference_queries if q)) >= 2
    admitted_not_found = any(token in answer for token in ("찾지 못", "없습니다", "없어", "기록이 없"))
    assert retried_with_new_query or admitted_not_found, f"재검색도 없고 못 찾았다고도 하지 않음: {answer}"
