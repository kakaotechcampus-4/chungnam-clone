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
    # 외부 멤버 busy-time 질문 → 근거가 tool 결과에 실제로 들어 있어야 한다.
    # tool 선택에 편차가 있어(일정 조회 계열 중 무엇을 고르는지) 한 번까지 재시도한다
    def has_evidence(turns):
        calls, results, _ = turns[0]
        return bool(BUSY_TIME_TOOLS & {name for name, _ in calls}) and "API 연동 실습" in repr(results)

    turns, ok = chat_turns_until(
        has_evidence, f"{JULY_FROM}부터 {JULY_TO}까지 철수와 영희가 언제 바쁜지 알려줘"
    )
    assert ok, f"tools={[name for name, _ in turns[0][0]]}"


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


# ── 시나리오 검증 회귀 — docs/week05_scenario_results.md에서 실제로 실패했던 입력을 그대로 고정한다 ──
def chat_turns(*texts: str) -> list[tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, Any]], str]]:
    """앱과 같은 조건으로 대화를 이어가며 여러 턴을 실행한다.

    앱(fixed/agent_runtime.py:_agent_messages)은 이력에 user/assistant 텍스트만 넘기고 tool 호출·결과는
    넘기지 않는다. 맥락 오염에서 비롯된 실패(16·17번)는 이 조건을 맞춰야 재현된다.
    """

    history: list[dict[str, str]] = []
    turns = []
    for text in texts:
        result = build_week_agent().invoke({"messages": [*history, {"role": "user", "content": text}]})
        events = extract_agent_events(result)
        answer = extract_final_text(result)
        history = [*history, {"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        turns.append((
            [(e["tool_name"], e.get("arguments") or {}) for e in events if e["event"] == "tool_call"],
            [(e["tool_name"], e.get("content")) for e in events if e["event"] == "tool_result"],
            answer,
        ))
    return turns


def chat_turns_until(check, *texts: str, attempts: int = 2):
    """기대를 만족할 때까지 최대 attempts회 실행하고 (마지막 결과, 성공 여부)를 돌려준다.

    LLM 응답에는 편차가 있다. 20개 시나리오를 반복 측정했을 때 21턴 전체가 한 번도 틀리지 않는 실행은
    약 3회 중 2회였고, 나머지 1회는 매번 다른 항목 하나가 조회를 건너뛰었다. 항목별로 따로 돌리면
    모두 통과하므로 결정적 실패가 아니라 편차다.

    재시도가 진짜 회귀까지 가려 버리면 안 되므로 횟수는 2회로 제한한다. 두 번 모두 실패하면
    편차로 설명되지 않는 문제로 본다.
    """

    turns = None
    for _ in range(attempts):
        turns = chat_turns(*texts)
        if check(turns):
            return turns, True
    return turns, False


def test_e2e_regression_query_with_extra_words_still_finds_evidence():
    # 시나리오 12: '준비'라는 한 단어 때문에 부분 문자열 대조가 깨져 0건으로 끝났다.
    # 영희 대화에는 "7월 16일 15시는 발표 리허설입니다"가 실제로 들어 있다.
    def found(turns):
        answer = turns[0][2]
        return "발표 리허설" in answer and not any(
            k in answer for k in ("찾을 수 없", "찾지 못", "기록이 없"))

    turns, ok = chat_turns_until(found, "영희가 발표 리허설 준비에 대해 뭐라고 했는지 찾아줘")
    assert ok, turns[0][2]


def test_e2e_regression_unknown_conversation_id_is_not_answered_with_other_talks():
    # 시나리오 15: 없는 id인데 앱 RAG가 찾은 내 지난 대화를 그 대화의 내용인 것처럼 나열했다.
    def honest(turns):
        answer = turns[0][2]
        return (any(k in answer for k in ("없", "찾지 못", "확인할 수 없"))
                and "7월 15일에 바쁜 팀원" not in answer)

    turns, ok = chat_turns_until(honest, "ext_zz 대화 내용 보여줘")
    assert ok, turns[0][2]


def test_e2e_regression_no_filter_listing_returns_practice_rows():
    # 시나리오 6: 날짜를 묻지 않았는데 오늘 날짜 필터를 넣어 기본값 대체 분기가 꺼지고 0건이 됐다.
    def listed_practice_rows(turns):
        payloads = tool_contents(turns[0][1], "list_shared_schedules")
        rows = [row for payload in payloads if isinstance(payload, dict)
                for row in (payload.get("rows") or [])]
        return len(rows) >= 18

    turns, ok = chat_turns_until(listed_practice_rows, "공유 일정 저장소에 등록되어 있는 일정을 보여줘")
    assert ok, f"기본 실습 일정이 조회되지 않음: {turns[0][0]}"


def test_e2e_regression_collect_does_not_pass_assistant_name():
    # 시나리오 5: member_names에 비서 이름('나나')을 넣어 호출이 낭비됐다.
    def only_external_members(turns):
        return all(
            "나" not in (args.get("member_names") or []) and "나나" not in (args.get("member_names") or [])
            for name, args in turns[0][0] if name == "collect_member_schedules"
        )

    turns, ok = chat_turns_until(
        only_external_members, "2026년 7월 7일부터 17일까지 나와 철수가 각각 언제 바쁜지 정리해줘"
    )
    assert ok, turns[0][0]


def test_e2e_regression_unspecified_target_is_not_narrowed_to_previous_members():
    # 시나리오 16: 앞 대화의 영희·민준으로 범위를 좁혀 7월 17일의 하린을 놓쳤다.
    turns, ok = chat_turns_until(
        lambda t: "하린" in t[-1][2],
        "2026년 7월 7일부터 17일까지 영희와 민준이 각각 언제 바쁜지 정리해줘",
        "7월 17일에 바쁜 사람 있어?",
    )
    assert ok, turns[-1][2]


def test_e2e_regression_answer_requires_lookup():
    # 시나리오 17: 앞 답변만 보고 조회 없이 "없습니다"라고 단정했다.
    turns, ok = chat_turns_until(
        lambda t: bool(t[-1][0]),
        "2026년 7월 7일부터 17일까지 영희와 민준이 각각 언제 바쁜지 정리해줘",
        "7월 6일에 바쁜 사람 있어?",
    )
    assert ok, "조회 tool을 부르지 않고 답했다"


def test_e2e_regression_delete_reuses_returned_schedule_id():
    # 시나리오 20: 방금 tool 결과로 받은 schedule_id를 사용자에게 되물으며 삭제하지 않았다.
    # 등록된 row는 임시 외부 DB에 생기므로 파일과 함께 버려진다.
    def deleted_one(turns):
        payloads = tool_contents(turns[-1][1], "delete_shared_schedule")
        return any((payload.get("deleted_count") or 0) >= 1
                   for payload in payloads if isinstance(payload, dict))

    turns, ok = chat_turns_until(
        deleted_one,
        "공유 저장소에 2026년 7월 20일 14시 스터디를 영희 이름으로 등록해줘",
        "방금 등록한 거 삭제해줘",
    )
    assert ok, turns[-1][2]
