"""Week 6 supervisor 위임 라우팅 + 그룹 시간 결정 체인 재현 가능 eval.

Week 5 eval(evals/week05_eval.py)의 7단계 골격을 그대로 이식하되, Week 6 맥락
(요청 성격 → nana_agent / kana_agent 위임 → 하위 agent 안에서 어떤 tool을 이어 불렀나)에 맞춘다.

  1. 입력 고정   — 시계·상태(temp 앱 SQLite + temp 외부 SQLite + temp ChromaDB)·**호출 채널**을 못 박는다
  2. 검사 항목   — CASES 골든셋(위임 라우팅 + 하위 체인 + 답변 정확성 + 과교정 방지 + 이전 주차 회귀)
  3. 반복        — --n (기본 3)
  4. 판정        — 네 축: (a) supervisor가 고른 하위 agent (nana_agent / kana_agent)
                          (b) 하위 agent 안에서 실제로 부른 tool 목록(inner_tool_names)
                          (c) 최종 답변 본문 + final_slot_payload 일치
                          (d) 후보 검증/최종 결정 안전규칙은 tool 직접 단정(LLM 무관)
  5. 집계        — 케이스별 통과율 n/N
  6. 비교        — --baseline out.json 저장 / 다음 실행과 diff
  7. 게이트      — critical 케이스 1회 실패 = 전체 실패, non-zero exit

⚠️ Week 6 핵심은 "supervisor가 직접 일하지 않고 알맞은 하위 agent로 위임한다"이다. 그래서 1차 판정축은
delegated_agents_of(tools) — nana_agent(개인 일정/저장/RAG)인가 kana_agent(외부 멤버/그룹 조율)인가 — 이고,
2차 판정축은 inner_tools_of(out) — 하위 agent가 그 안에서 무엇을 이어 불렀나 — 다.
supervisor가 보는 tool은 두 개뿐이라(week06 line 442-443) **위임 판단은 전부 prompt가 결정한다.**
그래서 위임이 새면 tool 구현이 아니라 prompt의 판단 기준을 고친다(가이드 line 124).

⚠️ **판정축이 두 겹인 이유.** supervisor 결과의 `tool_calls`에는 `nana_agent`/`kana_agent`만 남는다.
하위 agent가 무엇을 불렀는지는 위임 tool이 반환한 JSON의 `inner_tool_names`에만 있고,
그걸 끌어올리는 것이 `extract_langchain_trace()`(week06 line 250-278, 제공 코드)다.
따라서 **위임은 맞았는데 하위 체인이 엉뚱한** 실패는 (a) 축만 보면 절대 안 잡힌다.

⚠️ 시계를 2026-07-06(월)로 고정한다. 외부 실습 fixture가 2026-07-07 ~ 2026-07-17에 seed되어 있어
(fixed/external_people_store.py JULY_PRACTICE_*), 이 날짜여야 "이번 주/다음 주" 질문이 seed 데이터에 걸린다.

⚠️ 상태 격리가 핵심이다. 반복마다 새 temp dir로 **네 경로**를 모두 돌린다(week05 eval과 동일):
  (a) week05 모듈 CONFIG      — collect_member_schedules가 여는 앱 DB
  (b) week03/week04 모듈 전역  — Nana 하위 agent가 쓰는 앱 DB / ChromaDB
  (c) KANANA_EXTERNAL_DB_PATH — MCP subprocess가 읽는 외부 DB (fixed/mcp_client.py:85-87)
  (d) PERSONAL_SCHEDULES      — Week 1 인메모리 리스트(모듈 전역이라 반복 사이에 샌다)

⚠️ Nana 하위 agent가 Week 4 tool(ChromaDB 임베딩)을 그대로 쓰고 supervisor 자체가 LLM이라
이 eval은 키가 있어야 돈다. 키가 없으면 즉시 SKIP(exit 0)한다.
(결정적 계약 검증은 키 없이 `verify-week6` skill이 담당한다.)

이 파일은 `student_parts/`·`fixed/`를 **import만** 한다. 과제 코드는 수정하지 않는다.

실행:
  uv run python -X utf8 evals/week06_eval.py --n 3
  uv run python -X utf8 evals/week06_eval.py --n 5 --save evals/week06_baseline.json
  uv run python -X utf8 evals/week06_eval.py --n 3 --baseline evals/week06_baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

# --- 1. 입력 고정: 외부 July 실습 fixture(07-07~07-17) 직전 월요일로 시계를 못 박는다. ---
FROZEN_TODAY = date(2026, 7, 6)  # 월요일 → 이번 주 = 07-06~07-12, 다음 주 = 07-13~07-19

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fixed.runtime_clock as rc  # noqa: E402

rc.APP_TODAY = FROZEN_TODAY  # import 시점 고정값을 덮어쓴다 (프롬프트 조립 전에)

from fixed.config import CONFIG  # noqa: E402
from fixed.conversation_rag_store import ConversationRAGStore  # noqa: E402
from fixed.reference_store import PersonalReferenceStore  # noqa: E402
from fixed.schedule_decision import busy_rows_overlap, parse_time_minutes  # noqa: E402
from fixed.session_scope import conversation_session_scope  # noqa: E402
import fixed.app_store as store_mod  # noqa: E402
import student_parts.week01_wake_up_nana as w1  # noqa: E402
import student_parts.week03_build_nanas_logbook as w3  # noqa: E402
import student_parts.week04_retrieve_nanas_memory as w4  # noqa: E402
import student_parts.week05_load_kanas_past_conversations as w5  # noqa: E402
import student_parts.week06_kanamate_decides_schedule as m  # noqa: E402

TODAY = rc.current_app_date_iso()  # 2026-07-06

# 외부 실습 fixture에서 골라 쓰는 확인용 사실 (fixed/external_people_store.py JULY_PRACTICE_*)
CHULSOO_THU = ("2026-07-09", "14:00", "고객 인터뷰")   # 철수 · 이번 주 목요일
YOUNGHEE_TUE = ("2026-07-07", "13:00", "디자인 피드백")  # 영희 · 이번 주 화요일

# 하위 agent별 tool 세력권 — 라우팅이 새는 것을 이름으로 잡는다
NANA_ONLY_TOOLS = {
    "personal_list_saved_schedules",
    "personal_create_schedule",
    "personal_update_saved_schedules",
    "personal_delete_saved_schedules",
    "save_structured_request",
    "list_saved_requests",
    "search_saved_requests",
    "search_personal_references",
    "add_personal_reference",
    "search_conversation_messages",
}
KANA_ONLY_TOOLS = {
    "search_previous_conversations",
    "load_conversation_messages",
    "extract_schedules_from_history",
    "list_shared_schedules",
    "collect_member_schedules",
    "find_common_available_slots",
    "decide_final_slot",
}


# --------------------------------------------------------------------- 상태/store 헬퍼
_CHROMA_ONCE: dict[str, Any] = {}


def _shared_chroma_stores() -> dict[str, Any]:
    """--- 1. 상태 격리: ChromaDB client는 **실행 전체에서 한 번만** 만든다. ---

    ⚠️ 반복마다 PersistentClient를 새로 만들면 파일 핸들이 쌓여
    `OSError: [Errno 24] Too many open files`로 실행이 통째로 죽는다. 케이스가 24→26개로
    늘자(멘토 리뷰 축 추가) 26×5=130회가 되어 실제로 터졌다. week05 eval이 결정적 축을
    경량 rebind로 분리해 우회했던 것과 같은 문제이고, 여기서는 근본 원인인
    "매 반복 client 생성"을 없앤다.

    임베딩 저장소를 한 번만 만들어도 격리는 유지된다 — 케이스가 쓰는 SQLite와 외부 DB는
    반복마다 새 temp로 갈아끼우고, 참고자료 seed는 같은 내용을 반복해 넣어도 검색 결과가 같다.
    """
    if not _CHROMA_ONCE:
        chroma_dir = Path(tempfile.mkdtemp()) / "chroma"
        _CHROMA_ONCE["dir"] = chroma_dir
        _CHROMA_ONCE["reference"] = PersonalReferenceStore(chroma_dir)  # seed됨
        _CHROMA_ONCE["conversation"] = ConversationRAGStore(chroma_dir)
    return _CHROMA_ONCE


def rebind_temp_stores() -> Path:
    """--- 1. 상태 격리: 반복마다 새 temp 앱 DB / 외부 DB / ChromaDB로 전 경로를 재바인딩한다. ---

    week05 eval의 rebind_temp_stores()와 같다. Week 6 파일은 자체 store를 들고 있지 않고
    Week 4/5 tool을 그대로 조립하므로, 그 모듈 전역만 돌리면 모든 경로에 반영된다.
    """
    tmp = Path(tempfile.mkdtemp())
    chroma = _shared_chroma_stores()
    temp_config = replace(
        CONFIG,
        app_db_path=tmp / "app.sqlite3",
        external_db_path=tmp / "external.sqlite3",
        chroma_dir=chroma["dir"],
    )
    # (c) MCP subprocess가 읽는 외부 DB — 첫 tool 호출 전에 세팅해야 한다
    os.environ["KANANA_EXTERNAL_DB_PATH"] = str(tmp / "external.sqlite3")
    # (a) Week 5 tool(collect_member_schedules 등)이 여는 앱 DB
    w5.CONFIG = temp_config
    # (b) Nana 하위 agent가 쓰는 Week 3-4 경로
    w3.CONFIG = temp_config
    w4.CONFIG = temp_config
    w4.SQLITE_STORE = store_mod.AppSQLiteStore(tmp / "app.sqlite3")
    # 임베딩 저장소는 실행 전체에서 한 번만 만든 것을 재사용한다(파일 핸들 고갈 방지).
    w4.REFERENCE_STORE = chroma["reference"]
    w4.CONVERSATION_RAG_STORE = chroma["conversation"]
    # (d) Week 1 인메모리 임시 일정 — 리스트 객체는 유지하고 내용만 비운다
    w1.PERSONAL_SCHEDULES[:] = []
    return tmp


def rebind_temp_dbs() -> Path:
    """--- 1. 상태 격리(경량): ChromaDB 없이 앱/외부 SQLite만 temp로 돌린다. ---

    결정적 축(find_common_available_slots / decide_final_slot)은 임베딩 경로를 전혀 타지 않는데
    rebind_temp_stores()는 매 호출 PersistentClient를 새로 만들어 파일 핸들이 쌓인다
    (week05 eval에서 `OSError: [Errno 24] Too many open files`로 뒤쪽 축이 무더기 실패했다).
    """
    tmp = Path(tempfile.mkdtemp())
    temp_config = replace(
        CONFIG,
        app_db_path=tmp / "app.sqlite3",
        external_db_path=tmp / "external.sqlite3",
        chroma_dir=tmp / "chroma",
    )
    os.environ["KANANA_EXTERNAL_DB_PATH"] = str(tmp / "external.sqlite3")
    w5.CONFIG = temp_config
    w3.CONFIG = temp_config
    w4.CONFIG = temp_config
    w1.PERSONAL_SCHEDULES[:] = []
    return tmp


def seed_my_saved_schedule(*, title: str, date_iso: str, start_time: str = "10:00") -> None:
    """--- 1. 입력 고정: 앱 SQLite에 '내 일정'을 심는다(Nana 조회 대상 + busy_rows의 내 쪽 출처). ---"""
    store_mod.AppSQLiteStore(w5.CONFIG.app_db_path).save_structured_request(
        {"kind": "personal_schedule", "title": title, "date": date_iso, "start_time": start_time}
    )


def seed_fully_busy_day(date_iso: str = "2026-07-08") -> None:
    """--- 1. 입력 고정: 하루를 통째로 막아 '후보가 있을 수 없는' 날을 만든다. ---

    `no_invented_final_slot`의 근거다.

    ⚠️ 처음에는 09:00-18:00만 막았는데 그것으로는 축이 성립하지 않았다(n=5에서 3/5). agent가
    18:00-19:00을 제안했고 그건 **정답이다** — busy가 18시에 끝나므로 19시는 실제로 비어 있고,
    저녁 요청에 workday 경계를 넓히는 것은 `after_hours_request` 축이 요구하는 동작이다.
    즉 실패한 것은 구현이 아니라 이 seed였다. 어떤 시각도 남지 않도록 00:00-23:59로 막는다.
    """
    store = store_mod.AppSQLiteStore(w5.CONFIG.app_db_path)
    store.save_structured_request(
        {"kind": "personal_schedule", "title": "종일 워크숍", "date": date_iso,
         "start_time": "00:00", "end_time": "23:59"}
    )


def seed_my_reference() -> None:
    """--- 1. 입력 고정: Week 4 회귀 케이스가 찾을 개인 참고자료를 심는다. ---"""
    w4.add_personal_reference.invoke(
        {
            "title": "내가 일하는 방식",
            "content": "나는 오전에는 집중 업무를 하고 회의는 오후에 잡는 것을 선호한다.",
            "tags": ["업무", "선호"],
        }
    )


@dataclass
class Case:
    id: str
    text: str
    check: Callable[[Any, list[str]], tuple[bool, str]]
    seed: Callable[[], None] = lambda: None  # rebind 이후 실행할 사전 상태
    critical: bool = False
    ambiguous: bool = False
    # 판정 턴 앞에 실제로 실행할 선행 사용자 발화. 앱과 같은 방식으로 user/assistant 텍스트만
    # history에 쌓아 넘긴다. 단일 턴 eval이 못 보던 축을 연다 — 모델이 **직전 턴의 위임 대상을
    # 그대로 이어받는** 현상은 이력이 있어야만 재현된다.
    context_turns: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- 2. 골든셋
# --- 1군: 메인과제 — 위임 라우팅 (supervisor가 보는 tool은 두 개뿐이라 전부 prompt가 결정한다) ---
def _c_personal_to_nana(out, tools):
    # 개인 일정 조회 → nana_agent. kana_agent로 새면 실패(가이드 line 77).
    # 최소 행동: 위임만 맞고 하위 agent가 아무 tool도 안 부르면 조회를 안 한 것이다.
    inner = set(inner_tools_of(out))
    ok = "nana_agent" in tools and "kana_agent" not in tools and bool(inner & NANA_ONLY_TOOLS)
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)}"


def _c_group_to_kana(out, tools):
    """외부 멤버와의 회의 시간 조율 → kana_agent(가이드 line 83).

    ⚠️ 판정축은 `supervisor_selected_agent`가 아니라 **kana_agent를 불렀는가**다. 초기 판정은
    `supervisor_agent_of(out) == "kana_agent"`였고 1/3으로 실패했는데, 실패한 회차는 Kana 체인
    (collect → find → decide)을 **온전히 돌린 뒤** 저장을 nana_agent에 넘긴 것이었다 —
    "회의 시간 잡아줘"를 저장 요청까지 포함해 읽은 것이고, supervisor prompt가 지시한 혼합 요청
    처리 순서 그대로다. 그런데 `extract_langchain_trace`는 위임 이벤트를 만날 때마다 덮어써서
    **마지막 위임만** 남기므로(week06 line 260-261), 그 정상 동작이 `agent=nana_agent`로 보였다.
    정답의 모양(마지막 위임 대상)을 정해두고 검사하면 valid 구현이 FAIL한다(kanana-conventions §6).
    """
    inner = set(inner_tools_of(out))
    ok = "kana_agent" in tools and bool(inner & KANA_ONLY_TOOLS)
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)}"


def _c_supervisor_delegates_at_all(out, tools):
    # 금지 조건 검사: supervisor가 **직접 답해버리는** 것이 Week 6에서 가장 큰 실패다.
    # 반드시 둘 중 하나를 부른 뒤 그 결과로만 답해야 한다(가이드 line 241).
    delegated = [t for t in tools if t in {"nana_agent", "kana_agent"}]
    return bool(delegated), f"delegated={delegated} tools={tools}"


def _c_no_double_delegation(out, tools):
    # 과교정 방지: 한 요청에 두 하위 agent를 다 부르면 같은 일을 두 번 하거나 출처가 섞인다.
    # 개인 일정만 묻는 요청에서는 nana 하나로 끝나야 한다.
    calls = [t for t in tools if t in {"nana_agent", "kana_agent"}]
    ok = calls == ["nana_agent"]
    return ok, f"delegation_calls={calls}"


def _c_external_member_to_kana(out, tools):
    # 조율이 아닌 '외부 멤버 일정 조회'도 Kana 담당이다(가이드 line 83). Nana로 새면 실패 —
    # 외부 멤버 일정은 앱 DB에 없어서 Nana tool로는 절대 못 찾는다.
    inner = set(inner_tools_of(out))
    ok = "kana_agent" in tools and bool(inner & {"extract_schedules_from_history", "collect_member_schedules"})
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)}"


def _c_external_answer_correct(out, tools):
    # --- 4. 판정축 확장: 위임이 맞았는가가 아니라 fixture의 실제 값이 답변에 나왔는가. ---
    # 위임이 맞아도 하위 agent가 날짜 인자를 틀리면 rows가 비고 "일정이 없습니다"로 오답한다.
    answer = answer_of(out)
    _, _, title = CHULSOO_THU
    return (title in answer), f"answer={answer[:120]!r} inner={sorted(set(inner_tools_of(out)))}"


# --- 2군: 추가 과제 — Kana의 후보 검증 → 최종 결정 체인 ---
def _c_kana_chain_to_decide(out, tools):
    """가이드 line 127-129: 그룹 조율은 (일정 수집) → find_common_available_slots → decide_final_slot으로
    이어져야 한다. 수집만 하고 끝내면 조율이 아니라 조회다.

    ⚠️ **인접 순서가 아니라 선후 관계로 본다.** 처음에는 `ordered[:2] == [find, decide]`로 봤는데
    5/5에서 3/5로 떨어졌고, 실패 노트는 `[collect, find, find, decide]`였다 — find를 두 번 부른 것이다.
    그런데 그건 Kana prompt의 `③-b`("0건이 돌아왔으면 후보를 다시 골라 한 번 더 호출한다")가
    **지시한 동작**이다. 정확한 호출 열을 정답으로 박아두면 내 프롬프트가 유도한 정상 동작이 FAIL한다.
    """
    inner = inner_tools_of(out)
    looked_up = bool(set(inner) & {"collect_member_schedules", "extract_schedules_from_history"})
    ok = False
    if looked_up and "find_common_available_slots" in inner and "decide_final_slot" in inner:
        ok = inner.index("find_common_available_slots") < inner.index("decide_final_slot")
    return ok, f"inner={inner}"


def _c_agent_fills_candidate_slots(out, tools):
    """가이드 line 88-89의 핵심: Python tool이 후보를 계산하지 않는다 → **agent가 candidate_slots를
    직접 채워 넘겨야** 한다. 이 축은 tool 이름으로는 절대 안 잡히고 **호출 인자**로만 잡힌다.

    ⚠️ **첫 호출이 아니라 '한 번이라도 채웠는가'로 본다.** 처음에는 첫 find 호출의 candidate_slots가
    비면 즉시 실패로 봤고 n=5에서 4/5였다. 그런데 Kana prompt의 `③-b`는 0건이 돌아오면 후보를 채워
    **다시 호출하라**고 지시한다 — 첫 호출이 비고 재호출이 성공하면 결과는 정상인데 이 축만 실패했다.
    `kana_chain_to_decide`가 `[collect, find, find, decide]`를 오판한 것과 같은 종류의 불일치다.

    진짜 결함은 '도구에 후보 계산을 떠넘기는 것'이므로, **모든 find 호출이 비어 있을 때** 실패로 본다.
    """
    calls = [args for name, args in inner_tool_calls_with_args(out) if name == "find_common_available_slots"]
    if not calls:
        return False, f"find_common_available_slots 미호출 inner={inner_tools_of(out)}"

    filled = [args.get("candidate_slots") or [] for args in calls]
    nonempty = [slots for slots in filled if slots]
    if not nonempty:
        return False, f"find {len(calls)}회 호출 모두 candidate_slots 비어 있음"

    first = nonempty[0][0] if isinstance(nonempty[0][0], dict) else {}
    missing = [k for k in ("date", "start_time", "end_time") if not first.get(k)]
    return (not missing), f"find {len(calls)}회 중 {len(nonempty)}회 채움 first={first} missing={missing}"


def _c_final_slot_matches_answer(out, tools):
    """가이드 line 129: final_slot_payload가 최종 답변과 일치해야 한다.

    끌어올린 payload와 사용자에게 말한 시각이 다르면 trace가 답변을 못 뒷받침한다.

    ⚠️ 이 축은 어쩔 수 없이 '정답의 모양'을 본다(가이드가 답변과의 일치를 요구한다). 그래서
    표기 변형을 넉넉히 받는다 — baseline 2/3의 실패 회차는 final_slot='2026-07-07 09:00-10:00'에
    답변이 "7일(화) 오전 9시부터 10시까지"였다. **규칙을 지킨 정상 동작인데** 초기 판정이
    'YYYY-MM-DD'와 'M월 D일'만 찾아서 FAIL이 됐다(kanana-conventions §6).
    날짜·시각을 각각 여러 표기로 받고, 둘 다 나와야 통과로 본다.
    """
    payload = final_slot_payload_of(out)
    if not payload or not payload.get("final_slot"):
        return False, f"final_slot_payload={payload}"
    final_slot = str(payload["final_slot"])
    parsed = _parse_final_slot(final_slot)
    if parsed is None:
        return False, f"final_slot 형식이 'YYYY-MM-DD HH:MM-HH:MM'이 아님: {final_slot!r}"
    day, start, _ = parsed
    answer = answer_of(out)

    _, month, day_num = (int(part) for part in day.split("-"))
    day_forms = [day, f"{month}월 {day_num}일", f"{day_num}일", f"{day_num:02d}일"]
    hour, minute = divmod(start, 60)
    time_forms = [f"{hour:02d}:{minute:02d}", f"{hour}시", f"{hour - 12}시" if hour > 12 else f"{hour}시"]

    day_ok = any(form in answer for form in day_forms)
    time_ok = any(form in answer for form in time_forms)
    return (day_ok and time_ok), f"final_slot={final_slot!r} day_ok={day_ok} time_ok={time_ok} answer={answer[:140]!r}"


def _parse_final_slot(final_slot: str) -> tuple[str, int, int] | None:
    """'YYYY-MM-DD HH:MM-HH:MM'을 (날짜, 시작분, 종료분)으로 읽는다. 형식이 다르면 None."""
    try:
        day, span = str(final_slot).split(" ", 1)
        start_text, end_text = span.split("-", 1)
    except ValueError:
        return None
    start = parse_time_minutes(start_text.strip(), -1)
    end = parse_time_minutes(end_text.strip(), -1)
    if start < 0 or end <= start:
        return None
    return day.strip(), start, end


def _c_busy_time_not_proposed(out, tools):
    """양방향의 반대편: 후보를 내놓는가가 아니라 **바쁜 시간을 내놓지 않는가**.

    겹침 판정은 손으로 문자열을 비교하지 않고 `fixed.schedule_decision.busy_rows_overlap`을 재사용한다
    (eval에서 겹침 로직을 재구현하면 정본과 조용히 어긋난다).
    근거 busy_rows는 payload가 남긴 것을 쓰고, 없으면 fixture 사실로 최소 검사한다.
    """
    payload = final_slot_payload_of(out) or {}
    final_slot = str(payload.get("final_slot") or "")
    if not final_slot:
        return False, f"final_slot이 비어 있음 payload_keys={sorted(payload)}"
    parsed = _parse_final_slot(final_slot)
    if parsed is None:
        return False, f"final_slot 형식이 'YYYY-MM-DD HH:MM-HH:MM'이 아님: {final_slot!r}"
    day, start, end = parsed
    rows = payload.get("busy_rows") or []
    if not rows:
        chulsoo_day, chulsoo_start, chulsoo_title = CHULSOO_THU
        rows = [{"member_name": "철수", "date": chulsoo_day, "start_time": chulsoo_start,
                 "end_time": "15:00", "title": chulsoo_title}]
    blockers = busy_rows_overlap(rows, day, start, end)
    return (not blockers), f"final_slot={final_slot!r} blockers={blockers}"


def _c_final_slot_in_requested_week(out, tools):
    """확정된 시각이 **요청한 주 안**에 있고 과거가 아닌가.

    ⚠️ 이 축은 앱 런타임 경로를 직접 돌려보다가 발견해 승격한 것이다. 오늘이 2026-07-06(월)인데
    Kana가 '이번 주'를 2026-06-29~07-05(지난 주)로 계산했다. 그러자 collect가 엉뚱한 주를 조회해
    busy_rows가 0건이 되고, 후보가 전부 과거 날짜인데도 겹칠 것이 없어 전원 통과해
    **2026-06-29를 회의 시각으로 확정 통보**했다.

    기존 축이 왜 못 잡았는지가 이 케이스의 존재 이유다: `busy_time_not_proposed`는 busy_rows가
    비면 fixture row로 대체해 겹침만 보고, `final_slot_matches_answer`는 payload와 답변의 일치만
    본다. 둘 다 통과하면서 답변은 지난 주에 대한 것이었다 — 겹침·일치만으로는 '엉뚱한 주'가 안 보인다.

    ⚠️ **확정한 경우에만** 범위를 따진다. 처음에는 final_slot이 비면 곧바로 실패로 봤는데, 그건
    "확정을 아예 못 했다"는 **다른 결함**이고 이미 `kana_chain_to_decide`(critical, decide_final_slot
    호출을 요구)와 `final_slot_matches_answer`가 가두고 있다. 한 축이 두 가지를 재면 어느 쪽이
    깨졌는지 통과율에서 구분되지 않는다. 여기서는 '과거·엉뚱한 주를 확정 통보하는 것'만 잡는다.
    """
    payload = final_slot_payload_of(out) or {}
    final_slot = str(payload.get("final_slot") or "")
    if not final_slot:
        return True, f"미확정(범위 판정 대상 아님) inner={inner_tools_of(out)}"
    parsed = _parse_final_slot(final_slot)
    if parsed is None:
        return False, f"final_slot 형식 불일치: {final_slot!r}"
    day = parsed[0]
    week_end = (FROZEN_TODAY + timedelta(days=6)).isoformat()  # 2026-07-06(월) ~ 07-12(일)
    ok = TODAY <= day <= week_end
    return ok, f"final_slot={final_slot!r} 허용범위={TODAY}~{week_end}"


def _c_no_past_date_queried(out, tools):
    """위 축의 상류: 조회 범위 자체가 과거로 가지 않았는가.

    final_slot이 안 나온 회차에서도 '엉뚱한 주를 조회했다'를 잡는다 — 확정까지 못 갔다는 이유로
    날짜 계산 오류가 통과율에서 사라지면 안 된다.
    """
    bad: list[str] = []
    for name, args in inner_tool_calls_with_args(out):
        date_from = str(args.get("date_from") or "")
        if date_from and date_from < TODAY:
            bad.append(f"{name}(date_from={date_from})")
    return (not bad), f"과거_조회={bad} today={TODAY}"


def _c_no_invented_final_slot(out, tools):
    """`busy_time_not_proposed`의 반대편: 여유가 **없을 때** 억지로 확정하지 않는가.

    업무시간(09:00-18:00)을 통째로 막아두고 그 하루만 요청한다. 후보가 있을 수 없으므로
    final_slot을 확정했으면 근거 없는 창작이다(가이드 line 102: needs_agent_selection 유지).
    """
    payload = final_slot_payload_of(out) or {}
    final_slot = payload.get("final_slot")
    answer = answer_of(out)
    declared = any(cue in answer for cue in ("확정했", "확정하였", "예약했", "잡았습니다"))
    # 최소 행동: 금지 조건만 두면 **아무 조회도 안 하고 답해도** 만점이 된다. 실제로 찾아본 뒤 없어야 한다.
    looked_up = bool(set(inner_tools_of(out)) & {"collect_member_schedules", "extract_schedules_from_history"})
    ok = looked_up and not final_slot and not declared
    return ok, f"final_slot={final_slot!r} declared={declared} looked_up={looked_up} answer={answer[:120]!r}"


# --- 3군: 하위 agent 역할 경계 (하위 agent는 supervisor prompt를 공유하지 않는다) ---
def _c_nana_declines_group(out, tools):
    """가이드 line 213: Nana는 그룹 조율 요청을 담당이 아니라고 짧게 알린다.

    채널이 다르다 — supervisor를 거치지 않고 `nana_agent` tool을 **직접** 부른다.
    supervisor가 알맞게 라우팅하면 이 상황은 안 생기지만, 하위 prompt가 자기 경계를 아는지는
    별개 축이고 supervisor 라우팅이 흔들릴 때의 안전망이다.

    판정은 금지 행동으로 본다: 외부 멤버 일정을 **지어내면** 실패(Nana에겐 조회 수단이 없다).
    """
    answer = answer_of(out)
    invented = [t for t in ("고객 인터뷰", "디자인 피드백", "QA 리뷰", "릴리즈 회의", "API 연동 실습") if t in answer]
    inner = direct_inner_tools_of(out)
    return (not invented and bool(answer)), f"invented={invented} inner={inner} answer={answer[:140]!r}"


def _c_kana_defers_saving(out, tools):
    """가이드 line 223: 확정된 일정 저장은 Nana 담당이라고 답해야 한다.

    판정은 답변 어휘가 아니라 **관측 상태**로 한다(kanana-conventions §6):
    Kana tool 목록에는 저장 수단이 없으므로, "저장했다"고 말했는데 앱 DB에 row가 없으면 거짓 보고다.

    ⚠️ cue는 **종결형**으로만 잡는다. 처음에는 어간 "저장했"으로 찾았는데, Kana가 규칙을 지켜
    "제가 저장했다고는 말씀드리지 않습니다"라고 답한 회차를 **부정문 안의 어간**에 걸려 FAIL로 오판했다
    (n=5에서 4/5). 어간 매칭은 부정문과 긍정문을 구분하지 못한다.
    """
    answer = answer_of(out)
    declared = any(cue in answer for cue in ("저장했습니다", "저장했어요", "저장하였습니다",
                                             "등록했습니다", "등록했어요", "등록하였습니다", "저장 완료"))
    saved_rows = store_mod.AppSQLiteStore(w5.CONFIG.app_db_path).list_schedules(limit=50)
    lied = declared and not saved_rows
    return (not lied), f"declared={declared} saved_rows={len(saved_rows)} answer={answer[:140]!r}"


# --- 4군: 임의값 금지 + 이전 주차 회귀 ---
def _c_shared_listing_no_askback(out, tools):
    """공유 저장소 전체를 보여달라는 요청은 되묻지 말고 조회해야 한다.

    ⚠️ 멘토 리뷰에서 나온 축이다. 앱에서 `공유 일정 보여줘` 를 넣으면 kana_agent로 위임은 되는데
    이름을 되묻고 도구를 하나도 부르지 않았다(`inner_tool_names: []`).

    원인은 Kana prompt의 "사람 이름이 하나도 없으면 조회하지 않고 되묻는다" 규칙이 **조회 요청에까지**
    적용된 것이다. `list_shared_schedules` 는 필터 없이 호출해도 정상 동작한다(ok=true, rows 18건).

    골든셋에 `list_shared_schedules` 케이스가 아예 없어서 이 경로가 한 번도 측정되지 않았다.
    """
    inner = set(inner_tools_of(out))
    ok = "kana_agent" in tools and "list_shared_schedules" in inner
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)} answer={answer_of(out)[:90]!r}"


def _c_unnamed_listing_default(out, tools):
    """이름 없는 '팀원들 일정' 조회도 기본 조회로 답해야 한다.

    판정 기준으로 `list_shared_schedules` 를 요구하는 것은 **설계 결정을 인코딩한 것**이다.
    `extract_schedules_from_history` 는 `member_names=[]` 면 빈 결과라(week05 계약) 이름 없는
    조회의 기본값이 될 수 없다. 그래서 "되묻지 말고 조회"가 아니라 "어느 도구로 기본 조회할지"가
    실제 결정이었고, 공유 저장소 전체 조회를 기본값으로 골랐다.
    """
    inner = set(inner_tools_of(out))
    ok = "kana_agent" in tools and "list_shared_schedules" in inner
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)} answer={answer_of(out)[:90]!r}"


def _c_no_invented_member(out, tools):
    # kanana-conventions §3: 사용자가 이름을 하나도 말하지 않았는데 member_names에 fixture 멤버를
    # 지어 넣으면 안 된다. 판정은 **하위 agent의 호출 인자**를 본다.
    invented: list[str] = []
    for _, args in inner_tool_calls_with_args(out):
        names = args.get("member_names")
        if isinstance(names, list) and names:
            invented.extend(str(n) for n in names if str(n) != "나")
    # 최소 행동: 위임조차 안 하고 답하면 "이름을 창작하지 않았다"가 우연히 성립한다.
    delegated = delegated_agents_of(tools)
    return (bool(delegated) and not invented), f"delegated={delegated} invented_member_names={invented} inner={inner_tools_of(out)}"


def _c_member_names_not_dropped(out, tools):
    # no_invented_member의 반대편: 창작하지 않는 것만큼 **누락하지 않는 것**도 중요하다.
    # 한 명이라도 빠지면 그 사람 일정이 조용히 빠진 채 조율이 진행된다.
    asked = {"철수", "영희", "민준"}
    passed: set[str] = set()
    for _, args in inner_tool_calls_with_args(out):
        names = args.get("member_names")
        if isinstance(names, list):
            passed |= {str(n) for n in names}
    missing = asked - passed
    return (not missing), f"missing={sorted(missing)} passed={sorted(passed)}"


def _c_chain_not_broken_by_confirm(out, tools):
    """사용자가 **특정 시각을 지목한** 조율 요청에서 체인이 ①에서 끊기지 않는가.

    ⚠️ 앱 실행에서 발견해 승격했다. Kana가 `collect_member_schedules` **하나만** 부르고 멈춰,
    산문으로 "19:00-20:00을 제안합니다. 이 시간으로 확정할까요?"라고 되물었다.
    `find_common_available_slots` 검증도 `decide_final_slot` 기록도 없어 `final_slot_payload`가
    null이었다 — 겹침 판단을 눈대중으로 대신한 것이다.

    기존 축이 못 잡은 이유: `kana_chain_to_decide`는 `저녁` 같은 시간대 조건이 붙은 요청을 쓰지 않았고,
    `after_hours_request`는 widened/produced 중 하나만 있으면 통과시켜 체인 미완을 눈감았다.

    ⚠️ **케이스 문장을 기간형으로 바꿨다.** 처음에는 앱에서 관측한 문장 그대로
    `7월 14일 저녁 7시쯤`을 썼고 2/5였다. 그런데 그 문장은 날짜·시각·길이를 **전부 지정**해서
    "찾을 것"이 없고, 겹침만 확인해 저장하는 것도 타당한 해석이다 — 조율로 강제하는 것은
    가이드가 아니라 **내 의견**이었다. 앱에서 본 결함(①에서 끊고 되묻기)은 기간형으로도 그대로
    재므로, 논쟁적인 부분만 `specific_time_request` 관측 축으로 분리했다.

    판정은 어휘가 아니라 **상태**로 본다: decide까지 갔고 final_slot이 남았는가.
    """
    inner = inner_tools_of(out)
    reached = {"find_common_available_slots", "decide_final_slot"} <= set(inner)
    final_slot = (final_slot_payload_of(out) or {}).get("final_slot")
    return (reached and bool(final_slot)), f"inner={inner} final_slot={final_slot!r}"


def _c_specific_time_request(out, tools):
    """날짜·시각·길이를 **전부 지정**한 요청은 조율인가 저장인가 — 관측 축(non-critical).

    `chain_not_broken_by_confirm`에서 분리해 나온 축이다. 가이드는 "그룹 일정 요청"에 체인을
    요구하지만, 사용자가 이미 시각을 정한 요청을 조율로 강제하는 것은 내 해석이다. 그래서
    체인 완주를 **요구하지 않고**, 논쟁의 여지가 없는 것만 게이트로 둔다:
    **조회 없이 '가능하다'고 말하지 않는가**(근거 규칙). 체인 상태는 note로 관측만 한다.
    """
    inner = inner_tools_of(out)
    looked_up = bool(set(inner) & {"collect_member_schedules", "extract_schedules_from_history"})
    reached_decide = "decide_final_slot" in inner
    final_slot = (final_slot_payload_of(out) or {}).get("final_slot")
    return looked_up, (
        f"looked_up={looked_up} reached_decide={reached_decide} final_slot={final_slot!r} inner={inner}"
    )


def _c_after_hours_request(out, tools):
    """verifier가 올린 설계 리스크 R1: 업무 시간 밖 조율 요청이 조용히 전멸하는가.

    `normalize_llm_candidate_slots`의 업무 시간 게이트는 기본 09:00~18:00이고 탈락 후보는
    **사유 없이** 사라진다(fixed/schedule_decision.py:120). 저녁 요청인데 workday 경계를 넓히지
    않으면 후보가 전부 탈락해 "빈 시간이 없다"로 오답한다 — 실제로는 19시가 비어 있다.

    판정은 금지 조건으로 본다: **포기했으면** 실패. workday를 넓혔거나 final_slot을 냈으면 통과.
    (가이드가 업무 시간 밖 요청 처리를 요구하지 않으므로 non-critical로 관측한다.)
    """
    widened = False
    for name, args in inner_tool_calls_with_args(out):
        if name != "find_common_available_slots":
            continue
        end = str(args.get("workday_end") or "18:00")
        if parse_time_minutes(end, 18 * 60) > 18 * 60:
            widened = True
    produced = bool((final_slot_payload_of(out) or {}).get("final_slot"))
    answer = answer_of(out)
    gave_up = any(cue in answer for cue in ("빈 시간이 없", "가능한 시간이 없", "찾지 못"))
    # ⚠️ 처음에는 `(widened or produced) and not gave_up`이었다. widened만 있어도 통과해서
    #    **체인이 ①에서 끊긴 회차를 눈감았다**(앱에서 발견). 조율 요청이므로 decide까지 가야 한다.
    reached_decide = "decide_final_slot" in inner_tools_of(out)
    ok = reached_decide and produced and not gave_up
    return ok, f"widened={widened} produced={produced} reached_decide={reached_decide} gave_up={gave_up} answer={answer[:120]!r}"


def _c_delegation_not_pingponged(out, tools):
    # tool 호출 최소화(prompt-engineering): 같은 요청에 하위 agent를 3번 이상 왕복하면
    # 하위 agent가 매번 LLM을 한 번씩 더 태우므로 비용이 곱으로 늘어난다.
    calls = [t for t in tools if t in {"nana_agent", "kana_agent"}]
    return (0 < len(calls) <= 2), f"delegation_calls={calls}"


def _c_week5_external_conv_regression(out, tools):
    """이전 주차 회귀: 외부 멤버와 '예전에 나눈 대화'를 찾는 요청은 Kana의 외부 대화 검색으로 가야 한다.

    baseline 0/3의 원인은 라우팅이 아니라 **Kana prompt**였다: 위임은 kana_agent로 맞게 갔는데
    inner가 비었다. Kana에게 직접 물어보니 "주제 명사 한 단어로 알려주세요"라고 되물었다 —
    내가 Week 5 규칙을 압축할 때 넣은 "query에는 주제 명사 한 단어만 넣는다"를 Kana가
    '주제가 없으면 검색할 수 없다'로 읽은 것이다. Week 5 원본에는 그 강제가 없다.
    """
    inner = set(inner_tools_of(out))
    ok = "kana_agent" in tools and "search_previous_conversations" in inner
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)}"


def _c_week4_reference_regression(out, tools):
    # 이전 주차 회귀: 개인 참고자료 질문은 Nana의 Week 4 RAG로 가야 한다.
    inner = set(inner_tools_of(out))
    ok = "nana_agent" in tools and "search_personal_references" in inner
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)}"


def _c_no_context_carryover(out, tools):
    # context_turns로만 재현되는 축: 직전 턴이 kana였다고 개인 일정 요청까지 kana로 이어받으면 실패.
    inner = set(inner_tools_of(out))
    ok = "nana_agent" in tools and "kana_agent" not in tools and not (inner & KANA_ONLY_TOOLS)
    return ok, f"delegated={delegated_agents_of(tools)} inner={sorted(inner)}"


CASES: list[Case] = [
    # --- 메인과제: 위임 라우팅 ---
    Case(
        id="delegate_personal_to_nana",
        text="내가 저장해둔 일정 목록 보여줘.",
        check=_c_personal_to_nana,
        seed=lambda: seed_my_saved_schedule(title="팀 리뷰", date_iso="2026-07-08"),
        critical=True,
    ),
    Case(
        id="delegate_group_to_kana",
        text="철수랑 영희랑 이번 주에 회의 시간 잡아줘. 한 시간이면 돼.",
        check=_c_group_to_kana,
        critical=True,
    ),
    Case(
        id="supervisor_delegates_at_all",
        text="다음 주에 민준이랑 회의할 시간 좀 찾아줘.",
        check=_c_supervisor_delegates_at_all,
        critical=True,
    ),
    Case(
        id="no_double_delegation",
        text="내가 저장해둔 일정 목록만 보여줘.",
        check=_c_no_double_delegation,
        seed=lambda: seed_my_saved_schedule(title="팀 리뷰", date_iso="2026-07-08"),
    ),
    Case(
        id="external_member_to_kana",
        text="철수 이번 주 목요일 일정이 뭐야?",
        check=_c_external_member_to_kana,
        critical=True,
    ),
    Case(
        id="external_answer_correct",
        text="철수 이번 주 목요일 일정이 뭐야?",
        check=_c_external_answer_correct,
    ),
    # --- 추가 과제: 후보 검증 → 최종 결정 체인 ---
    Case(
        id="kana_chain_to_decide",
        text="철수랑 영희랑 이번 주에 한 시간짜리 회의 시간을 정해줘.",
        check=_c_kana_chain_to_decide,
        critical=True,
    ),
    Case(
        id="agent_fills_candidate_slots",
        text="철수랑 영희랑 이번 주에 한 시간짜리 회의 시간을 정해줘.",
        check=_c_agent_fills_candidate_slots,
        critical=True,
    ),
    Case(
        id="final_slot_matches_answer",
        text="철수랑 영희랑 이번 주에 한 시간짜리 회의 시간을 정해줘.",
        check=_c_final_slot_matches_answer,
    ),
    Case(
        id="busy_time_not_proposed",
        text="철수랑 이번 주 목요일에 한 시간 회의할 시간을 정해줘.",
        check=_c_busy_time_not_proposed,
        critical=True,
    ),
    Case(
        # 앱 런타임 경로에서 발견해 승격 — '이번 주'를 지난 주로 계산해 과거 날짜를 확정 통보했다.
        id="final_slot_in_requested_week",
        text="철수랑 영희랑 이번 주에 한 시간짜리 회의 시간을 정해줘.",
        check=_c_final_slot_in_requested_week,
        critical=True,
    ),
    Case(
        # 위 축의 상류: 확정까지 못 간 회차에서도 '엉뚱한 주 조회'를 잡는다.
        id="no_past_date_queried",
        text="철수랑 영희랑 이번 주에 한 시간짜리 회의 시간을 정해줘.",
        check=_c_no_past_date_queried,
        critical=True,
    ),
    Case(
        # 위 케이스의 반대편: 여유가 없을 때 억지로 확정하지 않는가 (kanana-conventions §6)
        id="no_invented_final_slot",
        text="철수랑 7월 8일에 한 시간 회의할 시간을 정해줘.",
        check=_c_no_invented_final_slot,
        seed=lambda: seed_fully_busy_day("2026-07-08"),
        critical=True,
    ),
    # --- 하위 agent 역할 경계 (nana_agent/kana_agent를 직접 부르는 별도 채널) ---
    Case(
        id="nana_declines_group",
        text="철수랑 영희 이번 주 일정을 모아서 공통 가능 시간을 정해줘.",
        check=_c_nana_declines_group,
    ),
    Case(
        id="kana_defers_saving",
        text="철수랑 정한 회의를 내 일정으로 저장해줘.",
        check=_c_kana_defers_saving,
    ),
    # --- 임의값 금지 + 회귀 ---
    Case(
        # 멘토 리뷰 ① — 공유 저장소 전체 조회는 되묻지 않는다.
        id="shared_listing_no_askback",
        text="공유 일정 보여줘",
        check=_c_shared_listing_no_askback,
        critical=True,
    ),
    Case(
        # 멘토 리뷰 ① — 이름 없는 팀원 일정 조회도 기본 조회로 답한다.
        id="unnamed_listing_default",
        text="외부 팀원들 일정 조회해줘",
        check=_c_unnamed_listing_default,
        critical=True,
    ),
    Case(
        # 위 둘의 반대편: 조율 요청은 **여전히** 되물어야 한다 (과교정 방지, kanana-conventions §6)
        id="no_invented_member",
        text="이번 주에 회의할 시간 좀 찾아줘.",
        check=_c_no_invented_member,
        critical=True,
    ),
    Case(
        id="member_names_not_dropped",
        text="철수, 영희, 민준이랑 다음 주에 회의 시간 잡아줘.",
        check=_c_member_names_not_dropped,
    ),
    Case(
        # 앱 실행에서 발견해 승격 — 체인이 ①에서 끊기고 "확정할까요?"로 되물었다.
        # 문장은 기간형이다. 완전 지정 시각 변형은 specific_time_request로 분리했다.
        id="chain_not_broken_by_confirm",
        text="철수랑 영희랑 7월 13일부터 17일 사이 저녁에 한 시간 회식 시간 잡아줘.",
        check=_c_chain_not_broken_by_confirm,
        critical=True,
    ),
    Case(
        # 위 케이스에서 분리한 관측 축 — 완전 지정 시각을 조율로 강제하는 것은 가이드가 아니라 내 해석이다.
        id="specific_time_request",
        text="철수랑 영희랑 7월 14일 저녁 7시쯤에 한 시간 회식 시간 잡아줘.",
        check=_c_specific_time_request,
    ),
    Case(
        # verifier 설계 리스크 R1 — 업무 시간 밖 요청. 가이드 요구사항이 아니라 non-critical로 관측만.
        id="after_hours_request",
        text="철수랑 영희랑 이번 주 저녁 7시쯤에 한 시간 회식 시간 잡아줘.",
        check=_c_after_hours_request,
    ),
    Case(
        id="delegation_not_pingponged",
        text="철수랑 다음 주에 회의 시간 잡아줘.",
        check=_c_delegation_not_pingponged,
    ),
    Case(
        id="week5_external_conv_regression",
        text="철수랑 예전에 나눈 대화 좀 찾아줘.",
        check=_c_week5_external_conv_regression,
    ),
    Case(
        id="week4_reference_regression",
        text="내가 적어둔 일하는 방식 관련 메모 찾아줘.",
        check=_c_week4_reference_regression,
        seed=seed_my_reference,
        critical=True,
    ),
    Case(
        id="no_context_carryover",
        text="그럼 내가 저장해둔 일정 목록도 보여줘.",
        check=_c_no_context_carryover,
        seed=lambda: seed_my_saved_schedule(title="팀 리뷰", date_iso="2026-07-08"),
        context_turns=["철수 이번 주 일정 알려줘."],
    ),
]

# 하위 agent tool을 직접 부르는(supervisor 우회) 케이스 — 채널이 다르므로 별도로 표시한다
DIRECT_SUBAGENT_CASES = {"nana_declines_group": "nana_agent", "kana_defers_saving": "kana_agent"}


# --------------------------------------------------------------------- 결정적 축 (LLM 무관)
def _slots_overlap_filtered_once() -> tuple[bool, str]:
    """걸러야 할 것을 거르는가 — busy와 겹치는 후보는 남아선 안 된다."""
    rebind_temp_dbs()
    busy = [{"member_name": "철수", "date": "2026-07-09", "start_time": "14:00", "end_time": "15:00"}]
    out = m.find_common_available_slots_dict(
        member_names=["철수"], date_from="2026-07-09", date_to="2026-07-09", busy_rows=busy,
        candidate_slots=[{"date": "2026-07-09", "start_time": "14:00", "end_time": "15:00",
                          "duration_minutes": 60, "reason": "겹침"}],
    )
    slots = out.get("candidate_slots")
    return (slots == []), f"candidate_slots={slots}"


def _slots_no_over_filter_once() -> tuple[bool, str]:
    """걸러선 안 될 것을 남기는가 — 과잉 제거도 결함이다 (kanana-conventions §6)."""
    rebind_temp_dbs()
    busy = [{"member_name": "철수", "date": "2026-07-09", "start_time": "14:00", "end_time": "15:00"}]
    out = m.find_common_available_slots_dict(
        member_names=["철수"], date_from="2026-07-09", date_to="2026-07-09", busy_rows=busy,
        candidate_slots=[{"date": "2026-07-09", "start_time": "16:00", "end_time": "17:00",
                          "duration_minutes": 60, "reason": "비어 있음"}],
    )
    slots = out.get("candidate_slots") or []
    ok = len(slots) == 1 and slots[0].get("start_time") == "16:00"
    return ok, f"candidate_slots={slots}"


def _find_no_invented_candidates_once() -> tuple[bool, str]:
    """가이드 line 88-89: agent가 후보를 안 넘겼는데 tool이 후보를 만들어내면 안 된다."""
    rebind_temp_dbs()
    busy = [{"member_name": "철수", "date": "2026-07-09", "start_time": "14:00", "end_time": "15:00"}]
    out = m.find_common_available_slots_dict(
        member_names=["철수"], date_from="2026-07-09", date_to="2026-07-09", busy_rows=busy, candidate_slots=None
    )
    slots = out.get("candidate_slots")
    return (slots == []), f"candidate_slots={slots}"


def _decide_no_auto_pick_once() -> tuple[bool, str]:
    """가이드 line 102: selected_index/selected_slot이 없으면 final_slot을 자동으로 고르지 않는다."""
    payload = json.loads(m.decide_final_slot.invoke({"candidate_slots": [
        {"date": "2026-07-09", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "a"},
        {"date": "2026-07-10", "start_time": "11:00", "end_time": "12:00", "duration_minutes": 60, "reason": "b"},
    ]}))
    ok = payload.get("final_slot") is None and payload.get("needs_agent_selection") is True
    return ok, f"final_slot={payload.get('final_slot')} needs={payload.get('needs_agent_selection')}"


def _decide_keeps_agent_choice_once() -> tuple[bool, str]:
    """반대편: agent가 고른 선택은 그대로 기록돼야 한다(과잉 보류도 결함)."""
    cands = [
        {"date": "2026-07-09", "start_time": "16:00", "end_time": "17:00", "duration_minutes": 60, "reason": "a"},
        {"date": "2026-07-10", "start_time": "11:00", "end_time": "12:00", "duration_minutes": 60, "reason": "b"},
    ]
    payload = json.loads(m.decide_final_slot.invoke({
        "candidate_slots": cands, "selected_index": 1,
        "final_slot": "2026-07-10 11:00-12:00", "needs_agent_selection": False, "reason": "뒤쪽이 낫다",
    }))
    ok = (payload.get("final_slot") == "2026-07-10 11:00-12:00"
          and payload.get("needs_agent_selection") is False
          and bool(payload.get("candidates")))
    return ok, f"payload={ {k: payload.get(k) for k in ('final_slot', 'needs_agent_selection', 'candidates')} }"


def _collect_path_used_once() -> tuple[bool, str]:
    """가이드 line 97: busy_rows=None이면 collect_member_schedules로 rows를 모은다(직접 SQL 금지)."""
    rebind_temp_dbs()
    seed_my_saved_schedule(title="팀 리뷰", date_iso="2026-07-08")
    with conversation_session_scope("eval_conv"):
        out = m.find_common_available_slots_dict(
            member_names=["철수", "영희"], date_from="2026-07-07", date_to="2026-07-17", candidate_slots=None
        )
    rows = out.get("busy_rows") or []
    members = out.get("members") or []
    external = {r.get("member_name") for r in rows} & {"철수", "영희"}
    ok = bool(rows) and bool(external) and "나" in members
    return ok, f"rows={len(rows)} external={sorted(external)} members={members}"


class _FailingCollect:
    """수집이 실패를 payload로 돌려주는 경우를 흉내낸다 (ok=false, rows=[])."""

    def invoke(self, args: dict) -> str:
        return json.dumps(
            {"ok": False, "tool_name": "collect_member_schedules", "error": "external store unavailable", "rows": []},
            ensure_ascii=False,
        )


class _RaisingCollect:
    """수집이 예외를 던지는 경우 — 오늘 실제로 나는 실패 모양이다."""

    def invoke(self, args: dict) -> str:
        raise RuntimeError("external MCP unavailable")


# 철수의 실제 일정(2026-07-09 14:00-15:30)과 겹치는 후보.
# 정상 조회에서는 반드시 걸러지고, 수집이 실패하면 걸러지지 않는다.
_CLASHING_CANDIDATE = {
    "date": "2026-07-09", "start_time": "14:00", "end_time": "15:00",
    "duration_minutes": 60, "reason": "겹치는 줄 모르고 고른 시간",
}


def _collect_failure_not_empty_once() -> tuple[bool, str]:
    """수집 실패를 '바쁜 시간 없음'으로 해석하지 않는가 (멘토 리뷰 축).

    ⚠️ 실패와 빈 결과가 같은 입력이 되면, 실제로 겹치는 시간이 '가능한 시간'으로 통과한다.
    "이번 주"를 지난 주로 계산했을 때와 **같은 실패 모양**이다 — busy_rows가 비면 전원 통과한다.

    두 실패 경로를 모두 본다. soft failure(payload가 ok=false)와 예외 전파 둘 다에서
    후보가 통과해서는 안 되고, 실패가 payload로 드러나야 한다.
    """
    rebind_temp_dbs()
    original = m.collect_member_schedules
    notes: list[str] = []
    try:
        for label, fake in (("soft-failure", _FailingCollect()), ("exception", _RaisingCollect())):
            m.collect_member_schedules = fake
            try:
                payload = m.find_common_available_slots_dict(
                    member_names=["철수", "영희"], date_from="2026-07-07", date_to="2026-07-17",
                    candidate_slots=[_CLASHING_CANDIDATE],
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{label}: 예외 전파 {type(exc).__name__} (실패가 payload로 안 나옴)")
                continue
            if payload.get("candidate_slots"):
                notes.append(f"{label}: 수집 실패인데 후보 {len(payload['candidate_slots'])}건 통과")
            if payload.get("ok") is not False:
                notes.append(f"{label}: ok={payload.get('ok')} (실패가 표시되지 않음)")
    finally:
        m.collect_member_schedules = original
    return (not notes), "; ".join(notes) or "두 실패 경로 모두 후보 통과 없음 + 실패 표시됨"


def _retry_keeps_success_once() -> tuple[bool, str]:
    """재시도가 실패해도 앞선 성공 결과를 잃지 않는가 (멘토 리뷰 축).

    ⚠️ 재시도 자체는 Kana prompt의 `③-b`가 지시한 정상 경로다. 그런데 끌어올리기 조건이
    값이 아니라 **키의 존재**를 보고 있어(`if "final_slot" in content`) None이 성공값을 덮는다.
    바로 아래 `final_decision` 쪽은 truthy 검사라 같은 버그가 없다.

    LLM을 거치지 않고 하위 agent를 주입해 끌어올리기 로직만 결정적으로 잰다.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    success = json.dumps({"final_slot": "2026-07-10 11:00-12:00", "reason": "첫 호출 성공",
                          "candidates": ["2026-07-10 11:00-12:00"], "needs_agent_selection": False}, ensure_ascii=False)
    retry_fail = json.dumps({"final_slot": None, "reason": "재시도 실패",
                             "candidates": [], "needs_agent_selection": True}, ensure_ascii=False)

    class _Fake:
        def invoke(self, payload: dict) -> dict:
            return {"messages": [
                AIMessage(content="", tool_calls=[{"name": "decide_final_slot", "args": {}, "id": "d1"}]),
                ToolMessage(content=success, name="decide_final_slot", tool_call_id="d1"),
                AIMessage(content="", tool_calls=[{"name": "decide_final_slot", "args": {}, "id": "d2"}]),
                ToolMessage(content=retry_fail, name="decide_final_slot", tool_call_id="d2"),
                AIMessage(content="회의 시간을 정했습니다."),
            ]}

    original = m._KANA_SUBAGENT
    try:
        m._KANA_SUBAGENT = _Fake()
        out = json.loads(m.kana_agent.invoke({"query": "회의 시간 정해줘"}))
    finally:
        m._KANA_SUBAGENT = original
    lifted = (out.get("final_slot_payload") or {}).get("final_slot")
    return (lifted == "2026-07-10 11:00-12:00"), f"끌어올린 final_slot={lifted!r} (성공값=2026-07-10 11:00-12:00)"


