# Week 3 오프라인 검증 스크립트 — LLM(프록시) 없이 기록장 전체를 검증한다.
#
# [왜 이 테스트가 가능한가]
#   agent에서 LLM이 하는 일은 "어떤 tool을 어떤 인자로 부를지 결정"과 "마지막 문장 작성"뿐이다.
#   tool → AppSQLiteStore → SQLite로 이어지는 나머지 전체는 결정적(deterministic) 코드라서,
#   LLM이 보냈을 인자를 이 스크립트가 직접 공급하면(= LLM인 척) 프록시 장애와 무관하게
#   저장/분기/조회/영속성을 완전하게 검증할 수 있다.
#
# [무엇을 검증하나]
#   1단계(현재 프로세스): 저장 tool → 두 테이블 동시 기록, kind별 분기(todo/reminder/unknown),
#                        조회 tool 3종(빈 결과 row=None 포함)
#   2단계(새 프로세스):   이 파일이 스스로를 새 파이썬 프로세스로 다시 실행한다.
#                        새 프로세스 = 메모리 완전 초기화 = 앱 재시작과 동일 조건.
#                        거기서도 일정이 조회되면 "기록장은 죽지 않는다"가 증명된다.
#   마지막: [오프라인검증] 마커가 붙은 더미 데이터를 전부 삭제해 실제 기록장을 오염시키지 않는다.
#
# [사용법] repo 루트에서:
#   uv run python tests/test_week03_logbook.py
#
# 모든 검증은 assert로 확인하므로, 조용히 끝나지 않고 실패 시 즉시 멈춘다.

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# 이 파일은 tests/ 안에 있으므로, repo 루트를 import 경로에 직접 추가한다.
# (student_parts/fixed 모듈을 찾기 위함 — 노트북의 find_repo_root와 같은 목적)
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from student_parts.week03_build_nanas_logbook import (  # noqa: E402 (경로 설정 후 import)
    _save_input_from,
    get_saved_request,
    list_saved_requests,
    personal_create_schedule,
    personal_delete_saved_schedules,
    personal_list_saved_schedules,
    personal_update_saved_schedule,
    save_structured_request,
    save_structured_request_payload,
)

# 더미 데이터를 구분하는 마커. 청소(cleanup)가 이 접두어만 지우므로 실제 데이터는 안전하다.
MARKER = "[오프라인검증]"
DB_PATH = REPO_ROOT / "data" / "kanana_app.sqlite3"


def stage1_save_and_query() -> None:
    """1단계: LLM이 보냈을 인자를 직접 공급해 저장·분기·조회를 검증한다."""

    print("=" * 64)
    print("① 저장 — extract 결과를 모사한 인자로 save tool 직접 호출")
    out = json.loads(save_structured_request.invoke({
        "kind": "personal_schedule",
        "title": f"{MARKER} 개인 코칭",
        "date": "2026-07-17",
        "start_time": "11:00",
        "members": [],
        "reason": "오프라인 검증용 더미",
        "original_text": "모레 11시 개인 코칭 저장해줘",
    }))
    assert out["ok"] is True
    # 일정 저장은 영수증(structured_requests) + 서랍(schedules) 두 테이블에 남아야 한다.
    tables = sorted(row["table"] for row in out["saved_rows"])
    assert tables == ["schedules", "structured_requests"], tables
    request_id = out["request_id"]
    print(f"  두 테이블 저장 확인, request_id={request_id}")

    print("② kind 분기 — todo/reminder/unknown이 각자 맞는 서랍으로 가는가")
    expected_tables = {
        "todo": ["structured_requests", "todos"],
        "reminder": ["reminders", "structured_requests"],
        "unknown": ["structured_requests"],  # unknown은 영수증에만 남는 안전 분류
    }
    for kind, expected in expected_tables.items():
        o = json.loads(save_structured_request.invoke({"kind": kind, "title": f"{MARKER} {kind}"}))
        got = sorted(row["table"] for row in o["saved_rows"])
        assert got == sorted(expected), f"{kind}: {got}"
        print(f"  {kind:9s} → {got}")

    print("③ 조회 tool 3종")
    rows = json.loads(list_saved_requests.invoke({}))["rows"]
    assert len([r for r in rows if (r.get("title") or "").startswith(MARKER)]) == 4
    row = json.loads(get_saved_request.invoke({"request_id": request_id}))["row"]
    assert row is not None and row["title"] == f"{MARKER} 개인 코칭"
    # 없는 ID는 예외가 아니라 row=None으로 돌아와야 한다 (빈 결과 = 정상 데이터).
    missing = json.loads(get_saved_request.invoke({"request_id": "req_존재하지않음"}))["row"]
    assert missing is None
    schedules = json.loads(personal_list_saved_schedules.invoke({}))["schedules"]
    assert any(s["title"] == f"{MARKER} 개인 코칭" for s in schedules)
    print("  목록/단건/없는 ID(None)/일정 조회 모두 통과")


