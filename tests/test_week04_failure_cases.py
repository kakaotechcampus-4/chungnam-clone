"""Week 4 실패/엣지 케이스 회귀 테스트 (unittest).

계약이 깨지기 쉬운 지점을 고정합니다. model/prompt를 바꿔도 중요 동작
(top-level 키, hit 재구성, 현재 대화 제외)이 유지되는지 확인합니다.

실행:
  python -m unittest discover -s tests
  python -m unittest tests.test_week04_failure_cases

이 파일은 결정적 테스트라 논리적 격리를 위해 모듈 import "전에" CONFIG의 저장소 경로를
임시 디렉터리로 돌리고 토큰도 비웁니다(아래). 그래서 (1) import 중 만들어지는 파일까지
임시 폴더에만 생겨 실제 data/를 건드리지 않고, (2) 토큰이 비어 참고자료 초기화 등
외부 임베딩 호출도 일어나지 않습니다. 실제 LLM 검증은 test_week04_agent_smoke가 자기
설정을 따로 만들어 수행합니다.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# week04는 import 시점에 전역 store를 만들기 때문에, import "전에" CONFIG 경로를 임시로 돌린다.
import fixed.config as _cfg

_TMP = Path(tempfile.mkdtemp(prefix="week4_failure_"))
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


class Week04FailureCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sqlite_store = AppSQLiteStore(_TMP / "test_app.sqlite3")
        cls.fake_ref = FakeReferenceStore()
        cls.fake_conv = FakeConvStore()
        w4.REFERENCE_STORE = cls.fake_ref
        w4.SQLITE_STORE = cls.sqlite_store
        w4.CONVERSATION_RAG_STORE = cls.fake_conv
        w3._store = lambda: cls.sqlite_store

    def test_a_saved_requests_rows_contract(self) -> None:
        """search_saved_requests는 top-level rows를 주고, 결과 없으면 []."""
        empty = json.loads(w4.search_saved_requests.invoke({"query": "존재하지않는검색어zzz", "top_k": 5}))
        self.assertEqual(empty["rows"], [])
        w3.save_structured_request_payload({"kind": "todo", "title": "우유 사기"}, store=self.sqlite_store)
        hit = json.loads(w4.search_saved_requests.invoke({"query": "우유", "top_k": 5}))
        self.assertTrue(any(r.get("title") == "우유 사기" for r in hit["rows"]))

    def test_b_personal_ref_hit_shape(self) -> None:
        """flat 스토어 hit을 {id, content, distance, metadata:{title, tags}}로 재구성."""
        res = json.loads(w4.search_personal_references.invoke({"query": "점심", "top_k": 3}))
        self.assertTrue(res["hits"])
        hit = res["hits"][0]
        self.assertEqual(set(hit.keys()), {"id", "content", "distance", "metadata"})
        self.assertEqual(set(hit["metadata"].keys()), {"title", "tags"})
        self.assertEqual(hit["metadata"]["title"], "점심 보호")

    def test_c_add_tags_none(self) -> None:
        """tags 미지정이면 스토어에 빈 list로 전달, reference_backend/reference 포함."""
        res = json.loads(w4.add_personal_reference.invoke({"title": "제목", "content": "내용"}))
        self.assertEqual(self.fake_ref.last_add["tags"], [])
        self.assertIn("reference_backend", res)
        self.assertIn("reference", res)

    def test_d_empty_conversation_id_normalized(self) -> None:
        """(핵심 회귀) conversation_id=""도 '미지정'으로 정규화되어 현재 대화가 제외돼야 한다."""
        self.fake_conv.calls.clear()
        with conversation_session_scope("conv_current"):
            w4.search_conversation_messages.invoke({"query": "질문", "conversation_id": ""})
        call = self.fake_conv.calls[-1]
        self.assertIsNone(call["conversation_id"])       # 빈 문자열 → None 정규화
        self.assertEqual(call["exclude"], "conv_current")  # 현재 대화 제외 유지

    def test_e_conversation_id_variants(self) -> None:
        """None → 현재 대화 제외 / 명시 id → 그 대화로 한정(제외 없음)."""
        self.fake_conv.calls.clear()
        with conversation_session_scope("conv_current"):
            w4.search_conversation_messages.invoke({"query": "질문"})
            w4.search_conversation_messages.invoke({"query": "질문", "conversation_id": "conv_x"})
        none_call, explicit_call = self.fake_conv.calls[-2], self.fake_conv.calls[-1]
        self.assertIsNone(none_call["conversation_id"])
        self.assertEqual(none_call["exclude"], "conv_current")
        self.assertEqual(explicit_call["conversation_id"], "conv_x")
        self.assertIsNone(explicit_call["exclude"])

    def test_f_conversation_return_shape(self) -> None:
        """hits==rows, context/rag_backend/sync 포함."""
        res = json.loads(w4.search_conversation_messages.invoke({"query": "질문"}))
        for key in ("hits", "rows", "context", "rag_backend", "sync"):
            self.assertIn(key, res)
        self.assertEqual(res["hits"], res["rows"])


if __name__ == "__main__":
    unittest.main()
