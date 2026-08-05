"""Week 6 supervisor/하위 에이전트 구조 단위 테스트 (unittest, 결정적·기본 실행).

LLM과 하위 agent를 stub으로 대체해 (1) tool 조립, (2) 위임 wrapper의 payload 계약,
(3) 후보 검증·최종 결정 payload 계약, (4) 조합 프롬프트 위생을 결정적으로 검증한다.
실제 supervisor 위임 판단(비결정적)은 test_week06_agent_smoke(RUN_LLM_TESTS)에서만 확인한다.

import 전에 CONFIG 경로를 임시로·토큰을 비워 실제 data/·외부 호출을 격리한다.

실행: python -m unittest discover -s tests
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fixed.config as _cfg

_TMP = Path(tempfile.mkdtemp(prefix="week6_supervisor_"))
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

# 아래 import는 CONFIG 패치 뒤에 와야 한다(week04/05 모듈이 import 시점에 전역 store를 만든다).
import student_parts.week06_kanamate_decides_schedule as w6


class _FakeToolCall:
    """tool_call 이벤트를 만드는 가짜 AI 메시지."""

    def __init__(self, name: str, args: dict | None = None) -> None:
        self.tool_calls = [{"name": name, "args": args or {}, "id": f"call_{name}"}]
        self.type = "ai"
        self.content = ""


class _FakeToolResult:
    """tool_result 이벤트를 만드는 가짜 tool 메시지(content는 JSON 문자열)."""

    def __init__(self, name: str, payload: dict) -> None:
        self.tool_calls = []
        self.type = "tool"
        self.name = name
        self.content = json.dumps(payload, ensure_ascii=False)
        self.tool_call_id = f"call_{name}"


class _FakeAnswer:
    def __init__(self, text: str) -> None:
        self.tool_calls = []
        self.type = "ai"
        self.content = text


class _FakeAgent:
    def __init__(self, messages: list) -> None:
        self._messages = messages
        self.invocations: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.invocations.append(payload)
        return {"messages": self._messages}


class Week06ToolComposition(unittest.TestCase):
    """supervisor는 위임 도구 2개만 보고, Kana는 외부·조율 도구를 갖는다."""

    def test_supervisor_sees_only_delegation_tools(self) -> None:
        self.assertEqual(w6.agent_tool_names("supervisor"), ["nana_agent", "kana_agent"])

    def test_kana_tools_include_external_and_decision_tools(self) -> None:
        names = w6.agent_tool_names("kana_agent")
        for expected in (
            "extract_schedule_request",
            "search_previous_conversations",
            "extract_schedules_from_history",
            "list_shared_schedules",
            "collect_member_schedules",
            "find_common_available_slots",
            "decide_final_slot",
        ):
            self.assertIn(expected, names)
        # 호환용 helper는 Kana 도구 목록에 들어가지 않는다.
        self.assertNotIn("propose_group_schedule", names)

    def test_nana_tools_are_week04_tools(self) -> None:
        names = w6.agent_tool_names("nana_agent")
        self.assertIn("personal_list_saved_schedules", names)
        self.assertIn("search_personal_references", names)
        # Nana는 외부 멤버/조율 도구를 갖지 않는다(역할 분리).
        self.assertNotIn("collect_member_schedules", names)
        self.assertNotIn("decide_final_slot", names)


class Week06SlotDecision(unittest.TestCase):
    """후보 검증/최종 결정은 tool이 계산하지 않고 agent가 넘긴 값을 검증·기록한다."""

    BUSY = [{"member_name": "철수", "title": "QA 리뷰", "date": "2026-07-14", "start_time": "10:00", "end_time": "11:00", "notes": ""}]

    def test_overlapping_candidate_is_rejected(self) -> None:
        result = json.loads(
            w6.find_common_available_slots.invoke(
                {
                    "member_names": ["철수"],
                    "date_from": "2026-07-14",
                    "date_to": "2026-07-14",
                    "busy_rows": self.BUSY,
                    "candidate_slots": [
                        {"date": "2026-07-14", "start_time": "10:30", "end_time": "11:30", "duration_minutes": 60, "reason": "겹침"},
                        {"date": "2026-07-14", "start_time": "14:00", "end_time": "15:00", "duration_minutes": 60, "reason": "가능"},
                    ],
                }
            )
        )
        kept = [(slot["start_time"], slot["end_time"]) for slot in result["candidate_slots"]]
        self.assertEqual(kept, [("14:00", "15:00")], result["candidate_slots"])
        self.assertEqual(result["busy_rows"], self.BUSY)  # 근거 보존

    def test_my_schedule_is_included_as_evidence_once(self) -> None:
        """내 일정도 근거이므로 members에 "나"가 포함되고 중복되지 않는다."""

        result = json.loads(
            w6.find_common_available_slots.invoke(
                {"member_names": ["나", "철수"], "date_from": "2026-07-14", "date_to": "2026-07-14", "busy_rows": []}
            )
        )
        self.assertEqual(result["members"], ["나", "철수"])

    def test_empty_candidates_returns_no_slots(self) -> None:
        """tool이 후보를 대신 계산하지 않는다(비워 넘기면 결과도 비어야 한다)."""

        result = json.loads(
            w6.find_common_available_slots.invoke(
                {"member_names": ["철수"], "date_from": "2026-07-14", "date_to": "2026-07-14", "busy_rows": self.BUSY}
            )
        )
        self.assertEqual(result["candidate_slots"], [])

    def test_decide_final_slot_contract(self) -> None:
        candidates = [{"date": "2026-07-14", "start_time": "14:00", "end_time": "15:00", "duration_minutes": 60, "reason": "가능"}]
        decided = json.loads(
            w6.decide_final_slot.invoke(
                {
                    "candidate_slots": candidates,
                    "selected_index": 0,
                    "final_slot": "2026-07-14 14:00-15:00",
                    "needs_agent_selection": False,
                    "reason": "둘 다 비어 있는 시간",
                    "member_names": ["나", "철수"],
                }
            )
        )
        for key in ("final_slot", "reason", "candidates"):  # course repo top-level 계약
            self.assertIn(key, decided)
        self.assertEqual(decided["final_slot"], "2026-07-14 14:00-15:00")
        self.assertFalse(decided["needs_agent_selection"])

    def test_no_selection_keeps_needs_agent_selection(self) -> None:
        """selected_index/selected_slot이 없으면 final_slot을 자동으로 고르지 않는다."""

        candidates = [{"date": "2026-07-14", "start_time": "14:00", "end_time": "15:00", "duration_minutes": 60, "reason": "가능"}]
        decided = json.loads(w6.decide_final_slot.invoke({"candidate_slots": candidates}))
        self.assertIsNone(decided["final_slot"])
        self.assertTrue(decided["needs_agent_selection"])
        self.assertEqual(decided["candidates"], ["2026-07-14 14:00-15:00"])


class Week06Delegation(unittest.TestCase):
    """위임 wrapper는 하위 agent를 실행해 answer/trace/inner_tool_names를 올려준다."""

    def setUp(self) -> None:
        self._orig_create = w6.create_agent
        self._orig_chat = w6.chat_model
        w6.chat_model = lambda **kwargs: object()  # 실제 LLM 클라이언트 생성 차단
        w6._NANA_SUBAGENT = None
        w6._KANA_SUBAGENT = None

    def tearDown(self) -> None:
        w6.create_agent = self._orig_create
        w6.chat_model = self._orig_chat
        w6._NANA_SUBAGENT = None
        w6._KANA_SUBAGENT = None

    def _install(self, messages: list) -> list:
        created: list = []

        def fake_create_agent(**kwargs):
            agent = _FakeAgent(messages)
            created.append(agent)
            return agent

        w6.create_agent = fake_create_agent
        return created

    def test_nana_agent_payload_and_inner_tools(self) -> None:
        created = self._install(
            [
                _FakeToolCall("personal_list_saved_schedules"),
                _FakeToolResult("personal_list_saved_schedules", {"ok": True, "schedules": []}),
                _FakeAnswer("저장된 일정은 없습니다."),
            ]
        )
        payload = json.loads(w6.nana_agent.invoke({"query": "내 일정 보여줘"}))
        self.assertEqual(payload["selected_agent"], "nana_agent")
        self.assertEqual(payload["answer"], "저장된 일정은 없습니다.")
        self.assertEqual(payload["inner_tool_names"], ["personal_list_saved_schedules"])
        self.assertTrue(payload["trace"]["events"])
        self.assertEqual(created[0].invocations[0]["messages"][0]["content"], "내 일정 보여줘")

    def test_nana_subagent_is_reused(self) -> None:
        created = self._install([_FakeAnswer("응답")])
        w6.nana_agent.invoke({"query": "1"})
        w6.nana_agent.invoke({"query": "2"})
        self.assertEqual(len(created), 1, "하위 agent는 한 번만 만들고 재사용해야 한다")

    def test_kana_agent_lifts_final_slot_payload(self) -> None:
        decided = {
            "ok": True,
            "tool_name": "decide_final_slot",
            "final_slot": "2026-07-14 14:00-15:00",
            "reason": "둘 다 비어 있는 시간",
            "candidates": ["2026-07-14 14:00-15:00"],
            "needs_agent_selection": False,
        }
        self._install(
            [
                _FakeToolCall("collect_member_schedules"),
                _FakeToolResult("collect_member_schedules", {"ok": True, "rows": []}),
                _FakeToolCall("find_common_available_slots"),
                _FakeToolResult("find_common_available_slots", {"ok": True, "candidate_slots": []}),
                _FakeToolCall("decide_final_slot"),
                _FakeToolResult("decide_final_slot", decided),
                _FakeAnswer("7월 14일 14시로 정했습니다."),
            ]
        )
        payload = json.loads(w6.kana_agent.invoke({"query": "철수랑 일정 맞춰줘"}))
        self.assertEqual(payload["selected_agent"], "kana_agent")
        self.assertEqual(payload["final_slot_payload"]["final_slot"], "2026-07-14 14:00-15:00")
        self.assertEqual(
            payload["inner_tool_names"],
            ["collect_member_schedules", "find_common_available_slots", "decide_final_slot"],
        )

    def test_kana_agent_lifts_final_decision_payload(self) -> None:
        self._install(
            [
                _FakeToolCall("propose_group_schedule"),
                _FakeToolResult("propose_group_schedule", {"ok": True, "final_decision": {"status": "confirmed"}}),
                _FakeAnswer("확정했습니다."),
            ]
        )
        payload = json.loads(w6.kana_agent.invoke({"query": "확정해줘"}))
        self.assertEqual(payload["final_decision_payload"], {"status": "confirmed"})

    def test_supervisor_trace_lifts_selected_agent_and_slot(self) -> None:
        """supervisor trace가 위임 대상과 최종 시간 payload를 UI용으로 정리한다."""

        kana_result = {
            "ok": True,
            "tool_name": "kana_agent",
            "selected_agent": "kana_agent",
            "answer": "7월 14일 14시",
            "inner_tool_names": ["collect_member_schedules", "decide_final_slot"],
            "final_slot_payload": {"final_slot": "2026-07-14 14:00-15:00", "reason": "가능"},
            "final_decision_payload": None,
        }
        supervisor_result = {
            "messages": [
                _FakeToolCall("kana_agent"),
                _FakeToolResult("kana_agent", kana_result),
                _FakeAnswer("7월 14일 14시로 정했습니다."),
            ]
        }
        trace = w6.extract_langchain_trace(supervisor_result)
        self.assertEqual(trace["supervisor_selected_agent"], "kana_agent")
        self.assertEqual(trace["inner_tool_names"], ["collect_member_schedules", "decide_final_slot"])
        self.assertEqual(trace["final_slot_payload"]["final_slot"], "2026-07-14 14:00-15:00")


class Week06PromptHygiene(unittest.TestCase):
    """멘토 리뷰: 최종 조합 프롬프트의 단일 정체성 / 무 'Week N' 라벨 / 무 모순."""

    IDENTITY = re.compile(r"너는 .{0,40}?(agent|에이전트|비서|Kana|Nana)")

    def test_prompts_have_single_identity_and_no_week_labels(self) -> None:
        for name, build in (
            ("supervisor", w6.week06_system_prompt),
            ("nana", w6.nana_system_prompt),
            ("kana", w6.kana_system_prompt),
        ):
            prompt = build()
            identity = [line.strip() for line in prompt.splitlines() if self.IDENTITY.search(line)]
            self.assertEqual(len(identity), 1, f"{name}: 정체성 문장 {len(identity)}개 → {identity}")
            self.assertIsNone(re.search(r"Week\s*\d", prompt), f"{name}: 'Week N' 라벨 노출")
            self.assertNotIn("아직 하지 않는다", prompt, f"{name}: 커리큘럼 범위 부정 잔존")

    def test_supervisor_prompt_states_delegation_only(self) -> None:
        prompt = w6.week06_system_prompt()
        self.assertIn("nana_agent", prompt)
        self.assertIn("kana_agent", prompt)
        self.assertIn("위임", prompt)

    def test_kana_prompt_is_self_contained(self) -> None:
        """Kana는 누적이 없으므로 역할·날짜 기준·조율 연쇄를 스스로 갖고 있어야 한다."""

        prompt = w6.kana_system_prompt()
        self.assertIn("Kana", prompt)
        self.assertIn("오늘 날짜는", prompt)
        for tool in ("collect_member_schedules", "find_common_available_slots", "decide_final_slot"):
            self.assertIn(tool, prompt)
        self.assertIn("Nana", prompt)  # 저장은 Nana 담당이라는 경계

    def test_nana_prompt_declines_group_coordination(self) -> None:
        self.assertIn("Kana", w6.nana_system_prompt())


if __name__ == "__main__":
    unittest.main()
