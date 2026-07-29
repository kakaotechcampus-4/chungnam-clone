"""Week 5 MCP wrapper 검증 — 실제 MCP 서버를 stdio 서브프로세스로 띄워 계약을 확인한다.

가짜 객체를 주입하지 않고 전부 실제로 호출한다. MCP 서버는 로컬 서브프로세스라 네트워크나
토큰이 필요 없다. 대신 KANANA_EXTERNAL_DB_PATH를 임시 경로로 돌려서, 실제 공유 저장소
(data/kanana_external_people.sqlite3)를 건드리지 않는다.

실호출만으로는 "내가 의도한 인자를 넘겼는지"를 증명할 수 없다. 예를 들어 member_names를
빠뜨려도 철수의 row가 포함된 상위집합이 돌아와 단정문이 통과한다. 그래서 음성 대조를 함께 둔다:
필터 결과는 무필터보다 엄격히 작아야 하고, 빈 리스트는 0건이어야 한다.

실행: uv run --with pytest pytest tests/test_week05_mcp.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sys

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.external_people_store import ExternalPeopleSQLiteStore  # noqa: E402
from student_parts.week05_load_kanas_past_conversations import (  # noqa: E402
    CollectMemberSchedulesInput,
    CreateSharedScheduleInput,
    DeleteSharedScheduleInput,
    ExtractSchedulesFromHistoryInput,
    ListSharedSchedulesInput,
    LoadConversationMessagesInput,
    SearchPreviousConversationsInput,
    create_shared_schedule,
    delete_shared_schedule,
    extract_schedules_from_history,
    list_shared_schedules,
    load_conversation_messages,
    load_langchain_mcp_tools_sync,
    search_previous_conversations,
)

# 외부 실습 데이터(서버가 뜰 때마다 seed된다)
JULY_FROM = "2026-07-07"
JULY_TO = "2026-07-17"
MEMBERS = ["철수", "영희", "민준", "서연", "지훈", "하린"]
SEEDED_SCHEDULE_COUNT = 18
CHULSOO_TITLES = {"API 연동 실습", "고객 인터뷰", "QA 리뷰"}
MCP_TOOL_NAMES = {
    "search_previous_conversations",
    "load_conversation_messages",
    "extract_schedules_from_history",
    "create_shared_schedule",
    "delete_shared_schedule",
    "list_shared_schedules",
}
CONTROL_SOURCE = "w5test:controls"


def call(tool: Any, **kwargs: Any) -> dict[str, Any]:
    """wrapper tool을 실제로 호출하고 JSON 문자열을 dict로 돌려준다."""

    return json.loads(tool.invoke(kwargs))


@pytest.fixture(scope="module", autouse=True)
def tmp_external_db(tmp_path_factory):
    """모든 MCP 호출을 임시 외부 DB로 돌린다.

    mcp_client가 호출 시점에 os.environ을 읽어 자식 프로세스에 넘기므로, 학생 코드를 고치지 않고
    저장소만 갈아끼울 수 있다. 새 DB에도 서버가 뜰 때 seed()가 실습 데이터를 다시 넣어 준다.
    monkeypatch fixture는 함수 단위라 module 단위 fixture에서는 인스턴스를 직접 만들어 쓴다.
    """

    path = tmp_path_factory.mktemp("week05_external") / "external_people.sqlite3"
    patch = pytest.MonkeyPatch()
    patch.setenv("KANANA_EXTERNAL_DB_PATH", str(path))
    yield path
    patch.undo()


@pytest.fixture(scope="module")
def seeded_controls(tmp_external_db):
    """무필터 기본값 분기를 검증할 음성 대조 row 2개를 심는다.

    하나는 기본 날짜 구간 밖(8월), 하나는 기본 멤버 목록에 없는 "나". 무필터 조회 결과에 이 둘이
    없어야 "기본 구간"과 "기본 멤버"가 모두 적용됐다는 뜻이 된다.
    id는 반드시 ext_로 시작하지 않는 자체 id를 쓴다. seed()가 ext_* row를 지우고 다시 넣기 때문에
    실습 데이터 id를 쓰면 서버가 뜰 때 사라진다.
    """

    store = ExternalPeopleSQLiteStore(tmp_external_db)
    store.create_shared_schedule(
        member_name="철수",
        title="구간 밖 일정",
        date="2026-08-01",
        start_time="09:00",
        end_time="10:00",
        source_conversation_id=CONTROL_SOURCE,
        schedule_id="w5test_out_of_window",
    )
    store.create_shared_schedule(
        member_name="나",
        title="내 일정",
        date="2026-07-08",
        start_time="09:00",
        end_time="10:00",
        source_conversation_id=CONTROL_SOURCE,
        schedule_id="w5test_me_in_window",
    )
    yield
    # 임시 파일이라 버려지지만, 심은 row는 항상 id로 되돌린다(공유 DB에 그대로 써도 안전하도록)
    store.delete_shared_schedules(source_conversation_id=CONTROL_SOURCE)


# ── 입력 스키마 경계 (강사가 준 제약을 내가 깨지 않았는지 확인하는 회귀 방지용, MCP 호출 없음) ──
def test_search_schema_defaults():
    # 기본값: member_names 없음, limit 5
    parsed = SearchPreviousConversationsInput(query="회의")
    assert parsed.member_names is None
    assert parsed.limit == 5


def test_search_schema_keeps_none_and_empty_distinct():
    # None(필터 없음)과 []("대상 없음")은 뜻이 달라 스키마가 구분해 보존해야 한다
    assert SearchPreviousConversationsInput(query="회의").member_names is None
    assert SearchPreviousConversationsInput(query="회의", member_names=[]).member_names == []


@pytest.mark.parametrize("limit", [1, 25, 50])
def test_search_schema_limit_allowed(limit):
    # limit 허용 범위 1~50
    assert SearchPreviousConversationsInput(query="회의", limit=limit).limit == limit


@pytest.mark.parametrize("limit", [0, -1, 51, 999])
def test_search_schema_limit_rejected(limit):
    # 범위를 벗어난 limit은 거부
    with pytest.raises(ValidationError):
        SearchPreviousConversationsInput(query="회의", limit=limit)


def test_search_schema_query_required():
    # query는 필수
    with pytest.raises(ValidationError):
        SearchPreviousConversationsInput()


def test_load_schema_conversation_id_required():
    # conversation_id는 필수
    with pytest.raises(ValidationError):
        LoadConversationMessagesInput()
    assert LoadConversationMessagesInput(conversation_id="ext_cs").conversation_id == "ext_cs"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"member_names": ["철수"]},
        {"member_names": ["철수"], "date_from": JULY_FROM},
        {"date_from": JULY_FROM, "date_to": JULY_TO},
    ],
)
def test_extract_schema_requires_three_fields(kwargs):
    # member_names/date_from/date_to 3개 모두 있어야 한다
    with pytest.raises(ValidationError):
        ExtractSchedulesFromHistoryInput(**kwargs)


def test_extract_schema_accepts_full_input():
    # 3개가 모두 있으면 통과
    parsed = ExtractSchedulesFromHistoryInput(member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO)
    assert parsed.member_names == ["철수"]


def test_collect_schema_requires_three_fields():
    # collect_member_schedules 입력도 같은 3개가 필수
    with pytest.raises(ValidationError):
        CollectMemberSchedulesInput(member_names=["철수"])
    parsed = CollectMemberSchedulesInput(member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO)
    assert parsed.date_to == JULY_TO


def test_list_schema_defaults():
    # 필터는 전부 선택, limit 기본 50
    parsed = ListSharedSchedulesInput()
    assert (parsed.member_names, parsed.date_from, parsed.date_to, parsed.source_conversation_id) == (
        None,
        None,
        None,
        None,
    )
    assert parsed.limit == 50


@pytest.mark.parametrize("limit", [1, 50, 200])
def test_list_schema_limit_allowed(limit):
    # limit 허용 범위 1~200
    assert ListSharedSchedulesInput(limit=limit).limit == limit


@pytest.mark.parametrize("limit", [0, 201, 999])
def test_list_schema_limit_rejected(limit):
    # 범위를 벗어난 limit은 거부
    with pytest.raises(ValidationError):
        ListSharedSchedulesInput(limit=limit)


def test_create_schema_defaults_and_required():
    # end_time 기본 "미정", 선택 필드는 None, 앞 4개는 필수
    parsed = CreateSharedScheduleInput(member_name="철수", title="회의", date="2026-07-20", start_time="15:00")
    assert parsed.end_time == "미정"
    assert (parsed.notes, parsed.source_conversation_id, parsed.schedule_id) == (None, None, None)
    with pytest.raises(ValidationError):
        CreateSharedScheduleInput(member_name="철수", title="회의", date="2026-07-20")


def test_delete_schema_both_optional():
    # 두 조건 모두 선택이라 아무것도 없이도 만들 수 있다(막는 책임은 저장소에 있다)
    parsed = DeleteSharedScheduleInput()
    assert (parsed.schedule_id, parsed.source_conversation_id) == (None, None)


# ── MCP 경계: tool 이름이 실제로 서버에 있는지 (오타 난 이름을 한 번에 잡는다) ──
def test_server_exposes_expected_tool_names():
    # 서버가 노출하는 tool 이름 6개 집합 — 내 코드에 없는 목록을 서버에서 받아온다
    assert {tool.name for tool in load_langchain_mcp_tools_sync()} == MCP_TOOL_NAMES


# ── search_previous_conversations ──
def test_search_returns_rows_envelope():
    # 반환이 JSON 문자열이고 ok/tool_name/rows 계약을 유지하는지
    payload = call(search_previous_conversations, query="일정")
    assert payload["ok"] is True
    assert payload["tool_name"] == "search_previous_conversations"
    assert payload["rows"]
    first = payload["rows"][0]
    assert {"conversation_id", "member_name", "title", "content", "created_at"} <= set(first)


def test_search_member_filter_actually_narrows():
    # (음성 대조) 필터 결과가 무필터보다 엄격히 작아야 member_names가 실제로 전달된 것이다
    unfiltered = call(search_previous_conversations, query="일정", limit=50)["rows"]
    filtered = call(search_previous_conversations, query="일정", member_names=["철수"], limit=50)["rows"]
    assert len(filtered) < len(unfiltered)
    assert {row["member_name"] for row in filtered} == {"철수"}


def test_search_empty_member_names_returns_nothing():
    # []는 "대상 멤버 없음"이라 0건 (None과 다르다)
    assert call(search_previous_conversations, query="일정", member_names=[])["rows"] == []


# ── load_conversation_messages ──
def test_load_returns_messages_in_time_order():
    # 메시지 필드가 보존되고 created_at 오름차순이 유지되는지
    rows = call(load_conversation_messages, conversation_id="ext_cs")["rows"]
    assert rows
    assert {"role", "sender", "content", "created_at"} <= set(rows[0])
    assert [row["created_at"] for row in rows] == sorted(row["created_at"] for row in rows)
    assert "API 연동 실습" in rows[0]["content"]


def test_load_unknown_conversation_returns_empty_rows():
    # 없는 대화 id는 에러가 아니라 빈 rows
    payload = call(load_conversation_messages, conversation_id="w5test_missing_conv")
    assert payload["ok"] is True
    assert payload["rows"] == []


# ── extract_schedules_from_history ──
def test_extract_returns_member_schedules_with_summary():
    # 필수 필드 6개가 보존되고 서버가 만든 schedule_summary가 함께 오는지
    payload = call(
        extract_schedules_from_history, member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO
    )
    rows = payload["rows"]
    assert {row["title"] for row in rows} == CHULSOO_TITLES
    assert all(
        {"member_name", "title", "date", "start_time", "end_time", "notes"} <= set(row) for row in rows
    )
    assert payload["schedule_summary"].strip()


def test_extract_empty_member_names_returns_nothing():
    # []는 0건 — 대상이 정해지기 전에 부르면 안 된다는 신호
    payload = call(extract_schedules_from_history, member_names=[], date_from=JULY_FROM, date_to=JULY_TO)
    assert payload["rows"] == []


def test_extract_date_window_excludes_outside_dates():
    # (음성 대조) 좁힌 날짜 구간 밖 일정이 빠져야 date_from/date_to가 실제로 전달된 것이다
    narrow = call(
        extract_schedules_from_history, member_names=["철수"], date_from=JULY_FROM, date_to="2026-07-07"
    )["rows"]
    assert {row["title"] for row in narrow} == {"API 연동 실습"}


# ── list_shared_schedules ──
def test_list_without_filters_uses_practice_defaults(seeded_controls):
    # 무필터면 서버가 기본 멤버·기본 구간으로 대체한다. 대조 row 2개는 빠져야 한다
    rows = call(list_shared_schedules, limit=200)["rows"]
    assert len(rows) == SEEDED_SCHEDULE_COUNT
    assert {row["member_name"] for row in rows} == set(MEMBERS)
    titles = {row["title"] for row in rows}
    assert "구간 밖 일정" not in titles, "기본 날짜 구간이 적용되지 않았다"
    assert "내 일정" not in titles, "기본 멤버 목록이 적용되지 않았다"


def test_list_member_filter_actually_narrows():
    # (음성 대조) 멤버 필터가 실제로 전달되는지
    rows = call(list_shared_schedules, member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO)["rows"]
    assert {row["member_name"] for row in rows} == {"철수"}
    assert {row["title"] for row in rows} == CHULSOO_TITLES


def test_list_limit_caps_row_count():
    # limit이 반환 건수 상한으로 작동하는지
    assert len(call(list_shared_schedules, limit=1)["rows"]) == 1


def test_list_empty_member_names_returns_nothing():
    # []는 0건
    assert call(list_shared_schedules, member_names=[])["rows"] == []


# ── create / delete (추가과제) ──
def test_shared_schedule_round_trip():
    # 등록 → 조회 → 같은 id로 재등록(갱신) → 삭제 → 조회 왕복. UPSERT라 두 번 등록해도 1건이다
    created = call(
        create_shared_schedule,
        member_name="철수",
        title="왕복 검증 회의",
        date="2026-07-20",
        start_time="15:00",
        end_time="16:00",
        source_conversation_id="w5test:roundtrip",
        schedule_id="w5test_rt_1",
    )
    assert created["shared_schedule"]["sync_status"] == "created"
    assert len(call(list_shared_schedules, source_conversation_id="w5test:roundtrip")["rows"]) == 1

    again = call(
        create_shared_schedule,
        member_name="철수",
        title="왕복 검증 회의 수정",
        date="2026-07-20",
        start_time="16:00",
        end_time="17:00",
        source_conversation_id="w5test:roundtrip",
        schedule_id="w5test_rt_1",
    )
    assert again["shared_schedule"]["sync_status"] == "updated"
    rows = call(list_shared_schedules, source_conversation_id="w5test:roundtrip")["rows"]
    assert len(rows) == 1, "같은 schedule_id인데 row가 늘었다면 UPSERT가 아니다"
    assert rows[0]["start_time"] == "16:00"

    deleted = call(delete_shared_schedule, schedule_id="w5test_rt_1")
    assert deleted["deleted_count"] == 1
    assert call(list_shared_schedules, source_conversation_id="w5test:roundtrip")["rows"] == []


def test_delete_matches_either_key_not_both():
    # 삭제 조건은 AND가 아니라 OR — 두 조건을 함께 주면 어느 한쪽이라도 맞는 row가 모두 지워진다
    for index in (1, 2):
        call(
            create_shared_schedule,
            member_name="영희",
            title=f"OR 검증 {index}",
            date="2026-07-21",
            start_time="10:00",
            end_time="11:00",
            source_conversation_id="w5test:or",
            schedule_id=f"w5test_or_{index}",
        )
    result = call(delete_shared_schedule, schedule_id="w5test_or_1", source_conversation_id="w5test:or")
    assert result["deleted_count"] == 2


def test_delete_without_match_is_not_failure():
    # 지울 대상이 없어도 실패가 아니라 deleted_count 0
    payload = call(delete_shared_schedule, schedule_id="w5test_never_created")
    assert payload["ok"] is True
    assert payload["deleted_count"] == 0


# ── seed 재실행이 테스트 데이터를 지우지 않는지 (4주차의 "정리가 조용히 실패" 재발 방지) ──
def test_seeded_test_rows_survive_next_mcp_call(seeded_controls):
    # 서버는 호출마다 새로 뜨고 그때 seed()가 돌지만, 자체 id로 심은 row는 남아 있어야 한다
    before = call(list_shared_schedules, source_conversation_id=CONTROL_SOURCE)["rows"]
    assert len(before) == 2
    call(search_previous_conversations, query="일정")
    after = call(list_shared_schedules, source_conversation_id=CONTROL_SOURCE)["rows"]
    assert len(after) == 2