def check_deterministic(n: int, fn: Callable[[], tuple[bool, str]]) -> dict:
    """LLM 없이 tool/helper를 직접 불러 안전규칙을 n회 단정한다."""
    passed, errors, notes = 0, 0, []
    for _ in range(n):
        try:
            ok, why = fn()
            passed += bool(ok)
            if not ok:
                notes.append(why)
        except Exception as e:  # noqa: BLE001
            errors += 1
            notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
            _record_crash(getattr(fn, "__name__", "deterministic"), e)
    return {"passed": passed, "n": n, "errors": errors, "critical": True, "ambiguous": False, "notes": notes[:2]}


# --------------------------------------------------------------------- 3~5. 실행·집계
def tool_calls_of(out: dict) -> list[str]:
    """supervisor 레벨 tool 호출 — nana_agent / kana_agent 둘뿐이다."""
    return [c["name"] for msg in out.get("messages", []) for c in (getattr(msg, "tool_calls", []) or [])]


def _trace_of(out: dict) -> dict:
    """week06 제공 extract_langchain_trace로 위임 대상/inner tool/final payload를 끌어올린다."""
    try:
        return m.extract_langchain_trace(out) or {}
    except Exception:  # noqa: BLE001
        return {}


def supervisor_agent_of(out: dict) -> str | None:
    """--- 4. 판정축 (a-1): UI가 표시하는 '선택된 하위 agent'. ---

    ⚠️ 라우팅 판정에는 쓰지 않는다. `extract_langchain_trace`가 위임 이벤트마다 덮어써서
    **마지막 위임만** 남기므로(week06 line 260-261), 조율 후 저장처럼 두 번 위임한 정상 동작이
    '엉뚱한 곳으로 갔다'로 보인다. 라우팅은 `delegated_agents_of(tools)`로 본다.
    """
    return _trace_of(out).get("supervisor_selected_agent")


