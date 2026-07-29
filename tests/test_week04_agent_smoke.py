"""Week 4 agent 종단 스모크 테스트 (unittest, 실제 LLM 필요).

왜 이 파일이 따로 있는가:
  test_week04_failure_cases 는 각 tool을 직접 호출한 "계약"만 확인한다. 그래서
  "agent가 실제 질문에서 맞는 tool을 골랐는지"와 "최종 답변이 검색 결과를 실제로
  근거로 썼는지"는 검증되지 않는다. 이 파일이 그 둘을 종단으로 확인한다.

왜 별도 파일 + 명시적 opt-in 인가:
  agent의 tool 선택은 LLM이라 비결정적이고 PROXY_TOKEN(네트워크)이 필요하다.
  결정적 계약 검증(test_week04_failure_cases)과 섞으면 간헐 실패가 나므로 분리한다.
  "토큰이 있으면 자동 실행"이 아니라 환경변수 RUN_LLM_TESTS=1 로 켤 때만 실행되도록
  명시적으로 opt-in 한다. 켜지 않으면 기본 discover에서 이 테스트는 skip될 뿐 아니라
  import 시점의 외부 호출(임베딩)도 일어나지 않는다.

실행:
  # 결정적(코드) 테스트만 — 기본
  python -m unittest discover -s tests
  # 비결정론적 LLM 스모크까지 포함 — 명시적으로 켤 때만
  RUN_LLM_TESTS=1 python -m unittest discover -s tests
  RUN_LLM_TESTS=1 python -m unittest tests.test_week04_agent_smoke

왜 임시 경로로 CONFIG를 돌리는가:
  agent가 실제 ChromaDB/SQLite(영구 저장소)에 읽고 쓰기 때문에, 실제 data/를
  오염시키면 사용자 데이터가 더럽혀지고 재현성도 깨진다. 그래서 저장 경로는 임시로
  돌린다. 실제 토큰은 opt-in 되어 실제로 도는 setUpClass에서만 적용한다(import 시점엔
  토큰을 비워 어떤 외부 호출도 없게 한다).
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

_REAL = load_config()  # .env에서 실제 토큰을 새로 읽는다(다른 테스트의 CONFIG 오염과 무관)
_HAS_KEY = _REAL.has_openai_key
# 비결정론적 LLM 테스트는 "토큰이 있으면 자동"이 아니라 명시적 opt-in(RUN_LLM_TESTS=1)으로만 실행한다.
_RUN_LLM = os.getenv("RUN_LLM_TESTS") == "1"
if not _RUN_LLM:
    _SKIP_REASON = "비결정론적 LLM 스모크: 기본 실행에서 제외됩니다. RUN_LLM_TESTS=1 로 명시적으로 켜세요."
elif not _HAS_KEY:
    _SKIP_REASON = "PROXY_TOKEN이 필요합니다."
else:
    _SKIP_REASON = ""

_TMP = Path(tempfile.mkdtemp(prefix="week4_smoke_"))
# import 시점에는 토큰을 비운다 → opt-in 하지 않으면 (discover 수집 단계 포함) 어떤 외부 호출도 없다.
# 실제 토큰은 opt-in 되어 실제로 도는 setUpClass에서만 적용한다.
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

import student_parts.week04_retrieve_nanas_memory as w4
import student_parts.week03_build_nanas_logbook as w3


def _calls_and_answer(result: dict) -> tuple[list[str], str]:
    calls = [c["name"] for m in result["messages"] for c in (getattr(m, "tool_calls", None) or [])]
    answer = result["messages"][-1].content
    return calls, (answer if isinstance(answer, str) else str(answer))


@unittest.skipUnless(_RUN_LLM and _HAS_KEY, _SKIP_REASON)
class Week04AgentSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # smoke는 실제 LLM/임베딩이 필요하므로 "자기 설정을 스스로" 구성한다.
        # 다른 (결정적) 테스트가 프로세스 전역·모듈 바인딩의 토큰을 비워 놨더라도,
        # 여기서 실제 토큰 + 임시 경로 config로 통일하고 store를 새로 만든 뒤 agent 캐시를 리셋한다.
        import fixed.config
        import fixed.llm
        import fixed.reference_store
        import fixed.conversation_rag_store
        import fixed.app_store

        cfg = dataclasses.replace(
            _REAL,  # .env의 실제 토큰
            chroma_dir=_TMP / "chroma",
            app_db_path=_TMP / "app.sqlite3",
            external_db_path=_TMP / "external.sqlite3",
        )
        # from-import로 CONFIG를 붙잡은 모듈들의 바인딩을 실제 토큰 config로 통일한다.
        for mod in (fixed.config, fixed.llm, fixed.reference_store, fixed.conversation_rag_store, w4):
            mod.CONFIG = cfg
        w4.REFERENCE_STORE = fixed.reference_store.PersonalReferenceStore(_TMP / "chroma")
        w4.SQLITE_STORE = fixed.app_store.AppSQLiteStore(_TMP / "app.sqlite3")
        w4.CONVERSATION_RAG_STORE = fixed.conversation_rag_store.ConversationRAGStore(_TMP / "chroma")
        w4._WEEK04_AGENT = None  # 실제 chat_model로 다시 빌드
        w3._store = lambda: w4.SQLITE_STORE

        # 근거로 검색될 데이터 seed (개인 참고자료 기본값은 store 생성 시 자동 seed됨: "집중 회의 선호" 등).
        # 치과 항목은 reminder로 저장 — 외부 공유 저장소 동기화(외부 호출)를 피하기 위함.
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
        # WHY(라우팅): 개인 취향·메모 질문은 참고자료 벡터 검색으로 가야 한다.
        self.assertIn("search_personal_references", calls, f"calls={calls}")
        # WHY(근거 사용): 답변이 참고자료 내용(오전 10~12시)을 실제로 써야 검색 결과 기반이다.
        self.assertTrue(any(tok in answer for tok in ["10", "12"]), f"answer={answer}")

    def test_b_saved_request_routing_and_grounding(self) -> None:
        calls, answer = self._ask("저장된 기록에서 치과 관련 일정을 검색해줘")
        # WHY(라우팅): 저장된 일정 기록 질문은 SQLite 검색으로 가야 한다.
        self.assertIn("search_saved_requests", calls, f"calls={calls}")
        # WHY(근거 사용): 답변이 저장된 일정의 고유 값(7/25)을 써야 검색 결과 기반이다.
        self.assertTrue(any(tok in answer for tok in ["25", "정기검진"]), f"answer={answer}")

    def test_c_conversation_routing_and_grounding(self) -> None:
        calls, answer = self._ask("지난 대화에서 제주도 여행 얘기할 때 렌터카에 대해 뭐라고 했었지?")
        # WHY(라우팅): 과거 대화 질문은 대화 RAG 검색으로 가야 한다.
        self.assertIn("search_conversation_messages", calls, f"calls={calls}")
        # WHY(근거 사용): 답변이 과거 대화의 고유 표현(대중교통/불편)을 써야 대화 RAG 결과 기반이다.
        self.assertTrue(any(tok in answer for tok in ["대중교통", "불편"]), f"answer={answer}")


if __name__ == "__main__":
    unittest.main()
