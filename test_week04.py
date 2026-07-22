import json

import pytest

from fixed.app_store import AppSQLiteStore
import student_parts.week03_build_nanas_logbook as w3
import student_parts.week04_retrieve_nanas_memory as w4


class FakeReferenceStore:
    """PersonalReferenceStore의 반환 모양만 흉내내는 테스트용 대역이다."""

    def __init__(self):
        self.items = []

    def backend_info(self):
        return {"vector_store": "chromadb", "embedding_provider": "openai"}

    def add_personal_reference(self, title, content, tags=None):
        saved = {
            "reference_id": f"ref_{len(self.items) + 1}",
            "title": title,
            "content": content,
            "tags": tags or [],
            "backend": self.backend_info(),
        }
        self.items.append(saved)
        return saved

    def search_personal_references(self, query, limit=3):
        return [
            {
                "id": item["reference_id"],
                "title": item["title"],
                "content": item["content"],
                "tags": ",".join(item["tags"]),
                "distance": 0.3,
            }
            for item in self.items
            if query in item["content"] or query in item["title"]
        ][:limit]


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """실제 앱 DB 대신 임시 파일 DB를 Week 3와 Week 4가 함께 쓰도록 교체한다."""
    store = AppSQLiteStore(tmp_path / "test_app.sqlite3")
    monkeypatch.setattr(w3, "_store", lambda: store)
    monkeypatch.setattr(w4, "SQLITE_STORE", store)
    return store


@pytest.fixture
def fake_reference(monkeypatch):
    """embedding API를 타지 않도록 참고자료 저장소를 대역으로 바꾼다."""
    store = FakeReferenceStore()
    monkeypatch.setattr(w4, "REFERENCE_STORE", store)
    return store


def test_registered_tools_return_string():
    """미구현 tool이 목록에 남아 있으면 agent가 깨진다."""
    assert "search_conversation_messages" not in {t.name for t in w4.week04_tools()}


def test_add_then_search_reference_roundtrip(fake_reference):
    """저장한 참고자료가 검색으로 다시 나와야 한다."""
    w4.add_personal_reference.invoke({
        "title": "회의 선호",
        "content": "회의는 목요일 오후에 잡는 걸 선호한다.",
        "tags": ["preference"],
    })

    result = json.loads(w4.search_personal_references.invoke({"query": "목요일"}))
    contents = [hit["content"] for hit in result["hits"]]
    assert "회의는 목요일 오후에 잡는 걸 선호한다." in contents


def test_reference_hit_has_contract_keys(fake_reference):
    """hit은 id/content/distance/metadata 구조여야 근거로 쓸 수 있다."""
    w4.add_personal_reference.invoke({
        "title": "점심 보호", "content": "점심 시간은 비워둔다.", "tags": ["lunch"],
    })

    hits = json.loads(w4.search_personal_references.invoke({"query": "점심"}))["hits"]
    assert set(hits[0]) == {"id", "content", "distance", "metadata"}
    assert set(hits[0]["metadata"]) == {"title", "tags"}


def test_save_then_search_saved_requests(temp_store):
    """Week 3에서 저장한 기록이 Week 4 검색으로 나와야 한다."""
    w3.save_structured_request.invoke({
        "kind": "todo",
        "title": "보고서 제출",
        "date": "2026-07-25",
        "original_text": "금요일까지 보고서 제출 할 일 저장해줘",
    })

    result = json.loads(w4.search_saved_requests.invoke({"query": "보고서"}))
    assert result["rows"], "store.search_saved_requests의 두 번째 인자는 kind다. limit=으로 넘겨야 한다."
    assert result["rows"][0]["title"] == "보고서 제출"