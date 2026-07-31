"""Week 5 MCP wrapper 단위 테스트 (unittest, 결정적·기본 실행).

MCP 경계(call_mcp_tool_sync / call_external_tool_payload)를 stub으로 대체해 wrapper
로직만 결정적으로 검증한다 → subprocess·LLM·네트워크 없이 빠르게 돈다. 실제 MCP
왕복은 test_week05_mcp_integration(RUN_MCP_TESTS), agent 종단은 test_week05_agent_smoke
(RUN_LLM_TESTS)에서 명시적 opt-in으로만 실행한다(멘토 리뷰: 실행 구분을 명시적으로).

import 전에 CONFIG 경로를 임시로, 토큰을 비워 실제 data/·외부 호출을 격리한다.

실행: python -m unittest discover -s tests
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fixed.config as _cfg

_TMP = Path(tempfile.mkdtemp(prefix="week5_wrappers_"))
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,  # import 시 임베딩/외부 호출 방지
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

from fixed.app_store import AppSQLiteStore
from fixed.session_scope import conversation_session_scope
from fixed.store_base import now_iso
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES
import student_parts.week05_load_kanas_past_conversations as w5


class Week05WrapperArgs(unittest.TestCase):
    """thin wrapper가 올바른 MCP tool 이름/인자를 넘기고 결과를 그대로 전달하는지 (경계 stub)."""

    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._orig_sync = w5.call_mcp_tool_sync
        self._orig_payload = w5.call_external_tool_payload

        def fake_sync(name, args):
            self.calls.append((name, args))
            return json.dumps({"ok": True, "tool_name": name, "rows": []}, ensure_ascii=False)

        def fake_payload(name, args):
            self.calls.append((name, args))
            return {"ok": True, "tool_name": name, "rows": [{"sender": "철수", "content": "안녕", "created_at": "t"}]}

        w5.call_mcp_tool_sync = fake_sync
        w5.call_external_tool_payload = fake_payload

    def tearDown(self) -> None:
        w5.call_mcp_tool_sync = self._orig_sync
        w5.call_external_tool_payload = self._orig_payload

    def test_search_previous_conversations_passes_args_asis(self) -> None:
        out = json.loads(w5.search_previous_conversations.invoke({"query": "회의", "member_names": ["철수"], "limit": 3}))
        name, args = self.calls[-1]
        self.assertEqual(name, "search_previous_conversations")
        self.assertEqual(args, {"query": "회의", "member_names": ["철수"], "limit": 3})  # 중복 정규화 없이 그대로
        self.assertTrue(out["ok"])

    def test_extract_schedules_passes_args_asis(self) -> None:
        w5.extract_schedules_from_history.invoke({"member_names": ["철수"], "date_from": "2026-07-01", "date_to": "2026-07-31"})
        name, args = self.calls[-1]
        self.assertEqual(name, "extract_schedules_from_history")
        self.assertEqual(args, {"member_names": ["철수"], "date_from": "2026-07-01", "date_to": "2026-07-31"})

    def test_list_shared_passes_all_five_args(self) -> None:
        w5.list_shared_schedules.invoke({"member_names": ["철수"], "date_from": "2026-07-01", "limit": 10})
        name, args = self.calls[-1]
        self.assertEqual(name, "list_shared_schedules")
        self.assertEqual(set(args.keys()), {"member_names", "date_from", "date_to", "source_conversation_id", "limit"})
        self.assertEqual(args["member_names"], ["철수"])
        self.assertEqual(args["limit"], 10)

    def test_create_and_delete_pass_args(self) -> None:
        w5.create_shared_schedule.invoke({"member_name": "철수", "title": "회의", "date": "2026-07-10", "start_time": "10:00"})
        cname, cargs = self.calls[-1]
        self.assertEqual(cname, "create_shared_schedule")
        self.assertEqual(cargs["member_name"], "철수")
        self.assertEqual(cargs["title"], "회의")
        w5.delete_shared_schedule.invoke({"schedule_id": "s1"})
        dname, dargs = self.calls[-1]
        self.assertEqual(dname, "delete_shared_schedule")
        self.assertEqual(dargs["schedule_id"], "s1")

    def test_load_conversation_messages_wraps_payload_unchanged(self) -> None:
        out = json.loads(w5.load_conversation_messages.invoke({"conversation_id": "ext_cs"}))
        name, args = self.calls[-1]
        self.assertEqual(name, "load_conversation_messages")
        self.assertEqual(args, {"conversation_id": "ext_cs"})
        self.assertEqual(out["rows"][0]["sender"], "철수")  # sender/content 보존


class Week05CollectMerge(unittest.TestCase):
    """_collect_member_schedules가 내 일정 + 외부 일정을 같은 row 구조로 합치는지 (외부 경계 stub)."""

    def setUp(self) -> None:
        self._orig_payload = w5.call_external_tool_payload

        def fake_payload(name, args):
            return {
                "ok": True,
                "rows": [
                    {"member_name": "철수", "title": "QA 리뷰", "date": "2026-07-15", "start_time": "16:00", "end_time": "17:00", "notes": ""}
                ],
            }

        w5.call_external_tool_payload = fake_payload

    def tearDown(self) -> None:
        w5.call_external_tool_payload = self._orig_payload

    def test_merges_me_and_external_same_shape(self) -> None:
        my = [{"schedule_id": "sch_1", "title": "내 집중", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00", "attendees": []}]
        result = w5._collect_member_schedules(
            member_names=["철수"], date_from="2026-07-01", date_to="2026-07-31", personal_schedules=my
        )
        members = [r["member_name"] for r in result["rows"]]
        self.assertIn("나", members)
        self.assertIn("철수", members)
        required = {"member_name", "title", "date", "start_time", "end_time", "notes"}
        for row in result["rows"]:
            self.assertTrue(required.issubset(row.keys()), row)
            # 필드 유무만이 아니라 "요청한 기간 안"인지도 확인한다(멘토 리뷰).
            self.assertTrue("2026-07-01" <= row["date"] <= "2026-07-31", row)
        self.assertTrue(result["schedule_summary"])

    def test_my_rows_outside_requested_range_are_excluded(self) -> None:
        """요청 기간(7월) 밖 내 일정과 날짜 없는 일정은 rows에 들어오면 안 된다."""

        my = [
            {"schedule_id": "in", "title": "7월 일정", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
            {"schedule_id": "after", "title": "8월 일정", "date": "2026-08-05", "start_time": "10:00", "end_time": "11:00"},
            {"schedule_id": "before", "title": "6월 일정", "date": "2026-06-30", "start_time": "10:00", "end_time": "11:00"},
            {"schedule_id": "nodate", "title": "날짜 미정", "date": None, "start_time": None, "end_time": None},
        ]
        result = w5._collect_member_schedules(
            member_names=[], date_from="2026-07-01", date_to="2026-07-31", personal_schedules=my
        )
        titles = [row["title"] for row in result["rows"]]
        self.assertEqual(titles, ["7월 일정"], titles)

    def test_range_bounds_are_inclusive(self) -> None:
        """양끝 날짜(date_from/date_to)는 외부 store SQL과 같이 포함된다."""

        my = [
            {"schedule_id": "start", "title": "시작일", "date": "2026-07-01", "start_time": "09:00", "end_time": "10:00"},
            {"schedule_id": "end", "title": "종료일", "date": "2026-07-31", "start_time": "09:00", "end_time": "10:00"},
        ]
        result = w5._collect_member_schedules(
            member_names=[], date_from="2026-07-01", date_to="2026-07-31", personal_schedules=my
        )
        self.assertEqual({row["title"] for row in result["rows"]}, {"시작일", "종료일"})

    def test_no_members_skips_external_call(self) -> None:
        # 정규화 결과가 비면 외부 MCP 호출 없이 내 일정만 담긴다.
        result = w5._collect_member_schedules(member_names=[], date_from="2026-07-01", date_to="2026-07-31", personal_schedules=[])
        self.assertEqual(result["rows"], [])


class Week05PersonalScope(unittest.TestCase):
    """_personal_schedules_for_current_scope: SQLite 저장 + 현재 대화 임시 일정, id 중복 제거."""

    def test_sqlite_plus_scoped_temp_with_dedup(self) -> None:
        # _personal_schedules_for_current_scope는 w5 모듈이 바인딩한 CONFIG.app_db_path를 읽는다.
        # (discover에서 다른 테스트가 fixed.config.CONFIG를 재할당해도) 함수가 읽는 바로 그 경로에 저장한다.
        store = AppSQLiteStore(w5.CONFIG.app_db_path)
        # 외부 동기화(subprocess)를 타지 않도록 schedules 테이블에 직접 저장한다.
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO schedules (schedule_id, request_id, owner, title, date, start_time, end_time, attendees_json, source, created_at)"
                " VALUES (?, ?, 'me', ?, ?, ?, ?, '[]', 'structured_output', ?)",
                ("sch_dup", "req_x", "저장된 일정", "2026-07-10", "10:00", "11:00", now_iso()),
            )
        PERSONAL_SCHEDULES.clear()
        with conversation_session_scope("conv_now"):
            PERSONAL_SCHEDULES.append({"id": "sch_dup", "title": "중복(무시돼야)", "session_id": "conv_now"})
            PERSONAL_SCHEDULES.append({"id": "sch_new", "title": "새 임시", "session_id": "conv_now"})
            PERSONAL_SCHEDULES.append({"id": "sch_other", "title": "다른 대화", "session_id": "conv_other"})
            rows = w5._personal_schedules_for_current_scope()
        titles = [r.get("title") for r in rows]
        self.assertIn("저장된 일정", titles)      # SQLite 저장분 포함
        self.assertIn("새 임시", titles)          # 현재 대화의 새 임시 일정 포함
        self.assertNotIn("중복(무시돼야)", titles)  # SQLite와 id 중복 → 제외
        self.assertNotIn("다른 대화", titles)      # 다른 대화 범위 → 제외
        PERSONAL_SCHEDULES.clear()


class Week05PromptHygiene(unittest.TestCase):
    """멘토 리뷰: 최종 조합 프롬프트의 단일 정체성 / 무 'Week N' 라벨 / 무 모순."""

    def test_week05_combined_prompt_is_clean(self) -> None:
        prompt = w5.week05_system_prompt()
        identity = [ln for ln in prompt.splitlines() if re.search(r"너는 .{0,40}?(agent|에이전트|비서)", ln)]
        self.assertEqual(len(identity), 1, f"정체성 문장 {len(identity)}개 → {identity}")
        self.assertIsNone(re.search(r"Week\s*\d", prompt), "'Week N' 라벨 노출")
        self.assertNotIn("아직 하지 않는다", prompt, "커리큘럼 범위 부정 잔존")


if __name__ == "__main__":
    unittest.main()
