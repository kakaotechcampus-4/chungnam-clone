"""Week 3 실패 케이스 회귀 테스트.

정상 동작이 아니라 "중간에 한 단계만 실패하는 경우"를 고정해 두는 테스트입니다.
model이나 prompt를 바꿨을 때 중요한 동작이 깨졌는지 빠르게 확인하는 용도로 사용합니다.

실행: 저장소 루트에서  python tests/week03_failure_cases.py
LLM을 호출하지 않고 tool 레이어만 검증하므로 PROXY_TOKEN 없이도 동작합니다.
임시 DB를 사용하므로 실제 앱 DB(data/kanana_app.sqlite3)에는 영향이 없습니다.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixed.app_store import AppSQLiteStore
import student_parts.week03_build_nanas_logbook as w3
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES

TMP_DB = Path(tempfile.mkdtemp(prefix="week3_failure_")) / "app.sqlite3"
store = AppSQLiteStore(TMP_DB)
w3._store = lambda: store  # 모든 tool이 임시 DB를 쓰게 해 실제 앱 DB를 보호


def case_a_partial_failure() -> None:
    """A. 임시 메모리는 저장됐는데 SQLite 저장이 실패한 경우.

    이중 기록이 부분 상태로 남으면 안 된다. SQLite(진실의 원천) 저장이 실패하면
    방금 만든 임시 일정을 보상 삭제(rollback)하고 ok=False로 실패를 보고한다.
    같은 요청을 다시 보내도 메모리에 유령 일정이 쌓이지 않는다.
    """

    class FailingStore:
        def save_structured_request(self, payload):
            raise RuntimeError("모의 SQLite 장애")

    original_store = w3._store
    w3._store = lambda: FailingStore()
    try:
        result = json.loads(
            w3.personal_create_schedule.invoke(
                {"title": "부분 실패 테스트", "date": "2026-08-01", "start_time": "09:00"}
            )
        )
        retry = json.loads(
            w3.personal_create_schedule.invoke(
                {"title": "부분 실패 테스트", "date": "2026-08-01", "start_time": "09:00"}
            )
        )
    finally:
        w3._store = original_store

    assert result["ok"] is False, result  # 실패가 실패로 보고됨
    assert result["rolled_back"] is True, result  # 임시 일정이 보상 삭제됨
    assert "모의 SQLite 장애" in result["error"], result
    memory_titles = [s["title"] for s in PERSONAL_SCHEDULES]
    assert memory_titles.count("부분 실패 테스트") == 0, memory_titles  # 재시도 후에도 유령/중복 없음
    assert retry["ok"] is False and retry["rolled_back"] is True, retry
    assert store.list_schedules(limit=100) == []  # DB에도 없음 (둘 다 없거나, 둘 다 있거나)
    print("A. SQLite 실패 시 보상 롤백 + 재시도 중복 없음 OK")


def case_b_delete_all_with_filter() -> None:
    """B. 전체 삭제 값(delete_all=True)과 날짜 필터가 함께 들어온 경우.

    모델이 '그 날짜 것 다 지워줘'를 delete_all=True로 잘못 보내도
    명시 필터가 우선해 전체 삭제로 번지지 않아야 한다.
    """

    w3.save_structured_request_payload(
        {"kind": "personal_schedule", "title": "지울 일정", "date": "2026-08-05", "start_time": "10:00"},
        store=store,
    )
    w3.save_structured_request_payload(
        {"kind": "personal_schedule", "title": "남을 일정", "date": "2026-08-06", "start_time": "10:00"},
        store=store,
    )
    result = json.loads(
        w3.personal_delete_saved_schedules.invoke({"delete_all": True, "date": "2026-08-05"})
    )
    assert result["ok"] is True and result["deleted_count"] == 1, result
    remaining = store.list_schedules(limit=100)
    assert [s["title"] for s in remaining] == ["남을 일정"], remaining
    print("B. delete_all+날짜 필터 동시 입력 → 필터 우선(전체 삭제 방지) OK")


def case_c_kind_unspecified_list() -> None:
    """C. 일정 종류를 말하지 않은 조회.

    kind 미지정이면 personal_schedule만 보이고, group_schedule은
    kind를 명시해야 조회된다는 기본 동작을 고정한다.
    """

    w3.save_structured_request_payload(
        {"kind": "group_schedule", "title": "그룹 회의", "date": "2026-08-07", "members": ["철수"]},
        store=store,
    )
    default_titles = [
        s["title"] for s in json.loads(w3.personal_list_saved_schedules.invoke({}))["schedules"]
    ]
    assert "남을 일정" in default_titles and "그룹 회의" not in default_titles, default_titles
    group_titles = [
        s["title"]
        for s in json.loads(w3.personal_list_saved_schedules.invoke({"kind": "group_schedule"}))["schedules"]
    ]
    assert group_titles == ["그룹 회의"], group_titles
    print("C. kind 미지정 조회 → personal_schedule 한정, group은 kind 명시 필요 OK")


def case_d_no_condition_delete_guard() -> None:
    """D. 조건이 하나도 없는 삭제 요청은 거부되어야 한다."""

    refused = json.loads(w3.personal_delete_saved_schedules.invoke({}))
    assert refused["ok"] is False and refused["deleted_count"] == 0, refused
    print("D. 조건 없는 삭제 거부 guard OK")


def cleanup() -> None:
    """임시 DB 일정과 외부 공유 저장소 복사본을 정리합니다."""

    w3.delete_saved_schedules_dict(delete_all=True, app_store=store)


if __name__ == "__main__":
    case_a_partial_failure()
    case_b_delete_all_with_filter()
    case_c_kind_unspecified_list()
    case_d_no_condition_delete_guard()
    cleanup()
    print("\nALL FAILURE-CASE TESTS PASSED")
