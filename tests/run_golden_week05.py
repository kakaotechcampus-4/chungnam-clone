"""Week 5 골든 데이터셋 실행기.

사용법:
  python tests/run_golden_week05.py                  # unit 테스트만 (MCP subprocess, OpenAI 불필요)
  python tests/run_golden_week05.py --agent           # unit + agent 테스트 (LLM 필요)
  python tests/run_golden_week05.py --tier all        # 추가과제 포함
"""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLDEN_FILE = Path(__file__).parent / "golden_week05.json"


# ── 결과 집계 ──────────────────────────────────────────────

passed: list[str] = []
failed: list[tuple[str, str]] = []


def ok(case_id: str) -> None:
    passed.append(case_id)
    print(f"  ✅ {case_id}")


def fail(case_id: str, reason: str) -> None:
    failed.append((case_id, reason))
    print(f"  ❌ {case_id}  →  {reason}")


# ── 기대값 검증 헬퍼 ──────────────────────────────────────

def check_expect(case_id: str, result: dict, expect: dict) -> None:
    reasons: list[str] = []

    if "ok" in expect and result.get("ok") != expect["ok"]:
        reasons.append(f"ok={result.get('ok')} (기대 {expect['ok']})")

    if "has_field" in expect and expect["has_field"] not in result:
        reasons.append(f"'{expect['has_field']}' 필드 없음 (있는 것: {list(result.keys())})")

    if "has_keys" in expect:
        for key in expect["has_keys"]:
            if key not in result:
                reasons.append(f"'{key}' 키 없음 (있는 것: {list(result.keys())})")

    if "rows_min_count" in expect:
        rows = result.get("rows", [])
        if len(rows) < expect["rows_min_count"]:
            reasons.append(f"rows 개수 {len(rows)} < {expect['rows_min_count']}")

    if "rows_empty" in expect and expect["rows_empty"]:
        rows = result.get("rows", [])
        if rows:
            reasons.append(f"rows가 비어있지 않음: {rows[:2]}")

    if "rows_member_name_all" in expect:
        rows = result.get("rows", [])
        expected_name = expect["rows_member_name_all"]
        bad = [r for r in rows if r.get("member_name") != expected_name]
        if bad:
            reasons.append(f"member_name이 '{expected_name}'이 아닌 row 존재: {[r.get('member_name') for r in bad]}")

    if "rows_first_has_keys" in expect:
        rows = result.get("rows", [])
        if not rows:
            reasons.append("rows가 비어있어 첫 항목을 확인할 수 없음")
        else:
            first = rows[0]
            for key in expect["rows_first_has_keys"]:
                if key not in first:
                    reasons.append(f"첫 row에 '{key}' 키 없음 (있는 것: {list(first.keys())})")

    if "deleted_min_count" in expect:
        deleted = result.get("deleted", [])
        if len(deleted) < expect["deleted_min_count"]:
            reasons.append(f"deleted 개수 {len(deleted)} < {expect['deleted_min_count']}")

    if reasons:
        fail(case_id, " | ".join(reasons))
    else:
        ok(case_id)


# ── unit 테스트 실행 (MCP subprocess 사용, OpenAI 불필요) ──

def run_unit(cases: list[dict], tier_filter: str) -> None:
    try:
        from student_parts.week05_load_kanas_past_conversations import (
            search_previous_conversations,
            load_conversation_messages,
            extract_schedules_from_history,
            list_shared_schedules,
            collect_member_schedules,
            create_shared_schedule,
            delete_shared_schedule,
        )
    except Exception as e:
        print(f"  ⚠️  week05 import 실패 ({e}) — unit 테스트 건너뜀")
        return

    TOOL_MAP = {
        "search_previous_conversations": search_previous_conversations,
        "load_conversation_messages": load_conversation_messages,
        "extract_schedules_from_history": extract_schedules_from_history,
        "list_shared_schedules": list_shared_schedules,
        "collect_member_schedules": collect_member_schedules,
        "create_shared_schedule": create_shared_schedule,
        "delete_shared_schedule": delete_shared_schedule,
    }

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]
        tool_name = c["tool"]
        tool_fn = TOOL_MAP.get(tool_name)
        if tool_fn is None:
            fail(case_id, f"tool '{tool_name}'을 찾지 못함")
            continue

        # pre_create: delete 테스트 전 먼저 일정 생성
        if "pre_create" in c:
            try:
                create_shared_schedule.invoke(c["pre_create"])
            except Exception as e:
                fail(case_id, f"pre_create 실패: {e}")
                continue

        inp = c.get("input", {})
        try:
            raw = tool_fn.invoke(inp)
            result = json.loads(raw)
        except Exception as e:
            fail(case_id, f"tool 실행 오류: {e}")
            continue

        check_expect(case_id, result, c["expect"])


# ── agent 테스트 실행 ─────────────────────────────────────

def run_agent(cases: list[dict], tier_filter: str) -> None:
    try:
        from student_parts.week05_load_kanas_past_conversations import build_week05_agent
        from fixed.langchain_trace import extract_agent_events
    except Exception as e:
        print(f"  ⚠️  week05 import 실패 ({e}) — agent 테스트 건너뜀")
        return

    try:
        agent = build_week05_agent()
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

        if "expect_tool_sequence" in c:
            for t in c["expect_tool_sequence"]:
                if t not in called_tools:
                    reasons.append(f"'{t}' 가 호출되지 않음 (호출된 것: {called_tools})")

        if "expect_tool_called" in c:
            t = c["expect_tool_called"]
            if t not in called_tools:
                reasons.append(f"'{t}' 가 호출되지 않음 (호출된 것: {called_tools})")

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

    print(f"\n{'─'*55}")
    print(f"  📋 Week 5 골든 테스트  (tier={tier_filter}, agent={'on' if args.agent else 'off'})")
    print(f"{'─'*55}")

    print("\n[unit — MCP subprocess 직접 호출, OpenAI 불필요]")
    run_unit(data["unit"], tier_filter)

    if args.agent:
        print("\n[agent — LLM 호출]")
        run_agent(data["agent"], tier_filter)

    print(f"\n{'─'*55}")
    total = len(passed) + len(failed)
    print(f"  결과: {len(passed)}/{total} 통과")
    if failed:
        print("  실패 목록:")
        for fid, reason in failed:
            print(f"    ❌ {fid}: {reason}")
    else:
        print("  🎉 전부 통과")
    print(f"{'─'*55}\n")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