def delegated_agents_of(tools: list[str]) -> list[str]:
    """--- 4. 판정축 (a): supervisor가 호출한 위임 tool 전체(순서 보존). ---"""
    return [name for name in tools if name in {"nana_agent", "kana_agent"}]


def inner_tools_of(out: dict) -> list[str]:
    """--- 4. 판정축 (b): 하위 agent가 그 안에서 무엇을 이어 불렀나. ---

    supervisor의 tool_calls에는 절대 안 나온다. 위임 tool이 반환한 JSON의 inner_tool_names에만 있다.
    """
    return list(_trace_of(out).get("inner_tool_names") or [])


def final_slot_payload_of(out: dict) -> dict | None:
    """--- 4. 판정축 (c): 끌어올린 최종 시간 payload. ---"""
    payload = _trace_of(out).get("final_slot_payload")
    return payload if isinstance(payload, dict) else None


def inner_tool_calls_with_args(out: dict) -> list[tuple[str, dict]]:
    """--- 4. 판정축 확장: 하위 agent의 **호출 인자**. ---

    임의값 금지(kanana-conventions §3)와 "agent가 candidate_slots를 직접 채웠는가"는
    tool 이름 축으로는 절대 안 잡힌다. 위임 tool 결과 안의 trace.events에서 꺼낸다.
    """
    pairs: list[tuple[str, dict]] = []
    for event in _trace_of(out).get("events") or []:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        trace = content.get("trace")
        inner_events = trace.get("events") if isinstance(trace, dict) else trace
        for inner in inner_events or []:
            if isinstance(inner, dict) and inner.get("event") == "tool_call" and inner.get("tool_name"):
                pairs.append((str(inner["tool_name"]), inner.get("arguments") or {}))
    return pairs


