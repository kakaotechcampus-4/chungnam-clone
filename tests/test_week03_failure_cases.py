"""Week 3 실패 케이스 회귀 테스트 (unittest).

정상 동작이 아니라 "중간에 한 단계만 실패하는 경우"를 고정합니다.
model/prompt를 바꿔도 중요 동작이 깨지는지 빠르게 확인합니다.

실행:
  python -m unittest discover -s tests
  python -m unittest tests.test_week03_failure_cases

LLM 없이 tool 레이어만 검증합니다. 실제 앱 데이터(data/)를 건드리지 않도록,
student_parts를 import하기 "전에" CONFIG의 저장소 경로(app/external 등)를 임시
디렉터리로 돌립니다.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# import 전에 CONFIG를 임시 경로로 돌려 실제 data/ (app DB·외부 공유 DB) 오염 방지
import fixed.config as _cfg

_TMP = Path(tempfile.mkdtemp(prefix="week3_failure_"))
# 논리적 격리: 결정적 테스트이므로 경로를 임시로 돌리는 것에 더해 토큰도 비워 import 시
# 외부 호출(임베딩 등)이 아예 없게 한다. 실제 토큰이 필요한 스모크 테스트는 자기
# setUpClass에서 실제 토큰 config를 스스로 구성하므로 여기서 비워도 안전하다.
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

from fixed.app_store import AppSQLiteStore
import student_parts.week03_build_nanas_logbook as w3
from student_parts.week01_wake_up_nana import PERSONAL_SCHEDULES


class Week03FailureCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = AppSQLiteStore(_TMP / "test_app.sqlite3")
        w3._store = lambda: cls.store  # 모든 tool이 임시 DB를 쓰게 해 실제 DB 보호

    def test_a_partial_failure_rollback(self) -> None:
        """SQLite 저장 실패 시 임시 일정을 보상 삭제하고, 재시도해도 유령이 안 쌓인다."""

        class FailingStore:
            def save_structured_request(self, payload):
                raise RuntimeError("모의 SQLite 장애")

        original = w3._store
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
            w3._store = original

        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertIn("모의 SQLite 장애", result["error"])
        self.assertEqual([s["title"] for s in PERSONAL_SCHEDULES].count("부분 실패 테스트"), 0)
        self.assertFalse(retry["ok"])
        self.assertTrue(retry["rolled_back"])

    def test_b_delete_all_with_filter(self) -> None:
        """delete_all=True와 날짜 필터가 함께 오면 명시 필터가 우선(전체 삭제 방지)."""

        w3.save_structured_request_payload(
            {"kind": "personal_schedule", "title": "지울일정B", "date": "2026-08-05", "start_time": "10:00"},
            store=self.store,
        )
        w3.save_structured_request_payload(
            {"kind": "personal_schedule", "title": "남을일정B", "date": "2026-08-06", "start_time": "10:00"},
            store=self.store,
        )
        result = json.loads(w3.personal_delete_saved_schedules.invoke({"delete_all": True, "date": "2026-08-05"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted_count"], 1)
        remaining = [s["title"] for s in self.store.list_schedules(limit=100)]
        self.assertIn("남을일정B", remaining)
        self.assertNotIn("지울일정B", remaining)

    def test_c_kind_unspecified_list(self) -> None:
        """kind 미지정이면 개인+그룹 모두, kind 지정 시 해당 종류로 축소."""

        w3.save_structured_request_payload(
            {"kind": "personal_schedule", "title": "개인조회C", "date": "2026-08-11", "start_time": "10:00"},
            store=self.store,
        )
        w3.save_structured_request_payload(
            {"kind": "group_schedule", "title": "그룹조회C", "date": "2026-08-12", "members": ["철수"]},
            store=self.store,
        )
        default_titles = [s["title"] for s in json.loads(w3.personal_list_saved_schedules.invoke({}))["schedules"]]
        self.assertIn("개인조회C", default_titles)
        self.assertIn("그룹조회C", default_titles)
        personal_titles = [
            s["title"]
            for s in json.loads(w3.personal_list_saved_schedules.invoke({"kind": "personal_schedule"}))["schedules"]
        ]
        self.assertIn("개인조회C", personal_titles)
        self.assertNotIn("그룹조회C", personal_titles)
        group_titles = [
            s["title"]
            for s in json.loads(w3.personal_list_saved_schedules.invoke({"kind": "group_schedule"}))["schedules"]
        ]
        self.assertIn("그룹조회C", group_titles)
        self.assertNotIn("개인조회C", group_titles)

    def test_d_no_condition_delete_guard(self) -> None:
        """조건이 하나도 없는 삭제 요청은 거부되어야 한다."""

        refused = json.loads(w3.personal_delete_saved_schedules.invoke({}))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["deleted_count"], 0)


if __name__ == "__main__":
    unittest.main()
