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

    if "has_keys" in expect:
        for key in expect["has_keys"]:
            if key not in result:
                reasons.append(f"'{key}' 키 없음 (있는 것: {list(result.keys())})")

    if "hits_is_list" in expect and expect["hits_is_list"]:
        if not isinstance(result.get("hits"), list):
            reasons.append(f"hits가 list가 아님: {type(result.get('hits'))}")

    if "rows_is_list" in expect and expect["rows_is_list"]:
        if not isinstance(result.get("rows"), list):
            reasons.append(f"rows가 list가 아님: {type(result.get('rows'))}")

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
    original_sqlite_store = week04mod.SQLITE_STORE
    week04mod.SQLITE_STORE = tmp_store

    # CONVERSATION_RAG_STORE를 임시 chroma 경로로 교체 (빈 컬렉션, embedding API 미호출)
    from fixed.conversation_rag_store import ConversationRAGStore
    tmp_chroma = Path(tmp_db).parent / "chroma_unit"
    tmp_conv_store = ConversationRAGStore(tmp_chroma)
    original_conv_store = week04mod.CONVERSATION_RAG_STORE
    week04mod.CONVERSATION_RAG_STORE = tmp_conv_store

    try:
        from student_parts.week04_retrieve_nanas_memory import (
            search_saved_requests,
            search_conversation_messages,
        )
    except Exception as e:
        print(f"  ⚠️  week04 tool import 실패 ({e}) — unit 테스트 건너뜀")
        week04mod.SQLITE_STORE = original_sqlite_store
        week04mod.CONVERSATION_RAG_STORE = original_conv_store
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)
        return

    TOOL_MAP = {
        "search_saved_requests": search_saved_requests,
        "search_conversation_messages": search_conversation_messages,
    }

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]
        tool_fn = TOOL_MAP.get(c["tool"])
        if tool_fn is None:
            fail(case_id, f"tool '{c['tool']}' 을 찾지 못함")
            continue

        # pre_save: Week 3 save_structured_request로 DB에 데이터 삽입
        if "pre_save" in c:
            try:
                save_structured_request.invoke(c["pre_save"])
            except Exception as e:
                fail(case_id, f"pre_save 실패: {e}")
                continue

        inp = c.get("input", {})
        try:
            raw = tool_fn.invoke(inp)
            result = json.loads(raw)
        except Exception as e:
            fail(case_id, f"tool 실행 오류: {e}")
            continue

        check_expect(case_id, result, c["expect"])

    week04mod.SQLITE_STORE = original_sqlite_store
    week04mod.CONVERSATION_RAG_STORE = original_conv_store
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, app_db_path=original_path)


# ── embedding 테스트 실행 ─────────────────────────────────