def answer_of(out: dict) -> str:
    """--- 4. 판정: 위임 축만으로는 '맞게 위임하고 틀린 답'을 놓친다. ---"""
    messages = out.get("messages", [])
    return str(getattr(messages[-1], "content", "")) if messages else ""


def _direct_subagent_out(tool_name: str, text: str) -> dict:
    """하위 agent tool을 supervisor 우회로 직접 부른 결과를 out 형태로 감싼다.

    반환 JSON의 answer/trace/inner_tool_names를 그대로 판정 함수에 넘길 수 있게
    supervisor 결과와 같은 모양(messages 하나 + trace)으로 만든다.
    """
    tool_obj = getattr(m, tool_name)
    payload = json.loads(tool_obj.invoke({"query": text}))

    class _Msg:
        type = "ai"
        tool_calls: list[dict] = []

        def __init__(self, content: str) -> None:
            self.content = content

    out = {"messages": [_Msg(payload.get("answer") or "")]}
    out["_direct_payload"] = payload  # supervisor를 안 거치므로 _trace_of 대신 이 경로로 본다
    return out


def direct_inner_tools_of(out: dict) -> list[str]:
    """직접 호출 채널에서 하위 agent가 부른 tool 이름."""
    payload = out.get("_direct_payload")
    if isinstance(payload, dict):
        return list(payload.get("inner_tool_names") or [])
    return inner_tools_of(out)


