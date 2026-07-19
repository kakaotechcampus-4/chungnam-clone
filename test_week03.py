import json
from pathlib import Path

import pytest

from fixed.app_store import AppSQLiteStore
import student_parts.week03_build_nanas_logbook as w3


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """실제 앱 DB 대신 임시 파일 DB를 쓰도록 _store()를 교체한다."""
    store = AppSQLiteStore(tmp_path / "test_app.sqlite3")
    monkeypatch.setattr(w3, "_store", lambda: store)
    return store


def test_week03_tools_excludes_legacy():
    """겹치던 Week 1 tool은 빠지고 SQLite 조회 tool만 남아야 한다."""
    names = {t.name for t in w3.week03_tools()}
    assert "personal_list_saved_schedules" in names
    assert "personal_list_schedules" not in names
    assert "personal_delete_schedule" not in names


def test_save_then_list_roundtrip(temp_store):
    """저장한 일정이 조회로 다시 나와야 한다."""
    w3.save_structured_request.invoke({
        "kind": "personal_schedule",
        "title": "개인 코칭",
        "date": "2026-07-16",
        "start_time": "10:00",
        "original_text": "내일 10시 개인 코칭 저장해줘",
    })

    result = json.loads(w3.personal_list_saved_schedules.invoke({}))
    titles = [s["title"] for s in result["schedules"]]
    assert "개인 코칭" in titles