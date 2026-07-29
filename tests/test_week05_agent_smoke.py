"""Week 5 agent 종단 스모크 테스트 (unittest, 실제 LLM + MCP 필요).

agent가 팀 일정 조율/외부 멤버 질문에서 Week 5 MCP 도구를 고르고, 그 결과를
근거로 답하는지 종단으로 확인한다. LLM 특성상 비결정적이라 결정적 코드 테스트와
분리하고, 환경변수 RUN_LLM_TESTS=1 로 켤 때만 실행한다(멘토 리뷰: 실행 구분을 명시적으로).

실행:
  RUN_LLM_TESTS=1 python -m unittest tests.test_week05_agent_smoke
(켜지 않으면 기본 discover에서 skip되고 import 시 외부 호출도 없다.)

격리: import 시 토큰을 비워 부작용을 막고, opt-in 시 setUpClass에서 실제 토큰 config를
스스로 구성한 뒤 임시 외부 DB(KANANA_EXTERNAL_DB_PATH)로 MCP를 격리한다.
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

_TMP = Path(tempfile.mkdtemp(prefix="week5_smoke_"))
# import 시점 토큰을 비워, opt-in 하지 않으면 수집 단계에서도 외부 호출이 없다.
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

import student_parts.week05_load_kanas_past_conversations as w5


def _calls_and_answer(result: dict) -> tuple[list[str], str]:
    calls = [c["name"] for m in result["messages"] for c in (getattr(m, "tool_calls", None) or [])]
    answer = result["messages"][-1].content
    return calls, (answer if isinstance(answer, str) else str(answer))


@unittest.skipUnless(_RUN_LLM and _HAS_KEY, _SKIP_REASON)
class Week05AgentSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 실제 LLM/임베딩이 필요하므로 자기 설정을 스스로 구성한다(다른 테스트의 CONFIG 오염과 무관).
        import fixed.config
        import fixed.llm
        import fixed.reference_store
        import fixed.conversation_rag_store
        import fixed.app_store
        import student_parts.week04_retrieve_nanas_memory as w4

        cfg = dataclasses.replace(
            _REAL,
            chroma_dir=_TMP / "chroma",
            app_db_path=_TMP / "app.sqlite3",
            external_db_path=_TMP / "external.sqlite3",
        )
        for mod in (fixed.config, fixed.llm, fixed.reference_store, fixed.conversation_rag_store, w4, w5):
            mod.CONFIG = cfg
        w4.REFERENCE_STORE = fixed.reference_store.PersonalReferenceStore(_TMP / "chroma")
        w4.SQLITE_STORE = fixed.app_store.AppSQLiteStore(_TMP / "app.sqlite3")
        w4.CONVERSATION_RAG_STORE = fixed.conversation_rag_store.ConversationRAGStore(_TMP / "chroma")
        w5._WEEK05_AGENT = None  # 실제 chat_model로 다시 빌드
        # MCP subprocess가 임시 외부 DB(seed fixtures)를 쓰게 한다.
        os.environ["KANANA_EXTERNAL_DB_PATH"] = str(_TMP / "external.sqlite3")
        cls.agent = w5.build_week05_agent()

    def _ask(self, prompt: str) -> tuple[list[str], str]:
        return _calls_and_answer(self.agent.invoke({"messages": [{"role": "user", "content": prompt}]}))

    def test_external_member_query_uses_week5_tool_and_grounds(self) -> None:
        calls, answer = self._ask("팀원 철수의 7월 일정을 알려줘")
        week5_external = {
            "extract_schedules_from_history",
            "list_shared_schedules",
            "collect_member_schedules",
            "search_previous_conversations",
        }
        # WHY(라우팅): 외부 멤버 일정 질문은 Week 5 MCP 도구로 가야 한다(개인 저장/RAG 도구가 아니라).
        self.assertTrue(week5_external & set(calls), f"calls={calls}")
        # WHY(근거 사용): 답변이 철수 fixture 일정(제목)을 실제로 써야 외부 조회 결과 기반이다.
        self.assertIn("철수", answer)
        self.assertTrue(any(tok in answer for tok in ["API", "인터뷰", "QA", "고객"]), f"answer={answer}")


if __name__ == "__main__":
    unittest.main()