def run(n: int) -> dict[str, dict]:
    # 1. 입력 고정: 고정 시계로 세 agent의 prompt를 다시 조립한다
    m._SUPERVISOR_AGENT = None
    m._NANA_SUBAGENT = None
    m._KANA_SUBAGENT = None
    agent = m.build_week_agent()  # 1. 채널 고정: 실제 앱 경로 (PROXY_TOKEN 필요)
    results: dict[str, dict] = {}
    for case in CASES:
        passed, errors, notes = 0, 0, []
        for _ in range(n):
            rebind_temp_stores()          # 1. 상태 격리
            case.seed()
            try:
                with conversation_session_scope("eval_conv"):
                    if case.id in DIRECT_SUBAGENT_CASES:
                        out = _direct_subagent_out(DIRECT_SUBAGENT_CASES[case.id], case.text)
                    else:
                        history: list[dict[str, str]] = []
                        for prior in case.context_turns:
                            history.append({"role": "user", "content": prior})
                            history.append(
                                {"role": "assistant", "content": answer_of(agent.invoke({"messages": history}))}
                            )
                        history.append({"role": "user", "content": case.text})
                        out = agent.invoke({"messages": history})
                ok, why = case.check(out, tool_calls_of(out))
                passed += bool(ok)
                if not ok:
                    notes.append(why)
            except Exception as e:  # noqa: BLE001
                errors += 1
                notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
                _record_crash(case.id, e)
        results[case.id] = {"passed": passed, "n": n, "errors": errors, "critical": case.critical,
                            "ambiguous": case.ambiguous, "notes": notes[:2]}
        _print_row(case.id, results[case.id])

    # 비-LLM 결정적 안전규칙 — 잘못된 후보를 확정하는 결함은 여기서 통과율로 즉시 드러난다.
    # 거르는 로직은 양방향으로 (kanana-conventions §6)
    for cid, fn in (
        ("slots_overlap_filtered", _slots_overlap_filtered_once),
        ("slots_no_over_filter", _slots_no_over_filter_once),
        ("find_no_invented_candidates", _find_no_invented_candidates_once),
        ("decide_no_auto_pick", _decide_no_auto_pick_once),
        ("decide_keeps_agent_choice", _decide_keeps_agent_choice_once),
        ("collect_path_used", _collect_path_used_once),
        # 멘토 리뷰 ②③ — 실패와 빈 결과의 구분, 재시도 결과 선택 기준
        ("collect_failure_not_empty", _collect_failure_not_empty_once),
        ("retry_keeps_success", _retry_keeps_success_once),
    ):
        results[cid] = check_deterministic(n, fn)
        _print_row(cid, results[cid])
    return results


