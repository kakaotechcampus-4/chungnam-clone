"""Week 4 골든 데이터셋 실행기.

사용법:
  python tests/run_golden_week04.py                        # unit 테스트만 (API 불필요, 빠름)
  python tests/run_golden_week04.py --embedding            # embedding 테스트 포함 (PROXY_TOKEN 필요)
  python tests/run_golden_week04.py --agent                # unit + agent 테스트 (LLM 필요)
  python tests/run_golden_week04.py --tier all             # 추가과제 포함
  python tests/run_golden_week04.py --embedding --tier all # 전부 실행
"""

from __future__ import annotations

import json
import sys
import tempfile
import os
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("KANANA_USE_LLM", "0")

GOLDEN_FILE = Path(__file__).parent / "golden_week04.json"


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
    """result dict를 expect 명세와 대조한다."""

    reasons: list[str] = []

    if "ok" in expect and result.get("ok") != expect["ok"]:
        reasons.append(f"ok={result.get('ok')} (기대 {expect['ok']})")

    if "has_field" in expect and expect["has_field"] not in result:
        reasons.append(f"'{expect['has_field']}' 필드 없음 (있는 것: {list(result.keys())})")

    if "rows_min_count" in expect:
        rows = result.get("rows", [])
        if len(rows) < expect["rows_min_count"]:
            reasons.append(f"rows 개수 {len(rows)} < {expect['rows_min_count']}")

    if "rows_max_count" in expect:
        rows = result.get("rows", [])
        if len(rows) > expect["rows_max_count"]:
            reasons.append(f"rows 개수 {len(rows)} > {expect['rows_max_count']}")

    if "rows_contains_title" in expect:
        rows = result.get("rows", [])
        titles = [r.get("title") for r in rows]
        if expect["rows_contains_title"] not in titles:
            reasons.append(f"rows 에 title='{expect['rows_contains_title']}' 없음 (있는 것: {titles})")

    if "rows_empty" in expect and expect["rows_empty"]:
        rows = result.get("rows", [])
        if rows:
            reasons.append(f"rows 가 비어있지 않음: {rows}")

    if "hits_min_count" in expect:
        hits = result.get("hits", [])
        if len(hits) < expect["hits_min_count"]:
            reasons.append(f"hits 개수 {len(hits)} < {expect['hits_min_count']}")

    if "hit_has_keys" in expect:
        hits = result.get("hits", [])
        if hits:
            first_hit = hits[0]
            for key in expect["hit_has_keys"]:
                if key not in first_hit:
                    reasons.append(f"hit에 '{key}' 키 없음 (있는 것: {list(first_hit.keys())})")

    if "reference_has_id" in expect and expect["reference_has_id"]:
        reference = result.get("reference") or {}
        if not reference.get("reference_id"):
            reasons.append(f"reference.reference_id 없음 (reference: {reference})")

    if reasons:
        fail(case_id, " | ".join(reasons))
    else:
        ok(case_id)


# ── unit 테스트 실행 ──────────────────────────────────────

def run_unit(cases: list[dict], tier_filter: str, tmp_db: Path) -> None:
    import dataclasses
    import fixed.config as cfg_mod

    original_path = cfg_mod.CONFIG.app_db_path
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=tmp_db)

    try:
        from fixed.app_store import AppSQLiteStore
        from student_parts.week03_build_nanas_logbook import save_structured_request
        import student_parts.week04_retrieve_nanas_memory as week04mod
    except Exception as e:
        print(f"  ⚠️  week04 import 실패 ({e}) — unit 테스트 건너뜀")
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)
        return

    # 모듈 레벨 SQLITE_STORE를 임시 DB로 교체
    tmp_store = AppSQLiteStore(tmp_db)
    original_store = week04mod.SQLITE_STORE
    week04mod.SQLITE_STORE = tmp_store

    try:
        from student_parts.week04_retrieve_nanas_memory import search_saved_requests
    except Exception as e:
        print(f"  ⚠️  search_saved_requests import 실패 ({e}) — unit 테스트 건너뜀")
        week04mod.SQLITE_STORE = original_store
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)
        return

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]

        # pre_save: Week 3 save_structured_request로 DB에 데이터 삽입
        if "pre_save" in c:
            try:
                save_structured_request.invoke(c["pre_save"])
            except Exception as e:
                fail(case_id, f"pre_save 실패: {e}")
                continue

        inp = c.get("input", {})
        try:
            raw = search_saved_requests.invoke(inp)
            result = json.loads(raw)
        except Exception as e:
            fail(case_id, f"tool 실행 오류: {e}")
            continue

        check_expect(case_id, result, c["expect"])

    week04mod.SQLITE_STORE = original_store
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)


