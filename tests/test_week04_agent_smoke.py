"""Week 4 agent 종단 스모크 테스트 (unittest, 실제 LLM 필요).

test_week04_failure_cases 는 각 tool을 직접 호출한 "계약"만 확인한다. 그래서 agent가
실제 질문에서 맞는 tool을 골랐는지 / 최종 답변이 검색 결과를 근거로 썼는지는 이 파일이
종단으로 확인한다. LLM이라 비결정적이고 PROXY_TOKEN(네트워크)이 필요하므로, 결정적
코드 테스트와 섞지 않고 환경변수 RUN_LLM_TESTS=1 로 켤 때만 실행한다(멘토 리뷰:
"토큰 있으면 자동"이 아니라 실행을 명시적으로 구분).

실행:
  RUN_LLM_TESTS=1 python -m unittest tests.test_week04_agent_smoke
(켜지 않으면 기본 discover에서 skip되고 import 시 외부 호출도 없다.)
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fixed.config as _cfg
from fixed.config import load_config

_REAL = load_config()
_HAS_KEY = _REAL.has_openai_key
_RUN_LLM = os.getenv("RUN_LLM_TESTS") == "1"
if not _RUN_LLM:
    _SKIP_REASON = "비결정론적 LLM 스모크: 기본 실행에서 제외됩니다. RUN_LLM_TESTS=1 로 명시적으로 켜세요."
elif not _HAS_KEY:
    _SKIP_REASON = "PROXY_TOKEN이 필요합니다."
else:
    _SKIP_REASON = ""

_TMP = Path(tempfile.mkdtemp(prefix="week4_smoke_"))
# import 시점 토큰을 비워, opt-in 하지 않으면 수집 단계에서도 외부 호출이 없다.
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

# 아래 import들은 위 CONFIG 패치 "뒤"에 와야 한다: week04 모듈은 import 시점에
# CONFIG를 읽어 전역 store를 만들기 때문에, 순서가 바뀌면 실제 data/에 파일이 생긴다.
import fixed.app_store
import fixed.conversation_rag_store
import fixed.llm
import fixed.reference_store
import student_parts.week03_build_nanas_logbook as w3
import student_parts.week04_retrieve_nanas_memory as w4


def _calls_and_answer(result: dict) -> tuple[list[str], str]:
    calls = [c["name"] for m in result["messages"] for c in (getattr(m, "tool_calls", None) or [])]
    answer = result["messages"][-1].content
    return calls, (answer if isinstance(answer, str) else str(answer))


@unittest.skipUnless(_RUN_LLM and _HAS_KEY, _SKIP_REASON)
class Week04AgentSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 실제 토큰 config를 스스로 구성(다른 테스트의 CONFIG 오염과 무관)한 뒤 store 재생성.
        cfg = dataclasses.replace(
            _REAL,
            chroma_dir=_TMP / "chroma",
            app_db_path=_TMP / "app.sqlite3",
            external_db_path=_TMP / "external.sqlite3",
        )
        for mod in (fixed.config, fixed.llm, fixed.reference_store, fixed.conversation_rag_store, w4):
            mod.CONFIG = cfg
        w4.REFERENCE_STORE = fixed.reference_store.PersonalReferenceStore(_TMP / "chroma")
        w4.SQLITE_STORE = fixed.app_store.AppSQLiteStore(_TMP / "app.sqlite3")
        w4.CONVERSATION_RAG_STORE = fixed.conversation_rag_store.ConversationRAGStore(_TMP / "chroma")
        w4._WEEK04_AGENT = None
        w3._store = lambda: w4.SQLITE_STORE
        # 근거로 검색될 데이터 seed (참고자료 기본값은 store 생성 시 자동 seed됨).
        # 치과 항목은 reminder로 저장 — 외부 공유 저장소 동기화(외부 호출)를 피한다.
        w3.save_structured_request_payload(
            {"kind": "reminder", "title": "치과 정기검진", "date": "2026-07-25", "start_time": "16:00"},
            store=w4.SQLITE_STORE,
        )
        conv_id = w4.SQLITE_STORE.create_conversation("제주도 여행 계획")["conversation_id"]
        w4.SQLITE_STORE.append_message(conv_id, "user", "제주도 여행 갈 건데 렌터카를 예약해야 할까?")
        w4.SQLITE_STORE.append_message(conv_id, "assistant", "제주도는 대중교통이 불편해서 렌터카 예약을 추천합니다.")
        cls.agent = w4.build_week04_agent()

    def _ask(self, prompt: str) -> tuple[list[str], str]:
        return _calls_and_answer(self.agent.invoke({"messages": [{"role": "user", "content": prompt}]}))

    def test_a_personal_reference_routing_and_grounding(self) -> None:
        calls, answer = self._ask("내가 집중이 잘 된다고 적어둔 회의 시간대가 언제였지?")
        self.assertIn("search_personal_references", calls, f"calls={calls}")
        self.assertTrue(any(tok in answer for tok in ["10", "12"]), f"answer={answer}")

    def test_b_saved_request_routing_and_grounding(self) -> None:
        calls, answer = self._ask("저장된 기록에서 치과 관련 일정을 검색해줘")
        self.assertIn("search_saved_requests", calls, f"calls={calls}")
        self.assertTrue(any(tok in answer for tok in ["25", "정기검진"]), f"answer={answer}")

    def test_c_conversation_routing_and_grounding(self) -> None:
        calls, answer = self._ask("지난 대화에서 제주도 여행 얘기할 때 렌터카에 대해 뭐라고 했었지?")
        self.assertIn("search_conversation_messages", calls, f"calls={calls}")
        self.assertTrue(any(tok in answer for tok in ["대중교통", "불편"]), f"answer={answer}")


if __name__ == "__main__":
    unittest.main()