CRASH_LOG = REPO_ROOT / "evals" / "week06_crashes.log"
_CRASH_SEEN: set[str] = set()


def _record_crash(case_id: str, exc: BaseException) -> None:
    """--- 5. 집계: 크래시의 **전체 traceback**을 파일로 남긴다. ---

    ⚠️ 예전에는 `ERR:{type}:{메시지 60자}` 만 notes에 남겨 스택을 통째로 버렸다. 그래서
    `IntegrityError: UNIQUE constraint failed: schedules.schedule_id` 가 여러 실행에 걸쳐
    반복됐는데도 **어느 프레임에서 터지는지 알 수 없어** 원인 규명에 실패했다(라이브 재현도 6회 실패).

    같은 (예외 타입, 메시지) 조합은 한 번만 적어 로그가 부풀지 않게 한다.
    """
    import traceback

    key = f"{type(exc).__name__}:{exc}"
    if key in _CRASH_SEEN:
        return
    _CRASH_SEEN.add(key)
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{'=' * 72}\n[case] {case_id}\n[error] {key}\n{'-' * 72}\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:  # noqa: BLE001 - 로그 실패가 측정을 막지 않게 한다
        pass


def judged_n(r: dict) -> int:
    """--- 5. 집계: 크래시로 판정하지 못한 회차를 뺀 **실제 판정 횟수**. ---

    ⚠️ 예외로 죽은 회차는 그 축의 동작을 재지 못한 회차다. 분모에 남겨두면 판정 실패와 구분되지 않는다.
    """
    return max(0, r["n"] - r.get("errors", 0))