def stage3_additional_assignments() -> None:
    """추가 과제 검증: 입력 정규화, 삭제 guard, 수정, 삭제, Week 1 호환 이중 기록.

    이 stage가 만드는 일정은 stage 안에서 전부 삭제하므로(삭제 tool 검증을 겸함)
    stage2(부활 확인)의 기대 개수에는 영향을 주지 않는다.
    """

    print("=" * 64)
    print("⑥ 입력 정규화 — 레거시 봉투/JSON 문자열이 저장 입력으로 풀리는가")
    unwrapped = _save_input_from({"structured_request": {"kind": "todo", "title": f"{MARKER} 봉투"}})
    assert unwrapped.title == f"{MARKER} 봉투" and unwrapped.kind == "todo"
    from_json = _save_input_from('{"kind": "reminder", "title": "제이슨"}')
    assert from_json.kind == "reminder"
    print("  봉투 벗기기 + JSON 문자열 정규화 통과")

    print("⑦ 삭제 guard — 조건 없는 삭제는 거부되는가 (심층 방어 2차)")
    refused = json.loads(personal_delete_saved_schedules.invoke({}))
    assert refused["ok"] is False and refused["deleted_count"] == 0
    print(f"  거부 확인: {refused['error']}")

    print("⑧ 수정 — 부분 수정(None=변경 안 함)과 없는 ID 처리")
    saved = save_structured_request_payload(
        {"kind": "personal_schedule", "title": f"{MARKER} 수정대상", "date": "2026-07-20", "start_time": "09:00"}
    )
    schedule_id = next(row["id"] for row in saved["saved_rows"] if row["table"] == "schedules")
    updated = json.loads(personal_update_saved_schedule.invoke({"schedule_id": schedule_id, "start_time": "12:00"}))
    # start_time만 바뀌고 title/date는 그대로여야 한다(부분 수정).
    assert updated["ok"] is True
    assert updated["updated_schedule"]["start_time"] == "12:00"
    assert updated["updated_schedule"]["title"] == f"{MARKER} 수정대상"
    missing = json.loads(personal_update_saved_schedule.invoke({"schedule_id": "sch_없는것", "title": "x"}))
    assert missing["ok"] is False
    print("  부분 수정 + 없는 ID(ok=False) 통과")

    print("⑨ 삭제 — schedule_ids 명시 삭제와 결과 3종 세트")
    deleted = json.loads(personal_delete_saved_schedules.invoke({"schedule_ids": [schedule_id]}))
    assert deleted["ok"] is True and deleted["deleted_count"] == 1
    assert deleted["filters"]["schedule_ids"] == [schedule_id]
    remaining = json.loads(personal_list_saved_schedules.invoke({}))["schedules"]
    assert all(row["schedule_id"] != schedule_id for row in remaining)
    print("  명시 삭제 + deleted_count/filters/deleted 반환 + 목록에서 제거 통과")

    print("⑩ Week 1 호환 이중 기록 — 임시 메모리와 SQLite 동시 기록 + 중복 방지")
    created = json.loads(personal_create_schedule.invoke(
        {"title": f"{MARKER} 이중기록", "date": "2026-07-21", "start_time": "14:00"}
    ))
    # Week 1 반환 계약(created_schedule) + Week 3 확장(structured_request/sqlite_save) 모두 존재해야 한다.
    assert created["ok"] is True and "created_schedule" in created
    assert created["structured_request"]["source_schedule_id"] == created["created_schedule"]["id"]
    assert created["sqlite_save"]["ok"] is True
    # 같은 임시 일정을 다시 저장하면 source_schedule_id 멱등성 가드가 중복을 막아야 한다.
    again = save_structured_request_payload(created["structured_request"])
    assert again.get("already_exists") is True
    dual_id = next(row["id"] for row in created["sqlite_save"]["saved_rows"] if row["table"] == "schedules")
    json.loads(personal_delete_saved_schedules.invoke({"schedule_ids": [dual_id]}))
    print("  이중 기록 + 멱등성(already_exists) + 정리 통과")


def stage2_survival_check() -> None:
    """2단계(새 프로세스에서 실행됨): 재시작 후에도 저장분이 살아있는지 확인한다."""

    print("=" * 64)
    print("④ 재시작(새 프로세스) 후 부활 확인")
    schedules = json.loads(personal_list_saved_schedules.invoke({}))["schedules"]
    survivors = [s for s in schedules if s["title"].startswith(MARKER)]
    assert len(survivors) == 1, f"일정 생존 {len(survivors)}건 (기대 1)"
    rows = json.loads(list_saved_requests.invoke({}))["rows"]
    kinds = sorted(r["kind"] for r in rows if (r.get("title") or "").startswith(MARKER))
    assert kinds == ["personal_schedule", "reminder", "todo", "unknown"], kinds
    print(f"  일정 1건 + 요청 4건({kinds}) 전부 생존 — 기록장 영속성 증명")


def cleanup() -> None:
    """더미 데이터를 모든 테이블에서 제거한다. 실제 기록장 데이터는 건드리지 않는다."""

    conn = sqlite3.connect(DB_PATH)
    with conn:
        ids = [r[0] for r in conn.execute(
            "SELECT request_id FROM structured_requests WHERE title LIKE ?", (f"{MARKER}%",)).fetchall()]
        if ids:
            marks = ",".join("?" * len(ids))
            for table in ("schedules", "todos", "reminders"):
                conn.execute(f"DELETE FROM {table} WHERE request_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM structured_requests WHERE request_id IN ({marks})", ids)
    conn.close()
    print(f"⑤ 청소 완료 — 더미 {len(ids)}건 삭제, 실제 기록은 그대로")


if __name__ == "__main__":
    if "--stage2" in sys.argv:
        # 부모 프로세스가 띄운 "재시작 시뮬레이션" 프로세스로 진입한 경우.
        stage2_survival_check()
        sys.exit(0)

    try:
        stage1_save_and_query()
        stage3_additional_assignments()
        # 자기 자신을 새 파이썬 프로세스로 실행한다 — 메모리가 완전히 초기화되므로
        # 앱을 껐다 켠 것과 같은 조건에서 SQLite 영속성을 검증할 수 있다.
        result = subprocess.run([sys.executable, __file__, "--stage2"], cwd=REPO_ROOT)
        assert result.returncode == 0, "2단계(재시작 검증) 실패"
    finally:
        # 검증이 중간에 실패해도 더미 데이터는 반드시 치운다.
        cleanup()
    print()
    print("✅ Week 3 오프라인 검증 전체 통과")
