"""Week 4 e2e 라우팅 테스트 — 자연어 입력 → agent가 실제로 호출하는 tool 확인.

앱에서 사람이 채팅을 치는 것과 같은 경로(build_week_agent().invoke)를 그대로 실행하고,
trace의 tool_call 이름이 기대한 검색 tool인지 검증한다. 실제 LLM·임베딩 프록시를 호출하므로
느리다. 빠른 결정적 유닛은 tests/test_week04_memory.py 참고.

실행: uv run --with pytest pytest tests/test_week04_e2e.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.langchain_trace import extract_agent_events  # noqa: E402
from fixed.store_base import now_iso  # noqa: E402
from student_parts.week04_retrieve_nanas_memory import SQLITE_STORE, build_week_agent  # noqa: E402

# "저장 기록 조회"는 목록/검색 tool 중 무엇을 골라도 정답으로 본다.
SAVED_SIDE = {"search_saved_requests", "personal_list_saved_schedules", "list_saved_requests"}


def called_tools(query: str) -> list[str]:
    result = build_week_agent().invoke({"messages": [{"role": "user", "content": query}]})
    return [event["tool_name"] for event in extract_agent_events(result) if event["event"] == "tool_call"]


@pytest.fixture()
def purge_saved():
    # 저장 흐름 테스트는 실제 DB에 일정을 만든다. 입력은 실제 사용자가 쓸 자연어 그대로 두고,
    # 정리는 "테스트 실행 중에 새로 생긴 row"만 삭제해 기존 기록을 건드리지 않는다.
    started_at = now_iso()
    yield
    with SQLITE_STORE.connect() as conn:
        conn.execute("DELETE FROM structured_requests WHERE created_at >= ?", (started_at,))
        conn.execute("DELETE FROM schedules WHERE created_at >= ?", (started_at,))


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
def test_e2e_save_flow(query, purge_saved):
    # 자연어 저장 요청 → 구조화(extract) 후 저장(save)까지 연쇄
    tools = called_tools(query)
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
