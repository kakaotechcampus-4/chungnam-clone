"""Week 3 골든 데이터셋 실행기.

사용법:
  python tests/run_golden_week03.py           # unit 테스트만 (LLM 불필요, 빠름)
  python tests/run_golden_week03.py --agent   # unit + agent 테스트 (LLM 필요)
  python tests/run_golden_week03.py --tier extra  # 추가과제 포함
"""

from __future__ import annotations

import json
import sys
import tempfile
import os
import argparse
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("KANANA_USE_LLM", "0")  # unit 테스트 기본값

GOLDEN_FILE = Path(__file__).parent / "golden_week03.json"


# ── 결과 집계 ──────────────────────────────────────────────

passed: list[str] = []
failed: list[tuple[str, str]] = []  # (id, reason)


def ok(case_id: str) -> None:
    passed.append(case_id)
    print(f"  ✅ {case_id}")


def fail(case_id: str, reason: str) -> None:
    failed.append((case_id, reason))
    print(f"  ❌ {case_id}  →  {reason}")


# ── 기대값 검증 헬퍼 ──────────────────────────────────────

def check_expect(case_id: str, result: dict, expect: dict, schedule_id: str | None = None) -> None:
    """result dict를 expect 명세와 대조한다."""

    reasons: list[str] = []

    if "ok" in expect and result.get("ok") != expect["ok"]:
        reasons.append(f"ok={result.get('ok')} (기대 {expect['ok']})")

    if "tool_name" in expect and result.get("tool_name") != expect["tool_name"]:
        reasons.append(f"tool_name={result.get('tool_name')} (기대 {expect['tool_name']})")

    if "has_field" in expect and expect["has_field"] not in result:
        reasons.append(f"'{expect['has_field']}' 필드 없음")

    if "rows_min_count" in expect:
        rows = result.get("rows", [])
        if len(rows) < expect["rows_min_count"]:
            reasons.append(f"rows 개수 {len(rows)} < {expect['rows_min_count']}")

    if "rows_contains_title" in expect:
        rows = result.get("rows", [])
        titles = [r.get("title") for r in rows]
        if expect["rows_contains_title"] not in titles:
            reasons.append(f"rows 에 title='{expect['rows_contains_title']}' 없음 (있는 것: {titles})")

    if "row_not_null" in expect and expect["row_not_null"] and result.get("row") is None:
        reasons.append("row 가 None")

    if "row_is_null" in expect and expect["row_is_null"] and result.get("row") is not None:
        reasons.append(f"row 가 None 이 아님: {result.get('row')}")

    if "row_contains_title" in expect:
        row = result.get("row") or {}
        if row.get("title") != expect["row_contains_title"]:
            reasons.append(f"row.title={row.get('title')} (기대 {expect['row_contains_title']})")

    if "all_rows_kind" in expect:
        rows = result.get("schedules", result.get("rows", []))
        wrong = [r.get("kind") for r in rows if r.get("kind") != expect["all_rows_kind"]]
        if wrong:
            reasons.append(f"personal_schedule 이 아닌 kind 포함: {wrong}")

    if "deleted_count_min" in expect:
        if result.get("deleted_count", 0) < expect["deleted_count_min"]:
            reasons.append(f"deleted_count={result.get('deleted_count')} < {expect['deleted_count_min']}")

    if "updated_title" in expect:
        updated = result.get("updated_schedule") or {}
        if updated.get("title") != expect["updated_title"]:
            reasons.append(f"updated_schedule.title={updated.get('title')} (기대 {expect['updated_title']})")

    if reasons:
        fail(case_id, " | ".join(reasons))
    else:
        ok(case_id)


# ── unit 테스트 실행 ──────────────────────────────────────

