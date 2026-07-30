"""Week 5 e2e 라우팅 테스트 — 자연어 입력 → agent가 실제로 호출하는 MCP tool 확인.

앱에서 사람이 채팅을 치는 것과 같은 경로(build_week_agent().invoke)를 실행하고, trace의
tool_call·tool_result를 검증한다. 실제 LLM과 MCP 서버를 모두 호출하므로 느리다.
빠른 결정적 유닛은 tests/test_week05_mcp.py 참고.

이 주차의 agent는 tool을 21개 들고 있고, 그 안에 이름이 비슷한 두 검색 tool이 함께 있다.
search_previous_conversations(외부 멤버)와 search_conversation_messages(나와의 지난 대화)를
구분하는지가 가장 중요한 검증 대상이다.

호출 순서는 프롬프트로 보장할 수 없다(4주차 실측). 그래서 순서를 단정하지 않고,
⑴ 필요한 tool이 불렸는지 ⑵ 근거가 tool 결과에 실제로 들어 있는지 ⑶ conversation_id가
검색 결과에 있던 실재 id인지를 확인한다. ⑶이 "먼저 검색했는가"를 순서 없이 확인하는 방법이다.

외부 실습 데이터 구간은 2026-07-07~17이고 앱의 오늘은 2026-07-29로 고정돼 있다. 즉 실습 구간은
과거이므로 "다음 주" 같은 상대 표현을 쓰지 않고 날짜를 명시해 질문한다.

외부 공유 저장소는 임시 파일로 돌려 실제 데이터를 건드리지 않는다. 앱 DB에 생기는 row는 생성된
id로만 지운다.

실행: uv run --with pytest pytest tests/test_week05_e2e.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.config import CONFIG  # noqa: E402
from fixed.langchain_trace import extract_agent_events, extract_final_text  # noqa: E402
from student_parts.week04_retrieve_nanas_memory import SQLITE_STORE  # noqa: E402
from student_parts.week05_load_kanas_past_conversations import build_week_agent  # noqa: E402

pytestmark = pytest.mark.skipif(
    not CONFIG.has_openai_key, reason="PROXY_TOKEN이 없으면 agent를 실행할 수 없다"
)

JULY_FROM = "2026-07-07"
JULY_TO = "2026-07-17"
# 외부 실습 대화 id — LLM이 지어낸 id를 잡기 위한 실재 id 목록
EXTERNAL_CONVERSATION_IDS = {"ext_cs", "ext_yh", "ext_mj", "ext_sy", "ext_jh", "ext_hr"}
EXTERNAL_SEARCH_TOOLS = {"search_previous_conversations", "extract_schedules_from_history"}
BUSY_TIME_TOOLS = {"extract_schedules_from_history", "collect_member_schedules"}
SEEDED_MY_TITLE = "회귀검증 내 개인 일정"


def run_agent(query: str) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, Any]], str]:
    """앱과 같은 경로로 질문을 실행하고 (tool_call, tool_result, 최종답변)을 돌려준다."""

    result = build_week_agent().invoke({"messages": [{"role": "user", "content": query}]})
    events = extract_agent_events(result)
    calls = [(e["tool_name"], e.get("arguments") or {}) for e in events if e["event"] == "tool_call"]
    results = [(e["tool_name"], e.get("content")) for e in events if e["event"] == "tool_result"]
    return calls, results, extract_final_text(result)


def called_tools(query: str) -> set[str]:
    return {name for name, _ in run_agent(query)[0]}


def tool_contents(results: list[tuple[str, Any]], tool_name: str) -> list[Any]:
    return [content for name, content in results if name == tool_name]


@pytest.fixture(scope="module", autouse=True)
def tmp_external_db(tmp_path_factory):
    """agent가 만드는 공유 일정이 실제 저장소에 남지 않도록 임시 DB로 돌린다.

    mcp_client가 호출 시점에 os.environ을 읽으므로 agent를 먼저 만들어도 적용된다.
    임시 DB에도 서버가 뜰 때 seed()가 실습 데이터를 넣어 주므로 질문 근거는 그대로 있다.
    """

    path = tmp_path_factory.mktemp("week05_e2e_external") / "external_people.sqlite3"
    patch = pytest.MonkeyPatch()
    patch.setenv("KANANA_EXTERNAL_DB_PATH", str(path))
    yield path
    patch.undo()


@pytest.fixture()
def cleanup_saved_ids():
    """agent가 앱 DB에 저장한 row를 생성된 id로만 지운다.

    시각 범위로 지우면 테스트 중 다른 경로가 만든 실제 데이터까지 지울 수 있다.
    """

    ids: list[str] = []
    yield ids
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    with SQLITE_STORE.connect() as conn:
        conn.execute(f"DELETE FROM schedules WHERE schedule_id IN ({marks})", tuple(ids))
        conn.execute(f"DELETE FROM structured_requests WHERE request_id IN ({marks})", tuple(ids))


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


@pytest.fixture()
def seeded_my_schedule():
    """조회 구간 안에 내 개인 일정 하나를 심는다(합치기 검증용). 심은 id로만 지운다."""

    schedule_id = "w5e2e_my_schedule"
    with SQLITE_STORE.connect() as conn:
        conn.execute(
            "INSERT INTO schedules (schedule_id, request_id, owner, title, date, start_time, end_time,"
            " attendees_json, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (schedule_id, f"req_{schedule_id}", "me", SEEDED_MY_TITLE, "2026-07-08", "09:00", "10:00",
             "[]", "test", "2026-07-29T09:00:00"),
        )
    yield SEEDED_MY_TITLE
    with SQLITE_STORE.connect() as conn:
        conn.execute("DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,))


@pytest.mark.parametrize(
    "query",
    [
        "철수가 예전 대화에서 무슨 얘기 했는지 찾아줘",
        "철수랑 나눴던 지난 대화를 검색해줘",
    ],
)
def test_e2e_external_member_question_uses_mcp_search(query):
    # 다른 멤버의 지난 대화 질문 → 외부 MCP 검색 tool
    assert EXTERNAL_SEARCH_TOOLS & called_tools(query)


def test_e2e_external_member_question_does_not_use_app_rag_only():
    # 규칙 ⑨ 회귀: 이름이 비슷한 search_conversation_messages(내 대화 RAG)로만 끝내면 안 된다
    tools = called_tools("철수가 지난 대화에서 언제 시간이 된다고 했는지 알려줘")
    assert EXTERNAL_SEARCH_TOOLS & tools, f"외부 MCP tool을 쓰지 않음: {tools}"


def test_e2e_my_past_conversation_question_uses_app_rag():
    # (반대 방향 대조) 나와의 지난 대화 질문은 앱 RAG로 가야 한다
    tools = called_tools("우리가 저번 대화에서 무슨 얘기를 했는지 찾아줘")
    assert "search_conversation_messages" in tools, f"앱 대화 RAG를 쓰지 않음: {tools}"


def test_e2e_conversation_id_is_not_fabricated():
    # 규칙 ⑩ 회귀: load_conversation_messages를 부른다면 그 conversation_id는 실재해야 한다.
    # 호출 순서는 단정할 수 없지만, 지어낸 id는 이렇게 잡을 수 있다
    calls, _, _ = run_agent("철수의 이전 대화 원문 메시지를 그대로 불러와서 보여줘")
    load_ids = [args.get("conversation_id") for name, args in calls if name == "load_conversation_messages"]
    assert load_ids, f"원문 조회 tool을 쓰지 않음: {[name for name, _ in calls]}"
    assert all(value in EXTERNAL_CONVERSATION_IDS for value in load_ids), f"지어낸 id: {load_ids}"


def test_e2e_busy_time_question_returns_evidence():
    # 외부 멤버 busy-time 질문 → 근거가 tool 결과에 실제로 들어 있어야 한다
    calls, results, _ = run_agent(
        f"{JULY_FROM}부터 {JULY_TO}까지 철수와 영희가 언제 바쁜지 알려줘"
    )
    tools = {name for name, _ in calls}
    assert BUSY_TIME_TOOLS & tools, f"일정 추출 tool을 쓰지 않음: {tools}"
    assert "API 연동 실습" in repr(results), "철수의 실습 일정이 tool 결과에 없음"


def test_e2e_merge_question_includes_me_and_external_member(seeded_my_schedule):
    # 규칙 ⑫ 회귀: 내 일정 + 멤버 일정을 함께 묻는 질문은 두 출처가 같은 결과에 들어와야 한다
    calls, results, _ = run_agent(
        f"{JULY_FROM}부터 {JULY_TO}까지 나와 철수가 각각 언제 바쁜지 정리해줘"
    )
    tools = {name for name, _ in calls}
    assert "collect_member_schedules" in tools, f"통합 tool을 쓰지 않음: {tools}"
    blob = repr(tool_contents(results, "collect_member_schedules"))
    assert seeded_my_schedule in blob, "내 일정이 통합 결과에 없음"
    assert "API 연동 실습" in blob, "외부 멤버 일정이 통합 결과에 없음"


def test_e2e_shared_store_question_uses_list_tool():
    # 공유 저장소 자체를 확인하는 질문 → list_shared_schedules
    tools = called_tools("공유 일정 저장소에 등록되어 있는 일정을 보여줘")
    assert "list_shared_schedules" in tools, f"공유 저장소 조회 tool을 쓰지 않음: {tools}"


def test_e2e_shared_store_registration_uses_create_tool(cleanup_saved_ids):
    # 추가과제 회귀: 공유 저장소 등록 요청 → create_shared_schedule 호출 + schedule_id 반환
    calls, results, _ = run_agent(
        "공유 일정 저장소에 2026년 7월 20일 15시 테스트회의를 철수 이름으로 등록해줘"
    )
    cleanup_saved_ids.extend(saved_ids_from_results(results))
    tools = {name for name, _ in calls}
    assert "create_shared_schedule" in tools, f"공유 일정 등록 tool을 쓰지 않음: {tools}"
    payloads = tool_contents(results, "create_shared_schedule")
    assert any(
        isinstance(payload, dict) and payload.get("shared_schedule", {}).get("schedule_id")
        for payload in payloads
    ), "등록 결과에 schedule_id가 없음"


def test_e2e_no_tool_for_small_talk():
    # 검색이 필요 없는 잡담에는 아무 tool도 부르지 않는다
    assert called_tools("고마워 도움이 많이 됐어") == set()
