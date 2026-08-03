"""Week 5 MCP wrapper 검증 — 실제 MCP 서버를 stdio 서브프로세스로 띄워 계약을 확인한다.

가짜 객체를 주입하지 않고 전부 실제로 호출한다. MCP 서버는 로컬 서브프로세스라 네트워크나
토큰이 필요 없다. 대신 KANANA_EXTERNAL_DB_PATH를 임시 경로로 돌려서, 실제 공유 저장소
(data/kanana_external_people.sqlite3)를 건드리지 않는다.

실호출만으로는 "내가 의도한 인자를 넘겼는지"를 증명할 수 없다. 예를 들어 member_names를
빠뜨려도 철수의 row가 포함된 상위집합이 돌아와 단정문이 통과한다. 그래서 음성 대조를 함께 둔다:
필터 결과는 무필터보다 엄격히 작아야 하고, 빈 리스트는 0건이어야 한다.

파일 구성은 세 묶음이다.
1. 입력 스키마 경계 — 강사가 준 제약을 내가 깨지 않았는지 확인하는 회귀 방지용이다. MCP를 쓰지 않는다.
2. MCP wrapper 계약 — 서버를 실제로 띄워 tool 이름·반환 키·필터 동작을 확인한다.
3. 병합 로직 — 앱 SQLite와 대화 중 임시 일정을 다루므로 MCP를 쓰지 않고, 대신 앱 DB 경로를
   임시 파일로 돌린다.

실행: uv run --with pytest pytest tests/test_week05_mcp.py -v
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import json
import sys

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import student_parts.week05_load_kanas_past_conversations as week05  # noqa: E402
from fixed.app_store import AppSQLiteStore  # noqa: E402
from fixed.config import CONFIG as APP_CONFIG  # noqa: E402
from fixed.external_people_store import (  # noqa: E402
    ExternalPeopleSQLiteStore,
    external_schedule_summary,
)
from fixed.session_scope import conversation_session_scope  # noqa: E402
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES  # noqa: E402
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts, week04_tools  # noqa: E402
from student_parts.week05_load_kanas_past_conversations import (  # noqa: E402
    CollectMemberSchedulesInput,
    CreateSharedScheduleInput,
    DeleteSharedScheduleInput,
    ExtractSchedulesFromHistoryInput,
    ListSharedSchedulesInput,
    LoadConversationMessagesInput,
    SearchPreviousConversationsInput,
    _collect_member_schedules,
    _personal_schedules_for_current_scope,
    collect_member_schedules,
    create_shared_schedule,
    delete_shared_schedule,
    extract_schedules_from_history,
    list_shared_schedules,
    load_conversation_messages,
    build_week05_agent,
    load_langchain_mcp_tools_sync,
    search_previous_conversations,
    week05_prompt_parts,
    week05_system_prompt,
    week05_tools,
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


@pytest.fixture(scope="module")
def seeded_controls():
    """무필터 기본값 분기를 검증할 음성 대조 row 2개를 심는다.

    하나는 기본 날짜 구간 밖(8월), 하나는 기본 멤버 목록에 없는 "나". 무필터 조회 결과에 이 둘이
    없어야 "기본 구간"과 "기본 멤버"가 모두 적용됐다는 뜻이 된다.
    id는 반드시 ext_로 시작하지 않는 자체 id를 쓴다. seed()가 ext_* row를 지우고 다시 넣기 때문에
    실습 데이터 id를 쓰면 서버가 뜰 때 사라진다.
    """

    store = ExternalPeopleSQLiteStore(APP_CONFIG.external_db_path)
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


# ── _personal_schedules_for_current_scope (앱 SQLite + 현재 대화 임시 일정, MCP 호출 없음) ──
CONV_A = "w5test_conv_a"
CONV_B = "w5test_conv_b"


def insert_schedule(store: AppSQLiteStore, schedule_id: str, title: str, date: str = "2026-07-20") -> None:
    """앱 DB의 schedules에 row를 직접 넣는다.

    save_structured_request를 쓰면 공유 저장소 자동 동기화까지 함께 돌아 MCP 서브프로세스가 뜨므로,
    이 함수의 관심사(병합·중복 제거·범위 필터)만 보려고 raw SQL로 심는다.
    """

    with store.connect() as conn:
        conn.execute(
            "INSERT INTO schedules (schedule_id, request_id, owner, title, date, start_time, end_time,"
            " attendees_json, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (schedule_id, f"req_{schedule_id}", "me", title, date, "10:00", "11:00", "[]", "test",
             "2026-07-29T09:00:00"),
        )


def pending_schedule(
    schedule_id: str, title: str, session_id: str | None = CONV_A, date: str = "2026-07-20"
) -> dict[str, Any]:
    """Week 1이 대화 중에 메모리에만 만들어 두는 임시 일정 row 모양."""

    row: dict[str, Any] = {
        "id": schedule_id,
        "title": title,
        "date": date,
        "start_time": "14:00",
        "end_time": "15:00",
        "attendees": [],
        "created_at": "2026-07-29T09:00:00",
    }
    if session_id is not None:
        row["session_id"] = session_id
    return row


@pytest.fixture()
def tmp_app_db(tmp_path, monkeypatch):
    """앱 SQLite를 임시 파일로 돌린다.

    CONFIG는 frozen dataclass라 app_db_path만 바꿀 수 없어서, 모듈이 들고 있는 CONFIG 자체를
    갈아끼운다. 구현이 호출 시점에 AppSQLiteStore(CONFIG.app_db_path)를 만들기 때문에 이렇게 통한다.
    """

    path = tmp_path / "app.sqlite3"
    store = AppSQLiteStore(path)
    monkeypatch.setattr(week05, "CONFIG", SimpleNamespace(app_db_path=path))
    return store


@pytest.fixture()
def pending_sandbox():
    """PERSONAL_SCHEDULES는 Week 1의 모듈 전역 리스트라 테스트가 끝나면 원래 내용으로 되돌린다.

    pop()이 아니라 저장해 둔 사본으로 덮어쓴다. 테스트 중 다른 경로가 append했을 수도 있어
    개수를 가정할 수 없기 때문이다. 이름을 다시 묶지 않고 슬라이스로 넣어야 Week 1이 들고 있는
    같은 리스트 객체가 유지된다.
    """

    saved = list(PERSONAL_SCHEDULES)
    yield PERSONAL_SCHEDULES
    PERSONAL_SCHEDULES[:] = saved


def test_personal_scope_returns_saved_rows(tmp_app_db):
    # 앱 DB에 저장된 일정을 그대로 가져오는지
    insert_schedule(tmp_app_db, "w5test_sch_1", "치과 예약")
    insert_schedule(tmp_app_db, "w5test_sch_2", "스터디")
    titles = {row["title"] for row in _personal_schedules_for_current_scope()}
    assert titles == {"치과 예약", "스터디"}


def test_personal_scope_empty_when_both_sources_empty(tmp_app_db, pending_sandbox):
    # 두 출처가 모두 비어 있으면 빈 목록
    pending_sandbox.clear()
    assert _personal_schedules_for_current_scope() == []


def test_personal_scope_includes_pending_of_current_conversation(tmp_app_db, pending_sandbox):
    # 아직 저장되지 않은 이번 대화의 임시 일정이 포함되는지
    pending_sandbox.clear()
    pending_sandbox.append(pending_schedule("w5test_pending_1", "방금 만든 회의"))
    with conversation_session_scope(CONV_A):
        titles = {row["title"] for row in _personal_schedules_for_current_scope()}
    assert "방금 만든 회의" in titles


def test_personal_scope_excludes_pending_of_other_conversation(tmp_app_db, pending_sandbox):
    # (대조) 다른 대화의 임시 일정은 빠지고, 같은 대화의 것만 남아야 한다
    pending_sandbox.clear()
    pending_sandbox.append(pending_schedule("w5test_pending_a", "A 대화 회의", session_id=CONV_A))
    pending_sandbox.append(pending_schedule("w5test_pending_b", "B 대화 회의", session_id=CONV_B))
    with conversation_session_scope(CONV_A):
        titles = {row["title"] for row in _personal_schedules_for_current_scope()}
    assert "A 대화 회의" in titles
    assert "B 대화 회의" not in titles, "다른 대화의 임시 일정이 새어 들어왔다"


def test_personal_scope_pending_without_session_id_belongs_to_default(tmp_app_db, pending_sandbox):
    # session_id가 없는 임시 일정은 기본 범위로 취급된다 — 특정 대화 안에서는 빠져야 한다
    pending_sandbox.clear()
    pending_sandbox.append(pending_schedule("w5test_pending_none", "범위 없는 회의", session_id=None))
    outside = {row["title"] for row in _personal_schedules_for_current_scope()}
    with conversation_session_scope(CONV_A):
        inside = {row["title"] for row in _personal_schedules_for_current_scope()}
    assert "범위 없는 회의" in outside
    assert "범위 없는 회의" not in inside


def test_personal_scope_dedupes_pending_already_saved(tmp_app_db, pending_sandbox):
    # 임시 일정의 id가 저장된 일정의 schedule_id와 같으면 한 번만 세어야 한다
    insert_schedule(tmp_app_db, "w5test_same_id", "중복 확인 회의")
    pending_sandbox.clear()
    pending_sandbox.append(pending_schedule("w5test_same_id", "중복 확인 회의"))
    with conversation_session_scope(CONV_A):
        rows = _personal_schedules_for_current_scope()
    assert len(rows) == 1, "이미 저장된 임시 일정이 두 번 세어졌다"


def test_personal_scope_keeps_pending_with_different_id(tmp_app_db, pending_sandbox):
    # (대조) id가 다르면 서로 다른 일정이므로 2건이어야 한다. 이 대조가 없으면 위 테스트는
    # 중복 제거가 아니라 "임시 일정이 아예 빠지는 버그"로도 통과한다
    insert_schedule(tmp_app_db, "w5test_saved_id", "중복 확인 회의")
    pending_sandbox.clear()
    pending_sandbox.append(pending_schedule("w5test_other_id", "중복 확인 회의"))
    with conversation_session_scope(CONV_A):
        rows = _personal_schedules_for_current_scope()
    assert len(rows) == 2


@pytest.mark.parametrize("count", [15, 20])
def test_personal_scope_reads_beyond_default_limit(tmp_app_db, count):
    # list_schedules 기본 limit이 12라, 명시하지 않으면 13번째부터 조용히 잘린다
    for index in range(count):
        insert_schedule(tmp_app_db, f"w5test_many_{index}", f"일정 {index}")
    assert len(_personal_schedules_for_current_scope()) == count


def test_personal_scope_does_not_filter_by_kind(tmp_app_db):
    # kind 필터를 넘겼다면 structured_requests에 짝이 없는 row는 제외된다. 그룹 회의도 내가 바쁜
    # 시간이므로 종류로 걸러내지 않는다는 것을 이 row가 남는지로 확인한다
    insert_schedule(tmp_app_db, "w5test_no_kind", "종류 정보 없는 일정")
    titles = {row["title"] for row in _personal_schedules_for_current_scope()}
    assert "종류 정보 없는 일정" in titles


# ── _collect_member_schedules (내 일정 + 외부 멤버 일정 통합, 외부 조회는 실제 MCP 호출) ──
MERGED_ROW_KEYS = {"member_name", "title", "date", "start_time", "end_time", "notes"}


def my_schedule(
    title: str, date: str | None, start_time: str = "09:00", end_time: str = "10:00"
) -> dict[str, Any]:
    """_personal_schedules_for_current_scope가 돌려주는 내 일정 row 모양(앱 DB row 기준)."""

    return {
        "schedule_id": f"w5test_{title}",
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "attendees": [],
    }


def collect(members: list[str], personal: list[dict[str, Any]], **bounds: str) -> dict[str, Any]:
    """키워드 전용 인자를 매번 쓰지 않도록 감싼 호출."""

    return _collect_member_schedules(
        member_names=members,
        date_from=bounds.get("date_from", JULY_FROM),
        date_to=bounds.get("date_to", JULY_TO),
        personal_schedules=personal,
    )


def test_collect_unifies_row_keys():
    # 내 일정과 외부 일정이 모두 같은 6개 키를 갖는지 (notes는 빈 문자열일 수 있어 키 존재로 확인)
    result = collect(["철수"], [my_schedule("내 회의", "2026-07-08")])
    assert result["rows"]
    assert all(MERGED_ROW_KEYS <= set(row) for row in result["rows"])


def test_collect_labels_my_rows_and_keeps_external_names():
    # 내 일정은 "나"로, 외부 일정은 각자 이름으로 남는지
    result = collect(["철수"], [my_schedule("내 회의", "2026-07-08")])
    assert {row["member_name"] for row in result["rows"]} == {"나", "철수"}


def test_collect_keeps_my_rows_when_no_members_requested():
    # member_names=[]면 외부는 0건이지만 내 일정은 남아야 한다
    result = collect([], [my_schedule("내 회의", "2026-07-08")])
    assert {row["member_name"] for row in result["rows"]} == {"나"}


def test_collect_excludes_my_schedule_outside_window():
    # 조회 구간 밖의 내 일정은 제외한다. 구간 안 일정이 남는 것이 대조다
    result = collect(
        [], [my_schedule("구간 안 회의", "2026-07-08"), my_schedule("구간 밖 회의", "2026-08-01")]
    )
    titles = {row["title"] for row in result["rows"]}
    assert "구간 안 회의" in titles
    assert "구간 밖 회의" not in titles, "구간 밖 일정이 바쁜 시간으로 섞였다"


def test_collect_excludes_my_schedule_without_date():
    # 날짜가 없는 내 일정은 구간 판단이 불가능해 제외한다
    result = collect([], [my_schedule("날짜 없는 회의", None)])
    assert result["rows"] == []


def test_collect_normalizes_datetime_bounds():
    # 날짜에 시간이 붙어 와도 정규화된다. 원문 그대로 서버에 넘기면 문자열 비교에서 밀려 0건이 된다
    result = collect(
        ["철수"], [], date_from="2026-07-07T00:00:00", date_to="2026-07-07T23:59:59"
    )
    assert {row["title"] for row in result["rows"]} == {"API 연동 실습"}


def test_collect_sorts_merged_rows_chronologically():
    # 두 출처를 합친 순서는 합친 쪽이 정한다. 내 일정이 앞에 뭉치지 않고 날짜순 자리에 들어가야 한다
    result = collect(["철수"], [my_schedule("내 회의", "2026-07-08")])
    ordered = [(row["date"], row["start_time"]) for row in result["rows"]]
    assert ordered == sorted(ordered)
    assert result["rows"][0]["member_name"] == "철수", "가장 이른 7월 7일 일정은 철수 것이다"
    assert result["rows"][1]["member_name"] == "나", "7월 8일 내 일정이 두 번째 자리여야 한다"


def test_collect_summary_is_rebuilt_over_merged_rows():
    # 요약은 합친 rows로 다시 만든다. 서버가 준 외부 멤버만의 요약과 달라야 한다
    result = collect(["철수"], [my_schedule("내 회의", "2026-07-08")])
    external_only = call(
        extract_schedules_from_history, member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO
    )["schedule_summary"]
    assert "나" in result["schedule_summary"]
    assert result["schedule_summary"] != external_only, "서버 요약을 그대로 재사용했다"
    assert result["schedule_summary"] == external_schedule_summary(result["rows"])


def test_collect_empty_result_uses_helper_wording():
    # 아무 근거도 없으면 rows는 빈 목록이고 요약 문구도 헬퍼가 정한 그대로다
    result = collect([], [])
    assert result["rows"] == []
    assert result["schedule_summary"] == external_schedule_summary([])


# ── collect_member_schedules tool 배선 (두 helper를 실제로 연결했는지) ──
def test_collect_tool_returns_json_with_rows_and_summary(tmp_app_db, pending_sandbox):
    # 반환이 JSON 문자열이고 rows/schedule_summary 두 키를 가지며 한글이 그대로 실려오는지
    pending_sandbox.clear()
    raw = collect_member_schedules.invoke(
        {"member_names": ["철수"], "date_from": JULY_FROM, "date_to": JULY_TO}
    )
    assert "철수" in raw, "ensure_ascii=False로 한글이 그대로 나와야 한다"
    payload = json.loads(raw)
    assert set(payload) == {"rows", "schedule_summary"}
    assert all(MERGED_ROW_KEYS <= set(row) for row in payload["rows"])


def test_collect_tool_merges_my_saved_schedule_with_external(tmp_app_db, pending_sandbox):
    # 배선 검증: 앱 DB의 내 일정과 외부 멤버 일정이 함께 나와야 한다
    pending_sandbox.clear()
    insert_schedule(tmp_app_db, "w5test_tool_saved", "내 저장 일정", date="2026-07-08")
    payload = call(collect_member_schedules, member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO)
    assert {row["member_name"] for row in payload["rows"]} == {"나", "철수"}
    assert any(row["title"] == "내 저장 일정" for row in payload["rows"])


def test_collect_tool_without_my_schedules_returns_external_only(tmp_app_db, pending_sandbox):
    # (대조) 앱 DB가 비면 외부 일정만 나온다. 이 대조가 없으면 위 테스트의 "나"가 어디서 왔는지
    # 확인되지 않아, 내 일정을 읽지 않는 배선 누락도 통과할 수 있다
    pending_sandbox.clear()
    payload = call(collect_member_schedules, member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO)
    assert {row["member_name"] for row in payload["rows"]} == {"철수"}


def test_collect_tool_includes_pending_of_current_conversation(tmp_app_db, pending_sandbox):
    # 아직 저장하지 않은 이번 대화의 임시 일정도 tool 결과에 들어와야 한다.
    # 이걸 빼면 "방금 3시에 스터디 있다고 했는데" 그 시간을 비어 있다고 답한다
    pending_sandbox.clear()
    pending_sandbox.append(
        pending_schedule("w5test_tool_pending", "대화 중 만든 회의", date="2026-07-09")
    )
    with conversation_session_scope(CONV_A):
        payload = call(
            collect_member_schedules, member_names=["철수"], date_from=JULY_FROM, date_to=JULY_TO
        )
    assert any(row["title"] == "대화 중 만든 회의" for row in payload["rows"])


# ── tool 목록·프롬프트 배선 ──
def test_week05_tools_extend_week04_without_duplicates():
    # 4주차 tool을 그대로 유지한 채 5주차 7개가 더해지고, 이름이 겹치지 않는지
    names = [item.name for item in week05_tools()]
    assert {item.name for item in week04_tools()} <= set(names)
    assert MCP_TOOL_NAMES | {"collect_member_schedules"} <= set(names)
    assert len(names) == len(set(names)), "tool 이름이 겹치면 LLM이 어느 것을 부를지 알 수 없다"


def test_week05_prompt_keeps_previous_parts_and_appends_one():
    # 앞 주차 조각을 고치지 않고 5주차 조각 하나만 덧붙이는지
    previous = week04_prompt_parts()
    parts = week05_prompt_parts()
    assert parts[: len(previous)] == previous
    assert len(parts) == len(previous) + 1
    assert all(isinstance(part, str) and part.strip() for part in parts)


def test_week05_system_prompt_names_confusable_tools():
    # 이름이 비슷한 두 검색 tool을 프롬프트가 함께 언급해 구분을 알려주는지
    prompt = week05_system_prompt()
    for name in (
        "search_previous_conversations",
        "search_conversation_messages",
        "collect_member_schedules",
        "list_shared_schedules",
    ):
        assert name in prompt, f"프롬프트에 {name} 안내가 없다"


@pytest.mark.skipif(not APP_CONFIG.has_openai_key, reason="PROXY_TOKEN이 없으면 agent를 만들 수 없다")
def test_build_week05_agent_accepts_all_tools_and_caches():
    # 21개 tool로 agent가 실제로 만들어지는지, 그리고 같은 인스턴스를 재사용하는지
    assert build_week05_agent() is build_week05_agent()
