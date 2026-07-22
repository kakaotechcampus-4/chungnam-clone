"""Week 4 실패/엣지 케이스 회귀 테스트.

정상 흐름이 아니라 계약이 깨지기 쉬운 지점을 고정합니다. model/prompt를 바꿔도
중요 동작(top-level 키, hit 재구성, 현재 대화 제외)이 유지되는지 빠르게 확인합니다.

실행: 저장소 루트에서  python tests/week04_failure_cases.py
임베딩/LLM에 의존하는 스토어 메서드는 stub으로 대체해 테스트 로직은 네트워크를 타지 않습니다.
(모듈 import 시 실제 참고자료 스토어 seed가 1회 일어날 수 있으나 테스트 결과에는 영향 없음)
임시 SQLite와 stub store를 쓰므로 실제 앱 데이터(data/)는 건드리지 않습니다.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixed.app_store import AppSQLiteStore
from fixed.session_scope import conversation_session_scope
import student_parts.week04_retrieve_nanas_memory as w4
import student_parts.week03_build_nanas_logbook as w3


class FakeReferenceStore:
    """임베딩 없이 참고자료 스토어를 흉내 냅니다."""

    def __init__(self) -> None:
        self.last_add: dict | None = None

    def add_personal_reference(self, title, content, tags=None):
        self.last_add = {"title": title, "content": content, "tags": tags}
        return {"reference_id": "ref_fake", "title": title, "content": content, "tags": tags, "backend": {"vector_store": "fake"}}

    def search_personal_references(self, query, limit=3):
        # 실제 스토어는 flat hit(metadata 중첩 아님, tags는 콤마 문자열)을 반환한다.
        return [{"id": "ref_1", "title": "점심 보호", "content": "점심시간 비움", "tags": "preference,lunch", "distance": 0.1}]

    def backend_info(self):
        return {"vector_store": "fake"}


class FakeConvStore:
    """임베딩 없이 대화 RAG 스토어를 흉내 내고, search 인자를 기록합니다."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def sync_from_sqlite(self, sqlite_store):
        return {"upserted": 0, "skipped": 0, "deleted": 0, "total": 0}

    def search(self, *, query, top_k=5, exclude_conversation_id=None, conversation_id=None):
        self.calls.append({"exclude": exclude_conversation_id, "conversation_id": conversation_id, "top_k": top_k})
        return []

    def context_from_hits(self, hits):
        return "ctx"

    def backend_info(self):
        return {"vector_store": "fake_conv"}


tmp = Path(tempfile.mkdtemp(prefix="week4_failure_"))
sqlite_store = AppSQLiteStore(tmp / "app.sqlite3")
fake_ref = FakeReferenceStore()
fake_conv = FakeConvStore()
w4.REFERENCE_STORE = fake_ref
w4.SQLITE_STORE = sqlite_store
w4.CONVERSATION_RAG_STORE = fake_conv
w3._store = lambda: sqlite_store


def case_a_saved_requests_rows_contract() -> None:
    """A. search_saved_requests는 top-level rows를 주고, 결과 없으면 []."""
    empty = json.loads(w4.search_saved_requests.invoke({"query": "존재하지않는검색어zzz", "top_k": 5}))
    assert "rows" in empty and empty["rows"] == [], empty
    w3.save_structured_request_payload({"kind": "todo", "title": "우유 사기"}, store=sqlite_store)
    hit = json.loads(w4.search_saved_requests.invoke({"query": "우유", "top_k": 5}))
    assert any(r.get("title") == "우유 사기" for r in hit["rows"]), hit
    print("A. search_saved_requests top-level rows / 빈 결과 [] OK")


def case_b_personal_ref_hit_shape() -> None:
    """B. flat 스토어 hit을 {id, content, distance, metadata:{title, tags}}로 재구성."""
    res = json.loads(w4.search_personal_references.invoke({"query": "점심", "top_k": 3}))
    assert "hits" in res and res["hits"], res
    hit = res["hits"][0]
    assert set(hit.keys()) == {"id", "content", "distance", "metadata"}, hit
    assert set(hit["metadata"].keys()) == {"title", "tags"}, hit["metadata"]
    assert hit["metadata"]["title"] == "점심 보호", hit
    print("B. search_personal_references hit 재구성(metadata{title,tags}) OK")


def case_c_add_tags_none() -> None:
    """C. tags 미지정이면 스토어에 빈 list로 전달, reference_backend/reference 포함."""
    res = json.loads(w4.add_personal_reference.invoke({"title": "제목", "content": "내용"}))
    assert fake_ref.last_add["tags"] == [], fake_ref.last_add
    assert "reference_backend" in res and "reference" in res, res
    print("C. add_personal_reference tags None→[] + payload OK")


def case_d_empty_conversation_id_normalized() -> None:
    """D. (핵심 회귀) conversation_id=""도 '미지정'으로 정규화되어 현재 대화가 제외돼야 한다."""
    fake_conv.calls.clear()
    with conversation_session_scope("conv_current"):
        w4.search_conversation_messages.invoke({"query": "질문", "conversation_id": ""})
    call = fake_conv.calls[-1]
    assert call["conversation_id"] is None, call            # 빈 문자열 → None 정규화
    assert call["exclude"] == "conv_current", call            # 현재 대화 제외 유지
    print("D. 빈 문자열 conversation_id 정규화 + 현재 대화 제외 OK")


def case_e_conversation_id_variants() -> None:
    """E. None → 현재 대화 제외 / 명시 id → 그 대화로 한정(제외 없음)."""
    fake_conv.calls.clear()
    with conversation_session_scope("conv_current"):
        w4.search_conversation_messages.invoke({"query": "질문"})                              # None
        w4.search_conversation_messages.invoke({"query": "질문", "conversation_id": "conv_x"})  # 명시
    none_call, explicit_call = fake_conv.calls[-2], fake_conv.calls[-1]
    assert none_call["conversation_id"] is None and none_call["exclude"] == "conv_current", none_call
    assert explicit_call["conversation_id"] == "conv_x" and explicit_call["exclude"] is None, explicit_call
    print("E. conversation_id None/명시 분기 OK")


def case_f_conversation_return_shape() -> None:
    """F. hits==rows, context/rag_backend/sync 포함."""
    res = json.loads(w4.search_conversation_messages.invoke({"query": "질문"}))
    for key in ("hits", "rows", "context", "rag_backend", "sync"):
        assert key in res, (key, list(res.keys()))
    assert res["hits"] == res["rows"], res
    print("F. conversation 반환 shape(hits==rows + context/rag_backend/sync) OK")


if __name__ == "__main__":
    case_a_saved_requests_rows_contract()
    case_b_personal_ref_hit_shape()
    case_c_add_tags_none()
    case_d_empty_conversation_id_normalized()
    case_e_conversation_id_variants()
    case_f_conversation_return_shape()
    print("\nALL WEEK4 FAILURE-CASE TESTS PASSED")
