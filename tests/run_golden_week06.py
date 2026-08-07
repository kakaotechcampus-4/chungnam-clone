"""Week 6 골든 데이터셋 실행기.

사용법:
  python tests/run_golden_week06.py                  # unit 테스트만 (LLM 불필요)
  python tests/run_golden_week06.py --agent           # unit + agent 테스트 (LLM 필요)
  python tests/run_golden_week06.py --tier all        # 추가과제 포함
"""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLDEN_FILE = Path(__file__).parent / "golden_week06.json"


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

    if "contains_all" in expect:
        text = str(result)
        for term in expect["contains_all"]:
            if term not in text:
                reasons.append(f"'{term}'이 결과에 없음")

    if reasons:
        fail(case_id, " | ".join(reasons))
    else:
        ok(case_id)


# ── unit 테스트 실행 ───────────────────────────────────────

def run_logic(tier_filter: str) -> None:
    """_pick_last_ok 재시도 기준을 직접 검증합니다 (tool 호출 불필요)."""
    try:
        from student_parts.week06_kanamate_decides_schedule import _pick_last_ok
    except Exception as e:
        print(f"  ⚠️  _pick_last_ok import 실패 ({e})")
        return

    success = {"final_slot": "2026-08-12 10:00-11:00", "ok": True}
    failure = {"final_slot": None, "ok": False}
    no_ok_field = {"final_slot": "2026-08-13 14:00-15:00"}

    # 성공 → 실패 재시도: 성공 결과 유지
    result = _pick_last_ok(None, success)
    result = _pick_last_ok(result, failure)
    if result == success:
        ok("retry_failure_keeps_success")
    else:
        fail("retry_failure_keeps_success", f"실패 결과가 성공을 덮어씀: {result}")

    # 실패 → 성공 재시도: 나중 성공으로 교체
    result = _pick_last_ok(None, failure)
    result = _pick_last_ok(result, success)
    if result == success:
        ok("retry_success_replaces_failure")
    else:
        fail("retry_success_replaces_failure", f"성공 결과로 교체되지 않음: {result}")

    # ok 필드 없는 결과는 성공으로 간주
    result = _pick_last_ok(success, no_ok_field)
    if result == no_ok_field:
        ok("no_ok_field_treated_as_success")
    else:
        fail("no_ok_field_treated_as_success", f"ok 없는 결과를 실패로 취급함: {result}")

    # None 시작 → 첫 성공 결과 저장
    result = _pick_last_ok(None, success)
    if result == success:
        ok("first_success_stored_from_none")
    else:
        fail("first_success_stored_from_none", f"첫 성공 결과가 저장되지 않음: {result}")


def run_unit(cases: list[dict], tier_filter: str) -> None:
    try:
        from student_parts.week06_kanamate_decides_schedule import (
            find_common_available_slots,
            decide_final_slot,
            supervisor_system_prompt,
        )
    except Exception as e:
        print(f"  ⚠️  week06 import 실패 ({e}) — unit 테스트 건너뜀")
        return

    TOOL_MAP = {
        "find_common_available_slots": find_common_available_slots,
        "decide_final_slot": decide_final_slot,
    }

    for c in cases:
        if tier_filter == "main" and c.get("tier") not in ("main", None):
            continue

        case_id = c["id"]
        tool_name = c["tool"]

        # 특수 케이스: prompt 내용 확인
        if tool_name == "_prompt_check":
            prompt = supervisor_system_prompt()
            result = {"prompt": prompt}
            check_expect(case_id, result, c["expect"])
            continue

        tool_fn = TOOL_MAP.get(tool_name)
        if tool_fn is None:
            fail(case_id, f"tool '{tool_name}'을 찾지 못함")
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
        from student_parts.week06_kanamate_decides_schedule import build_langchain_supervisor_agent
        from fixed.langchain_trace import extract_agent_events
    except Exception as e:
        print(f"  ⚠️  week06 import 실패 ({e}) — agent 테스트 건너뜀")
        return

    try:
        agent = build_langchain_supervisor_agent()
    except RuntimeError as e:
        print(f"  ⚠️  agent 생성 실패 ({e}) — PROXY_TOKEN 확인 필요")
        return

    for c in cases:
        if tier_filter == "main" and c.get("tier") not in ("main", None):
            continue

        case_id = c["id"]
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": c["input"]}]})
        except Exception as e:
            fail(case_id, f"agent 실행 오류: {e}")
            continue

        events = extract_agent_events(result)
        supervisor_called_tools = [e["tool_name"] for e in events if e.get("event") == "tool_call"]

        # inner_tool_names: nana_agent / kana_agent 결과에서 꺼냄
        inner_tool_names: list[str] = []
        for e in events:
            content = e.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except Exception:
                    content = {}
            if isinstance(content, dict):
                inner_tool_names.extend(content.get("inner_tool_names") or [])

        reasons: list[str] = []

        if "expect_supervisor_calls" in c:
            expected = c["expect_supervisor_calls"]
            if expected not in supervisor_called_tools:
                reasons.append(
                    f"supervisor가 '{expected}'를 호출하지 않음 (호출: {supervisor_called_tools})"
                )

        if "expect_inner_tool_called" in c:
            expected = c["expect_inner_tool_called"]
            if expected not in inner_tool_names:
                reasons.append(
                    f"inner tool '{expected}'이 호출되지 않음 (호출: {inner_tool_names})"
                )

        if "expect_inner_tool_sequence" in c:
            seq = c["expect_inner_tool_sequence"]
            for t in seq:
                if t not in inner_tool_names:
                    reasons.append(f"inner tool '{t}'이 호출되지 않음 (호출: {inner_tool_names})")

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
    print(f"  📋 Week 6 골든 테스트  (tier={tier_filter}, agent={'on' if args.agent else 'off'})")
    print(f"{'─'*55}")

    print("\n[logic — _pick_last_ok 재시도 기준]")
    run_logic(tier_filter)

    print("\n[unit — LLM 불필요]")
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