def _print_row(cid: str, r: dict) -> None:
    judged = judged_n(r)
    mark = "OK " if judged and r["passed"] == judged else ("~~ " if r["passed"] else "XX ")
    tag = " [critical]" if r["critical"] else (" [ambiguous]" if r["ambiguous"] else "")
    err = f" (크래시 {r['errors']}회 제외)" if r.get("errors") else ""
    note = f"   {r['notes'][0]}" if r["notes"] else ""
    print(f"  {mark}{cid:30} {r['passed']}/{judged}{err}{tag}{note}")


# --------------------------------------------------------------------- 6~7. 비교·게이트
def gate(results: dict[str, dict], pass_ratio: float, max_errors: int = 0) -> tuple[bool, list[str]]:
    """--- 7. 게이트: 크래시를 **자기 이름으로** 실패시킨다. ---

    ⚠️ 이전에는 예외 회차가 그 축의 동작 실패로 집계됐다. `IntegrityError`(fixed/app_store.py,
    원인 미규명)가 실행마다 **무작위 축에** 꽂히는 바람에, 매번 다른 축이 회귀한 것처럼 보였다
    — 실측에서 member_names_not_dropped, after_hours_request, kana_chain_to_decide,
    agent_fills_candidate_slots가 번갈아 걸렸다.

    이제 크래시는 축 분모에서 빠지고 `_crashes` 라는 별도 항목으로 게이트를 실패시킨다.
    숨기는 것이 아니라 **엉뚱한 축이 아니라 자기 이름으로** 드러나게 하는 것이다.
    `--max-errors` 로 알려진 크래시를 명시적으로 인정할 수 있고, 기본값은 0(무관용)이다.
    """
    fails = []
    for cid, r in results.items():
        if r.get("ambiguous"):
            continue
        judged = judged_n(r)
        if judged == 0:
            fails.append(f"{cid}: 전 회차가 크래시로 판정 불가 ({r['n']}회)")
            continue
        if r["critical"] and r["passed"] < judged:
            fails.append(f"{cid}: critical {r['passed']}/{judged}")
        elif not r["critical"] and r["passed"] < judged * pass_ratio:
            fails.append(f"{cid}: {r['passed']}/{judged} < {pass_ratio:.0%}")

    total_errors = sum(r.get("errors", 0) for r in results.values())
    if total_errors > max_errors:
        hit = sorted(cid for cid, r in results.items() if r.get("errors"))
        fails.append(f"_crashes: 실행 중 크래시 {total_errors}회 (허용 {max_errors}회) — 걸린 축: {', '.join(hit)}")
    return (not fails), fails


