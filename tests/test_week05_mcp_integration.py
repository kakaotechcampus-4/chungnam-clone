"""Week 5 MCP 통합 테스트 (unittest, 결정적이지만 실제 MCP subprocess 사용).

wrapper가 실제 MCP 서버(mcp_server/sqlite_mcp_server.py)와 올바르게 왕복하는지
검증한다. LLM은 필요 없지만 stdio subprocess를 띄우고 외부 SQLite에 접근하므로,
빠른 기본 코드 테스트(test_week05_wrappers)와 분리해 명시적 opt-in으로만 실행한다.

실행:
  RUN_MCP_TESTS=1 python -m unittest tests.test_week05_mcp_integration
  RUN_MCP_TESTS=1 python -m unittest discover -s tests
(켜지 않으면 기본 discover에서 skip되고 subprocess도 뜨지 않는다.)

격리: 모듈 import 전 CONFIG 경로를 임시로·토큰을 비우고, opt-in 시 setUpClass에서
KANANA_EXTERNAL_DB_PATH를 임시 외부 DB로 지정한다 → 실제 data/를 건드리지 않는다.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_RUN_MCP = os.getenv("RUN_MCP_TESTS") == "1"
_TMP = Path(tempfile.mkdtemp(prefix="week5_mcp_it_"))

import fixed.config as _cfg

_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

from fixed.app_store import AppSQLiteStore
from fixed.store_base import now_iso
import student_parts.week05_load_kanas_past_conversations as w5


@unittest.skipUnless(_RUN_MCP, "실제 MCP subprocess 통합 테스트: RUN_MCP_TESTS=1 로 명시적으로 켜세요.")
class Week05McpIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # MCP subprocess가 임시 외부 DB를 쓰도록 opt-in 시에만 env 지정(전역 side-effect 최소화).
        os.environ["KANANA_EXTERNAL_DB_PATH"] = str(_TMP / "external.sqlite3")

    def test_list_shared_returns_seeded_rows(self) -> None:
        res = json.loads(w5.list_shared_schedules.invoke({}))
        self.assertTrue(res["ok"])
        self.assertTrue(res["rows"], "기본 공유 일정 seed rows가 있어야 함")
        self.assertTrue(res.get("schedule_summary"))
        required = {"member_name", "title", "date", "start_time", "end_time", "schedule_id"}
        self.assertTrue(required.issubset(res["rows"][0].keys()), res["rows"][0])

    def test_extract_schedules_fields(self) -> None:
        res = json.loads(
            w5.extract_schedules_from_history.invoke(
                {"member_names": ["철수"], "date_from": "2026-07-01", "date_to": "2026-07-31"}
            )
        )
        self.assertTrue(res["ok"] and res["rows"])
        required = {"member_name", "title", "date", "start_time", "end_time", "notes"}
        for row in res["rows"]:
            self.assertTrue(required.issubset(row.keys()), row)
            self.assertEqual(row["member_name"], "철수")

    def test_create_list_delete_roundtrip(self) -> None:
        created = json.loads(
            w5.create_shared_schedule.invoke(
                {
                    "member_name": "통합테스트멤버",
                    "title": "IT 회의",
                    "date": "2026-08-03",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "schedule_id": "sch_it_roundtrip",
                    "source_conversation_id": "it_src_roundtrip",
                }
            )
        )
        self.assertTrue(created["ok"])
        listed = json.loads(w5.list_shared_schedules.invoke({"member_names": ["통합테스트멤버"]}))
        titles = [r.get("title") for r in listed["rows"]]
        self.assertIn("IT 회의", titles)
        deleted = json.loads(w5.delete_shared_schedule.invoke({"schedule_id": "sch_it_roundtrip"}))
        self.assertTrue(deleted["ok"])
        self.assertGreaterEqual(deleted["deleted_count"], 1)
        listed_after = json.loads(w5.list_shared_schedules.invoke({"member_names": ["통합테스트멤버"]}))
        self.assertNotIn("IT 회의", [r.get("title") for r in listed_after["rows"]])

    def test_collect_includes_me_and_external(self) -> None:
        # 내(나) 일정 하나를 임시 앱 DB에 직접 저장(외부 동기화 subprocess 회피).
        store = AppSQLiteStore(_cfg.CONFIG.app_db_path)
        with store.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO schedules (schedule_id, request_id, owner, title, date, start_time, end_time, attendees_json, source, created_at)"
                " VALUES (?, ?, 'me', ?, ?, ?, ?, '[]', 'structured_output', ?)",
                ("sch_me_collect", "req_me", "내 집중 작업", "2026-07-08", "09:00", "10:00", now_iso()),
            )
        res = json.loads(
            w5.collect_member_schedules.invoke(
                {"member_names": ["철수"], "date_from": "2026-07-01", "date_to": "2026-07-31"}
            )
        )
        self.assertTrue(res["ok"] and res.get("schedule_summary"))
        members = {r["member_name"] for r in res["rows"]}
        self.assertIn("나", members)
        self.assertIn("철수", members)
        self.assertIn("내 집중 작업", [r.get("title") for r in res["rows"]])


if __name__ == "__main__":
    unittest.main()
