"""Week 6 supervisor 종단 스모크 테스트 (unittest, 실제 LLM + MCP 필요).

test_week06_supervisor는 stub으로 payload 계약만 확인한다. supervisor가 실제 요청을
알맞은 하위 에이전트로 위임하는지, 그룹 조율에서 조율 연쇄(일정 수집 → 후보 검증 →
최종 결정)가 이어지는지는 이 파일이 종단으로 확인한다.

LLM 특성상 비결정적이므로 결정적 테스트와 분리하고, RUN_LLM_TESTS=1 로 켤 때만 실행한다.

실행:
  RUN_LLM_TESTS=1 python -m unittest tests.test_week06_agent_smoke
(켜지 않으면 기본 discover에서 skip되고 import 시 외부 호출도 없다.)
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

_TMP = Path(tempfile.mkdtemp(prefix="week6_smoke_"))
# import 시점 토큰을 비워, opt-in 하지 않으면 수집 단계에서도 외부 호출이 없다.
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

# 아래 import는 CONFIG 패치 뒤에 와야 한다(week04/05 모듈이 import 시점에 전역 store를 만든다).
import fixed.app_store
import fixed.conversation_rag_store
import fixed.llm
import fixed.reference_store
import student_parts.week03_build_nanas_logbook as w3
import student_parts.week04_retrieve_nanas_memory as w4
import student_parts.week05_load_kanas_past_conversations as w5
import student_parts.week06_kanamate_decides_schedule as w6


@unittest.skipUnless(_RUN_LLM and _HAS_KEY, _SKIP_REASON)
class Week06SupervisorSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 실제 LLM/임베딩이 필요하므로 자기 설정을 스스로 구성한다(다른 테스트의 CONFIG 오염과 무관).
        cfg = dataclasses.replace(
            _REAL,
            chroma_dir=_TMP / "chroma",
            app_db_path=_TMP / "app.sqlite3",
            external_db_path=_TMP / "external.sqlite3",
        )
        for mod in (fixed.config, fixed.llm, fixed.reference_store, fixed.conversation_rag_store, w4, w5, w6):
            mod.CONFIG = cfg
        w4.REFERENCE_STORE = fixed.reference_store.PersonalReferenceStore(_TMP / "chroma")
        w4.SQLITE_STORE = fixed.app_store.AppSQLiteStore(_TMP / "app.sqlite3")
        w4.CONVERSATION_RAG_STORE = fixed.conversation_rag_store.ConversationRAGStore(_TMP / "chroma")
        w3._store = lambda: w4.SQLITE_STORE
        w6._NANA_SUBAGENT = None
        w6._KANA_SUBAGENT = None
        w6._SUPERVISOR_AGENT = None
        # MCP subprocess가 임시 외부 DB(seed fixtures)를 쓰게 한다.
        os.environ["KANANA_EXTERNAL_DB_PATH"] = str(_TMP / "external.sqlite3")
        # Nana가 조회할 내 일정 seed (reminder로 두어 공유 저장소 동기화 호출을 피한다).
        w3.save_structured_request_payload(
            {"kind": "reminder", "title": "치과 정기검진", "date": "2026-07-25", "start_time": "16:00"},
            store=w4.SQLITE_STORE,
        )
        cls.agent = w6.build_langchain_supervisor_agent()

    def _ask(self, prompt: str) -> dict:
        result = self.agent.invoke({"messages": [{"role": "user", "content": prompt}]})
        trace = w6.extract_langchain_trace(result)
        trace["answer"] = w6.extract_final_text(result) if hasattr(w6, "extract_final_text") else ""
        return trace

    @staticmethod
    def _delegated_agents(trace: dict) -> list[str]:
        """supervisor가 위임한 하위 agent를 호출 순서대로 뽑는다.

        trace의 supervisor_selected_agent는 "마지막에 부른" 하나만 남기므로, 그 필드로
        위임을 검증하면 Kana를 먼저 부르고 Nana를 나중에 불러도 통과해 버린다.
        위임 라우팅은 이 목록으로 검증한다.
        """

        return [
            event["tool_name"]
            for event in trace["events"]
            if event.get("event") == "tool_call" and event.get("tool_name") in {"nana_agent", "kana_agent"}
        ]

    def test_personal_request_is_delegated_to_nana(self) -> None:
        trace = self._ask("저장된 기록에서 치과 관련 일정을 검색해줘")
        delegated = self._delegated_agents(trace)
        # WHY(위임): 나 혼자에 대한 개인 기록 조회는 Nana 담당이어야 한다.
        self.assertIn("nana_agent", delegated, delegated)
        # WHY(오라우팅 방지): 개인 기록은 Kana가 볼 수 없으므로 Kana에게 위임하면 라우팅 오류다.
        #   (마지막 위임만 보는 필드로는 "Kana 먼저, Nana 나중"을 걸러내지 못한다)
        self.assertNotIn("kana_agent", delegated, delegated)
        # WHY(하위 실행): Nana 하위 trace에 개인 저장 기록 조회 도구가 남아야 한다.
        self.assertTrue(
            {"search_saved_requests", "personal_list_saved_schedules"} & set(trace["inner_tool_names"]),
            trace["inner_tool_names"],
        )

    @staticmethod
    def _saved_counts() -> tuple[int, int]:
        """앱 DB에 실제로 저장된 일정/구조화 요청 row 수(저장이 진짜 일어났는지 확인용)."""

        store = w4.SQLITE_STORE
        with store.connect() as conn:
            schedules = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
            requests = conn.execute("SELECT COUNT(*) FROM structured_requests").fetchone()[0]
        return schedules, requests

    def test_group_coordination_is_delegated_to_kana_with_decision_chain(self) -> None:
        # 요청을 "조율만"으로 명확히 한다. "정해줘"처럼 예약으로도 읽히는 표현을 쓰면 supervisor가
        # 저장까지 진행하는 경우가 있어(실측 1/3~1/4), 시나리오 자체를 모호하지 않게 고정한다.
        before = self._saved_counts()
        trace = self._ask(
            "철수랑 2026년 7월 14일에 1시간 회의 가능한 시간을 찾아서 최종 시간을 알려줘. 저장은 하지 말고 시간만 알려줘."
        )
        delegated = self._delegated_agents(trace)
        # WHY(위임): 다른 사람이 포함된 조율은 Kana 담당이어야 한다.
        self.assertIn("kana_agent", delegated, delegated)
        inner = set(trace["inner_tool_names"])
        # WHY(범위 준수): 저장을 요청하지 않았으므로 쓰기가 일어나면 안 된다(원치 않은 일정 생성 방지).
        #   supervisor가 Kana 답변을 보고 Nana에게 한 번 더 위임하는지는 LLM 판단이라 단정하지 않고,
        #   "요청하지 않은 쓰기가 실제로 일어났는가"라는 계약만 검증한다.
        #   (1) 쓰기 도구 호출 흔적 — 빠르게 원인을 알려주는 신호
        write_tools = {
            "save_structured_request",
            "personal_create_schedule",
            "create_shared_schedule",
            "personal_update_saved_schedule",
            "personal_delete_saved_schedules",
        }
        self.assertFalse(write_tools & inner, f"요청하지 않은 쓰기 도구 호출: {sorted(write_tools & inner)}")
        #   (2) 앱 DB row 수 — 저장이 "진짜로" 일어났는지 상태로 확인(도구를 안 거친 경로까지 포함)
        self.assertEqual(self._saved_counts(), before, "요청하지 않은 저장으로 앱 DB row가 늘었습니다")
        # WHY(근거 수집): 후보를 고르기 전에 busy-time을 모으는 도구가 호출돼야 한다.
        self.assertTrue(
            {"collect_member_schedules", "extract_schedules_from_history"} & inner,
            trace["inner_tool_names"],
        )
        # WHY(조율 연쇄): 후보 검증에서 멈추지 않고 최종 결정까지 이어져야 한다(가이드 검증 기준).
        self.assertIn("decide_final_slot", inner, trace["inner_tool_names"])
        # WHY(결과 승격): supervisor가 답변 근거로 쓸 final_slot_payload가 올라와야 한다.
        self.assertIsNotNone(trace["final_slot_payload"], trace)
        print("\n[final_slot_payload]", json.dumps(trace["final_slot_payload"], ensure_ascii=False)[:300])


if __name__ == "__main__":
    unittest.main()