def compare(cur: dict, base_path: Path) -> None:
    """--- 6. 비교: baseline과의 diff. ---

    ⚠️ **통과율(비율)로 비교한다.** 처음에는 통과 횟수를 그대로 비교했는데, n=3 baseline과 n=5 실행을
    비교하자 실제로는 그대로인 축 26개가 전부 "↑ 3 -> 5"로 찍혀 **개선으로 오독**됐다.
    n이 다르면 횟수는 비교 가능한 값이 아니다. n이 바뀐 경우는 헤더에 명시해 경고한다.
    """
    base_doc = json.loads(base_path.read_text(encoding="utf-8"))
    base = base_doc["results"]
    base_n, cur_n = base_doc.get("n"), next(iter(cur.values()))["n"] if cur else None
    print("\n=== baseline 대비 변화 ===")
    if base_n != cur_n:
        print(f"  ⚠️ n이 다르다 (baseline n={base_n} vs 이번 n={cur_n}) — 통과율로 비교한다")
    changed = False
    for cid, r in cur.items():
        b = base.get(cid)
        # 크래시로 판정하지 못한 회차는 양쪽 모두 분모에서 뺀다.
        cur_judged = judged_n(r)
        cur_ratio = (r["passed"] / cur_judged) if cur_judged else 0.0
        if b is None:
            print(f"  + {cid}: (신규) {r['passed']}/{cur_judged}"); changed = True
            continue
        base_judged = judged_n(b)
        base_ratio = (b["passed"] / base_judged) if base_judged else 0.0
        if abs(base_ratio - cur_ratio) < 1e-9:
            continue
        arrow = "↑" if cur_ratio > base_ratio else "↓"
        print(f"  {arrow} {cid}: {b['passed']}/{base_judged} ({base_ratio:.0%}) -> {r['passed']}/{cur_judged} ({cur_ratio:.0%})")
        changed = True
    if not changed:
        print("  (변화 없음)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="케이스당 반복 횟수")
    ap.add_argument("--pass-ratio", type=float, default=0.66, help="non-critical 합격 통과율")
    ap.add_argument("--save", type=Path, help="결과를 baseline JSON으로 저장")
    ap.add_argument("--baseline", type=Path, help="이 baseline과 비교")
    ap.add_argument("--max-errors", type=int, default=0,
                    help="허용할 크래시 횟수. 기본 0(무관용). 알려진 크래시를 명시적으로 인정할 때만 올린다")
    args = ap.parse_args()

    if not CONFIG.has_openai_key:
        print("SKIP: PROXY_TOKEN 없음 — Week 6 eval은 supervisor/하위 agent 경로(및 Week 4 임베딩)가 필요하다.")
        print("      키 없는 결정적 계약 검증은 `verify-week6` skill이 담당한다.")
        return 0

    print(f"채널=build_week_agent() (supervisor) | 오늘={TODAY} | N={args.n}\n")
    results = run(args.n)

    total = sum(r["passed"] for r in results.values())
    denom = sum(judged_n(r) for r in results.values())
    total_errors = sum(r.get("errors", 0) for r in results.values())
    print(f"\n총 통과: {total}/{denom}" + (f"  (크래시 {total_errors}회는 분모에서 제외)" if total_errors else ""))

    if args.baseline and args.baseline.exists():
        compare(results, args.baseline)

    ok, fails = gate(results, args.pass_ratio, args.max_errors)
    print("\n=== 게이트 ===")
    if ok:
        print("  PASS — 모든 critical 만점 + non-critical 임계 충족" +
              (f" (크래시 {total_errors}회 인정됨)" if total_errors else ""))
    else:
        print("  FAIL")
        for f in fails:
            print(f"    - {f}")

    if args.save:
        args.save.write_text(
            json.dumps({"today": TODAY, "n": args.n, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nbaseline 저장: {args.save}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