def run_embedding(cases: list[dict], tier_filter: str, tmp_chroma: Path) -> None:
    import dataclasses
    import fixed.config as cfg_mod

    original_chroma = cfg_mod.CONFIG.chroma_dir
    original_db = cfg_mod.CONFIG.app_db_path

    # embedding 테스트 전용 임시 DB + chroma
    tmp_emb_dir = tmp_chroma.parent / "emb_tmp"
    tmp_emb_dir.mkdir(parents=True, exist_ok=True)
    tmp_emb_db = tmp_emb_dir / "emb_test.sqlite3"
    tmp_emb_chroma = tmp_emb_dir / "chroma"

    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=tmp_emb_chroma, app_db_path=tmp_emb_db)

    try:
        from fixed.app_store import AppSQLiteStore
        from fixed.reference_store import PersonalReferenceStore
        from fixed.conversation_rag_store import ConversationRAGStore
        from fixed.session_scope import conversation_session_scope
        import student_parts.week04_retrieve_nanas_memory as week04mod
        from student_parts.week04_retrieve_nanas_memory import (
            add_personal_reference,
            search_personal_references,
            search_conversation_messages,
        )
    except Exception as e:
        print(f"  ⚠️  week04 import 실패 ({e}) — embedding 테스트 건너뜀")
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=original_chroma, app_db_path=original_db)
        return

    # 모듈 레벨 store를 임시 경로로 교체
    try:
        tmp_ref_store = PersonalReferenceStore(tmp_emb_chroma)
        tmp_sqlite_store = AppSQLiteStore(tmp_emb_db)
        tmp_conv_store = ConversationRAGStore(tmp_emb_chroma)
    except Exception as e:
        print(f"  ⚠️  store 초기화 실패 ({e}) — embedding 테스트 건너뜀")
        cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=original_chroma, app_db_path=original_db)
        return

    original_ref = week04mod.REFERENCE_STORE
    original_sqlite = week04mod.SQLITE_STORE
    original_conv = week04mod.CONVERSATION_RAG_STORE
    week04mod.REFERENCE_STORE = tmp_ref_store
    week04mod.SQLITE_STORE = tmp_sqlite_store
    week04mod.CONVERSATION_RAG_STORE = tmp_conv_store

    TOOL_MAP = {
        "add_personal_reference": add_personal_reference,
        "search_personal_references": search_personal_references,
        "search_conversation_messages": search_conversation_messages,
    }

    for c in cases:
        if tier_filter == "main" and c.get("tier") != "main":
            continue

        case_id = c["id"]
        tool_name = c["tool"]
        pre_conversation_id: str | None = None

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

        # pre_conversation: SQLite에 대화 데이터 삽입
        if "pre_conversation" in c:
            try:
                pc = c["pre_conversation"]
                conv = tmp_sqlite_store.create_conversation(title=pc.get("title", "테스트 대화"))
                pre_conversation_id = conv["conversation_id"]
                for msg in pc.get("messages", []):
                    tmp_sqlite_store.append_message(pre_conversation_id, msg["role"], msg["content"])
            except Exception as e:
                fail(case_id, f"pre_conversation 삽입 실패: {e}")
                continue

        tool_fn = TOOL_MAP.get(tool_name)
        if tool_fn is None:
            fail(case_id, f"tool '{tool_name}' 을 찾지 못함")
            continue

        # set_current_session_to_pre_conversation: 현재 세션을 pre_conversation ID로 설정
        # → tool이 conversation_id 없이 호출돼도 pre_conversation이 자동 제외되어야 함
        def _run_tool(inp: dict) -> dict:
            raw = tool_fn.invoke(inp)
            return json.loads(raw)

        try:
            if c.get("set_current_session_to_pre_conversation") and pre_conversation_id:
                with conversation_session_scope(pre_conversation_id):
                    result = _run_tool(c.get("input", {}))
            else:
                result = _run_tool(c.get("input", {}))
        except RuntimeError as e:
            if "PROXY_TOKEN" in str(e):
                fail(case_id, f"PROXY_TOKEN 없음 — embedding 불가: {e}")
                continue
            fail(case_id, f"tool 실행 오류: {e}")
            continue
        except Exception as e:
            fail(case_id, f"tool 실행 오류: {e}")
            continue

        # hits_exclude_pre_conversation_id: pre_conversation의 conversation_id가 hits에 없어야 함
        expect = c["expect"]
        if expect.get("hits_exclude_pre_conversation_id") and pre_conversation_id:
            hits = result.get("hits", [])
            included = [h for h in hits if h.get("conversation_id") == pre_conversation_id]
            if included:
                fail(case_id, f"현재 대화({pre_conversation_id})가 hits에 포함됨 — 제외되어야 함")
                continue

        check_expect(case_id, result, expect)

    week04mod.REFERENCE_STORE = original_ref
    week04mod.SQLITE_STORE = original_sqlite
    week04mod.CONVERSATION_RAG_STORE = original_conv
    cfg_mod.CONFIG = dataclasses.replace(cfg_mod.CONFIG, chroma_dir=original_chroma, app_db_path=original_db)


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