def run_unit(cases: list[dict], tier_filter: str, tmp_db: Path) -> None:
    from fixed.app_store import AppSQLiteStore
    from fixed.config import CONFIG

    # 테스트용 임시 DB 사용
    store = AppSQLiteStore(tmp_db)

    # tool 함수는 CONFIG.app_db_path 를 바라보므로 임시 경로로 교체
    import fixed.config as cfg_mod
    original_path = cfg_mod.CONFIG.app_db_path

    import dataclasses
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=tmp_db)

    # week03 툴 import (구현 후 동작)
    try:
        from student_parts.week03_build_nanas_logbook import (
            save_structured_request,
            list_saved_requests,
            get_saved_request,
            personal_list_saved_schedules,
            personal_delete_saved_schedules,
            personal_update_saved_schedule,
            personal_create_schedule,
        )
    except Exception as e:
        print(f"  ⚠️  week03 import 실패 ({e}) — unit 테스트 건너뜀")
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)
        return

    TOOL_MAP = {
        "save_structured_request": save_structured_request,
        "list_saved_requests": list_saved_requests,
        "get_saved_request": get_saved_request,
        "personal_list_saved_schedules": personal_list_saved_schedules,
        "personal_delete_saved_schedules": personal_delete_saved_schedules,
        "personal_update_saved_schedule": personal_update_saved_schedule,
        "personal_create_schedule": personal_create_schedule,
    }

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]
        tool_fn = TOOL_MAP.get(c["tool"])
        if tool_fn is None:
            fail(case_id, f"tool '{c['tool']}' 을 찾지 못함")
            continue

        # pre_save: 테스트 전 데이터 삽입
        saved_request_id: str | None = None
        saved_schedule_id: str | None = None
        if "pre_save" in c:
            pre = c["pre_save"]
            try:
                raw = save_structured_request.invoke(pre)
                pre_result = json.loads(raw)
                saved_request_id = pre_result.get("request_id")
                saved_schedule_id = pre_result.get("schedule_id")
            except Exception as e:
                fail(case_id, f"pre_save 실패: {e}")
                continue

        # 입력 결정
        inp = c.get("input", {})
        if inp == "use_saved_request_id":
            if not saved_request_id:
                fail(case_id, "pre_save 에서 request_id 를 못 얻음")
                continue
            inp = {"request_id": saved_request_id}
        elif inp == "use_saved_schedule_id":
            if not saved_schedule_id:
                fail(case_id, "pre_save 에서 schedule_id 를 못 얻음")
                continue
            inp = {"schedule_ids": [saved_schedule_id]}

        if c.get("input_id") == "use_saved_schedule_id":
            if not saved_schedule_id:
                fail(case_id, "pre_save 에서 schedule_id 를 못 얻음")
                continue
            inp = {**inp, "schedule_id": saved_schedule_id}

        # tool 호출
        try:
            raw = tool_fn.invoke(inp)
            result = json.loads(raw)
        except Exception as e:
            fail(case_id, f"tool 실행 오류: {e}")
            continue

        check_expect(case_id, result, c["expect"])

    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)


# ── agent 테스트 실행 ─────────────────────────────────────

def run_agent(cases: list[dict], tier_filter: str) -> None:
    try:
        from student_parts.week03_build_nanas_logbook import build_week03_agent
        from fixed.langchain_trace import extract_agent_events
    except Exception as e:
        print(f"  ⚠️  week03 import 실패 ({e}) — agent 테스트 건너뜀")
        return

    try:
        agent = build_week03_agent()
    except RuntimeError as e:
        print(f"  ⚠️  agent 생성 실패 ({e}) — PROXY_TOKEN 확인 필요")
        return

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": c["input"]}]})
        except Exception as e:
            fail(case_id, f"agent 실행 오류: {e}")
            continue

        events = extract_agent_events(result)
        called_tools = [e["tool_name"] for e in events if e.get("event") == "tool_call"]

        reasons: list[str] = []

        # tool 호출 순서 검증
        if "expect_tool_sequence" in c:
            seq = c["expect_tool_sequence"]
            for t in seq:
                if t not in called_tools:
                    reasons.append(f"'{t}' 가 호출되지 않음 (호출된 것: {called_tools})")

        # 특정 tool 호출 여부
        if "expect_tool_called" in c:
            t = c["expect_tool_called"]
            if t not in called_tools:
                reasons.append(f"'{t}' 가 호출되지 않음 (호출된 것: {called_tools})")

        # 저장된 필드 검증 (save_structured_request tool_result 에서)
        if "expect_saved_fields" in c:
            save_results = [
                e["content"] for e in events
                if e.get("event") == "tool_result" and e.get("tool_name") == "save_structured_request"
            ]
            if not save_results:
                reasons.append("save_structured_request 결과 없음")
            else:
                saved = save_results[-1] if isinstance(save_results[-1], dict) else {}
                for field, expected_val in c["expect_saved_fields"].items():
                    actual = saved.get(field) or (saved.get("saved_data") or {}).get(field)
                    if actual != expected_val:
                        reasons.append(f"saved.{field}={actual} (기대 {expected_val})")

        if reasons:
            fail(case_id, " | ".join(reasons))
        else:
            ok(case_id)


# ── 메인 ─────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true", help="agent 테스트도 실행 (LLM 필요)")
    parser.add_argument("--tier", choices=["main", "all"], default="main", help="main=메인과제만, all=추가과제 포함")
    args = parser.parse_args()

    data = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    tier_filter = args.tier

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_db = Path(tmp_dir) / "test_kanana.sqlite3"

        print(f"\n{'─'*50}")
        print(f"  📋 Week 3 골든 테스트  (tier={tier_filter}, agent={'on' if args.agent else 'off'})")
        print(f"{'─'*50}")

        print("\n[unit — LLM 없음]")
        run_unit(data["unit"], tier_filter, tmp_db)

        if args.agent:
            print("\n[agent — LLM 호출]")
            run_agent(data["agent"], tier_filter)

        print(f"\n{'─'*50}")
        total = len(passed) + len(failed)
        print(f"  결과: {len(passed)}/{total} 통과")
        if failed:
            print("  실패 목록:")
            for fid, reason in failed:
                print(f"    ❌ {fid}: {reason}")
        else:
            print("  🎉 전부 통과")
        print(f"{'─'*50}\n")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