# ── embedding 테스트 실행 ─────────────────────────────────

def run_embedding(cases: list[dict], tier_filter: str, tmp_chroma: Path) -> None:
    import dataclasses
    import fixed.config as cfg_mod

    original_chroma = cfg_mod.CONFIG.chroma_dir
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=tmp_chroma)

    try:
        from fixed.reference_store import PersonalReferenceStore
        import student_parts.week04_retrieve_nanas_memory as week04mod
        from student_parts.week04_retrieve_nanas_memory import (
            add_personal_reference,
            search_personal_references,
        )
    except Exception as e:
        print(f"  ⚠️  week04 import 실패 ({e}) — embedding 테스트 건너뜀")
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=original_chroma)
        return

    # 모듈 레벨 REFERENCE_STORE를 임시 chroma 경로로 교체
    try:
        tmp_ref_store = PersonalReferenceStore(tmp_chroma)
        original_store = week04mod.REFERENCE_STORE
        week04mod.REFERENCE_STORE = tmp_ref_store
    except Exception as e:
        print(f"  ⚠️  PersonalReferenceStore 초기화 실패 ({e}) — embedding 테스트 건너뜀")
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=original_chroma)
        return

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]
        tool_name = c["tool"]

        # pre_add: 검색 전 참고자료 추가
        if "pre_add" in c:
            try:
                add_personal_reference.invoke(c["pre_add"])
            except RuntimeError as e:
                if "PROXY_TOKEN" in str(e):
                    fail(case_id, f"PROXY_TOKEN 없음 — embedding 불가: {e}")
                    continue
                fail(case_id, f"pre_add 실패: {e}")
                continue
            except Exception as e:
                fail(case_id, f"pre_add 실패: {e}")
                continue

        tool_fn = {"add_personal_reference": add_personal_reference,
                   "search_personal_references": search_personal_references}.get(tool_name)
        if tool_fn is None:
            fail(case_id, f"tool '{tool_name}' 을 찾지 못함")
            continue

        try:
            raw = tool_fn.invoke(c.get("input", {}))
            result = json.loads(raw)
        except RuntimeError as e:
            if "PROXY_TOKEN" in str(e):
                fail(case_id, f"PROXY_TOKEN 없음 — embedding 불가: {e}")
                continue
            fail(case_id, f"tool 실행 오류: {e}")
            continue
        except Exception as e:
            fail(case_id, f"tool 실행 오류: {e}")
            continue

        check_expect(case_id, result, c["expect"])

    week04mod.REFERENCE_STORE = original_store
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=original_chroma)


# ── agent 테스트 실행 ─────────────────────────────────────

def run_agent(cases: list[dict], tier_filter: str) -> None:
    try:
        from student_parts.week04_retrieve_nanas_memory import build_week04_agent
        from fixed.langchain_trace import extract_agent_events
    except Exception as e:
        print(f"  ⚠️  week04 import 실패 ({e}) — agent 테스트 건너뜀")
        return

    try:
        agent = build_week04_agent()
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
    parser.add_argument("--embedding", action="store_true", help="embedding 테스트 실행 (PROXY_TOKEN 필요)")
    parser.add_argument("--agent", action="store_true", help="agent 테스트도 실행 (LLM 필요)")
    parser.add_argument("--tier", choices=["main", "all"], default="main", help="main=메인과제만, all=추가과제 포함")
    args = parser.parse_args()

    data = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    tier_filter = args.tier

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_db = Path(tmp_dir) / "test_kanana.sqlite3"
        tmp_chroma = Path(tmp_dir) / "chroma"

        print(f"\n{'─'*55}")
        print(f"  📋 Week 4 골든 테스트  (tier={tier_filter}, embedding={'on' if args.embedding else 'off'}, agent={'on' if args.agent else 'off'})")
        print(f"{'─'*55}")

        print("\n[unit — SQLite LIKE 검색, API 불필요]")
        run_unit(data["unit"], tier_filter, tmp_db)

        if args.embedding:
            print("\n[embedding — ChromaDB + OpenAI embedding, PROXY_TOKEN 필요]")
            run_embedding(data["embedding"], tier_filter, tmp_chroma)

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
