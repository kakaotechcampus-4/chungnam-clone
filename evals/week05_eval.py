"""Week 5 외부 MCP 라우팅 + 두 출처 일정 통합 재현 가능 eval.

Week 4 eval(evals/week04_eval.py)의 7단계 골격을 그대로 이식하되, Week 5 맥락
(질문 성격 → 외부 대화 검색 / 대화 로드 / 외부 일정 추출 / 공유 저장소 조회 / 두 출처 통합)에 맞춘다.

  1. 입력 고정   — 시계·상태(temp 앱 SQLite + temp 외부 SQLite + temp ChromaDB)·**호출 채널**을 못 박는다
  2. 검사 항목   — CASES 골든셋(MCP tool 라우팅 + 답변 정확성 + 과교정 방지 + 이전 주차 회귀)
  3. 반복        — --n (기본 3)
  4. 판정        — 세 축: (a) tool 호출 목록(어느 MCP wrapper를 골랐나)
                          (b) 최종 답변 본문(맞는 tool을 부르고도 틀린 답을 내는가)
                          (c) 중복 제거·대화 범위 격리는 helper 직접 단정(LLM 무관)
  5. 집계        — 케이스별 통과율 n/N
  6. 비교        — --baseline out.json 저장 / 다음 실행과 diff
  7. 게이트      — critical 케이스 1회 실패 = 전체 실패, non-zero exit

⚠️ Week 5 핵심은 "외부 데이터는 직접 SQL이 아니라 MCP wrapper tool로 접근"이다. 그래서 판정축은
tool_calls_of(out) — 질문 성격에 맞는 wrapper(search_previous_conversations /
load_conversation_messages / extract_schedules_from_history / list_shared_schedules /
collect_member_schedules)를 골랐는가 — 이다. build_week05_agent()(실제 앱)로만 잰다.

⚠️ 시계를 2026-07-06(월)로 고정한다. 외부 실습 fixture가 2026-07-07 ~ 2026-07-17에 seed되어 있어
(fixed/external_people_store.py:24-26 JULY_PRACTICE_*), 이 날짜여야 "이번 주/다음 주" 질문이
seed 데이터에 걸린다. 시계가 어긋나면 라우팅이 맞아도 rows가 비어 전부 오답이 된다.

⚠️ 상태 격리가 핵심이다. 반복마다 새 temp dir을 만들어 **네 경로**를 모두 돌린다:
  (a) week05 모듈 CONFIG      — _personal_schedules_for_current_scope()가 여는 앱 DB
  (b) week03/week04 모듈 전역  — 누적된 Week 1-4 tool이 쓰는 앱 DB / ChromaDB
  (c) KANANA_EXTERNAL_DB_PATH — MCP subprocess가 읽는 외부 DB (fixed/mcp_client.py:85-87)
  (d) PERSONAL_SCHEDULES      — Week 1 인메모리 리스트(모듈 전역이라 반복 사이에 샌다)
(c)를 빠뜨리면 eval이 사용자 실 외부 DB에 공유 일정 row를 쓰고 지운다.
(d)를 빠뜨리면 앞 반복의 임시 일정이 뒤 반복 rows에 섞여 중복 제거 케이스가 거짓 통과한다.

⚠️ Week 4 tool이 누적되어 있어 ChromaDB 임베딩 경로가 살아 있다 → 이 eval은 키가 있어야 돈다.
키가 없으면 즉시 SKIP(exit 0)한다. (계약 검증은 키 없이 `verify-week5` skill이 담당한다.)

이 파일은 `student_parts/`·`fixed/`를 **import만** 한다. 과제 코드는 수정하지 않는다.

실행:
  uv run python -X utf8 evals/week05_eval.py --n 3
  uv run python -X utf8 evals/week05_eval.py --n 5 --save evals/week05_baseline.json
  uv run python -X utf8 evals/week05_eval.py --n 3 --baseline evals/week05_baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import date
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
from fixed.session_scope import conversation_session_scope  # noqa: E402
import fixed.app_store as store_mod  # noqa: E402
import student_parts.week01_wake_up_nana as w1  # noqa: E402
import student_parts.week03_build_nanas_logbook as w3  # noqa: E402
import student_parts.week04_retrieve_nanas_memory as w4  # noqa: E402
import student_parts.week05_load_kanas_past_conversations as m  # noqa: E402

TODAY = rc.current_app_date_iso()  # 2026-07-06

# 외부 실습 fixture에서 골라 쓰는 확인용 사실 (fixed/external_people_store.py:65-84)
CHULSOO_THU = ("2026-07-09", "14:00", "고객 인터뷰")   # 철수 · 이번 주 목요일
YOUNGHEE_TUE = ("2026-07-07", "13:00", "디자인 피드백")  # 영희 · 이번 주 화요일

W5_MCP_TOOLS = {
    "search_previous_conversations",
    "load_conversation_messages",
    "extract_schedules_from_history",
    "list_shared_schedules",
    "collect_member_schedules",
    "create_shared_schedule",
    "delete_shared_schedule",
}


# --------------------------------------------------------------------- 상태/store 헬퍼
def rebind_temp_stores() -> Path:
    """--- 1. 상태 격리: 반복마다 새 temp 앱 DB / 외부 DB / ChromaDB로 전 경로를 재바인딩한다. ---

    week04 eval의 rebind_temp_stores()에 **외부 DB와 Week 1 인메모리 리스트**를 더한 것이다.
    Week 5 tool은 모듈 전역 store를 들고 있지 않고 호출 시점에
      - `AppSQLiteStore(CONFIG.app_db_path)` (week05 모듈 전역 CONFIG)
      - MCP subprocess (`KANANA_EXTERNAL_DB_PATH` 환경변수)
    를 새로 열기 때문에, 전역 CONFIG와 환경변수만 바꾸면 모든 Week 5 경로에 반영된다.

    temp 외부 DB는 첫 접근 때 ExternalPeopleSQLiteStore가 스스로 July 실습 fixture를
    seed하므로(fixed/external_people_store.py) 외부 멤버 데이터는 별도 seed가 필요 없다.
    """
    tmp = Path(tempfile.mkdtemp())
    temp_config = replace(
        CONFIG,
        app_db_path=tmp / "app.sqlite3",
        external_db_path=tmp / "external.sqlite3",
        chroma_dir=tmp / "chroma",
    )
    # (c) MCP subprocess가 읽는 외부 DB — 첫 tool 호출 전에 세팅해야 한다
    os.environ["KANANA_EXTERNAL_DB_PATH"] = str(tmp / "external.sqlite3")
    # (a) Week 5 tool이 여는 앱 DB
    m.CONFIG = temp_config
    # (b) 누적된 Week 1-4 tool 경로
    w3.CONFIG = temp_config
    w4.CONFIG = temp_config
    w4.SQLITE_STORE = store_mod.AppSQLiteStore(tmp / "app.sqlite3")
    w4.REFERENCE_STORE = PersonalReferenceStore(tmp / "chroma")  # seed됨
    w4.CONVERSATION_RAG_STORE = ConversationRAGStore(tmp / "chroma")
    # (d) Week 1 인메모리 임시 일정 — 리스트 객체는 유지하고 내용만 비운다
    w1.PERSONAL_SCHEDULES[:] = []
    return tmp


def seed_my_saved_schedule(*, title: str, date_iso: str, start_time: str = "10:00") -> None:
    """--- 1. 입력 고정: 앱 SQLite에 '내 일정'을 심는다(collect_member_schedules의 내 쪽 출처). ---"""
    store_mod.AppSQLiteStore(m.CONFIG.app_db_path).save_structured_request(
        {"kind": "personal_schedule", "title": title, "date": date_iso, "start_time": start_time}
    )


_SHARED_ROWS_BEFORE = 0


def _shared_row_count() -> int:
    """7월 공유 저장소 row 수. 조회 요청의 부작용(쓰기)을 재는 데 쓴다."""
    payload = json.loads(
        m.list_shared_schedules.invoke(
            {"date_from": "2026-07-01", "date_to": "2026-07-31", "limit": 200}
        )
    )
    return len(payload.get("rows", []))


def snapshot_shared_rows() -> None:
    """--- 1. 입력 고정: 조회 전 공유 저장소 상태를 기록해 사후 비교한다. ---"""
    global _SHARED_ROWS_BEFORE
    _SHARED_ROWS_BEFORE = _shared_row_count()


def seed_shared_row() -> None:
    """--- 1. 입력 고정: 삭제 라우팅 케이스가 지울 대상 row를 미리 등록한다. ---"""
    m.create_shared_schedule.invoke(
        {
            "member_name": "지훈",
            "title": "릴리즈 점검",
            "date": "2026-07-20",
            "start_time": "15:00",
            "end_time": "16:00",
            "source_conversation_id": "eval:shared:del",
            "schedule_id": "eval_del_1",
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
    # history에 쌓아 넘긴다(fixed/agent_runtime.py:80-88). 단일 턴 eval이 못 보던 축을 연다 —
    # 모델이 **직전 턴의 tool 패턴을 그대로 이어받는** 현상은 이력이 있어야만 재현된다.
    context_turns: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- 2. 골든셋
def _c_external_member_routing(out, tools):
    # 외부 팀원의 일정 질문 → 외부 MCP 일정 경로(추출 또는 통합수집)로 가야 한다.
    # Week 3 저장 일정 tool(내 일정)로 새면 실패 — 외부 멤버 일정은 앱 DB에 없다.
    ok = bool({"extract_schedules_from_history", "collect_member_schedules"} & set(tools))
    return ok, f"tools={tools}"


def _c_external_answer_correct(out, tools):
    # --- 4. 판정축 확장: tool을 골랐는가가 아니라 fixture의 실제 값이 답변에 나왔는가. ---
    # 라우팅이 맞아도 날짜 인자를 엉뚱하게 넣으면 rows가 비고 "일정이 없습니다"로 오답한다.
    # 그런데도 tool 이름 판정만 보면 external_member_routing은 PASS로 집계된다.
    answer = answer_of(out)
    _, _, title = CHULSOO_THU
    ok = title in answer
    return ok, f"answer={answer[:110]!r} tools={tools}"


def _c_collect_both_sources(out, tools):
    # Week 5 핵심: 나 + 외부 멤버를 한 rows로 모으는 요청은 collect_member_schedules로 가야 한다.
    # Week 6 공통 가능 시간(추가 과제)이 이 tool의 rows를 busy_rows 근거로 쓴다(가이드 line 99).
    ok = "collect_member_schedules" in tools
    return ok, f"tools={tools}"


def _c_collect_answer_has_mine(out, tools):
    # 통합 수집인데 내 일정이 답변에서 빠지면 조율 근거가 반쪽이다.
    # (seed된 내 일정 제목이 답변에 나오는지로 잰다.)
    answer = answer_of(out)
    ok = "팀 리뷰" in answer
    return ok, f"answer={answer[:110]!r} tools={tools}"


def _c_previous_conversation_search(out, tools):
    # 외부 멤버와 '예전에 나눈 대화' 자체를 찾는 질문 → 외부 대화 검색.
    # Week 4 대화 RAG(search_conversation_messages)는 **내 앱 대화**용이라 외부 멤버 대화를 못 찾는다.
    ok = "search_previous_conversations" in tools
    return ok, f"tools={tools}"


def _c_load_full_conversation(out, tools):
    # 특정 외부 대화의 '전체 내용'을 원하면 검색 결과 conversation_id로 메시지를 로드해야 한다.
    # 검색만 하고 끝내면 발췌만 남는다(가이드 line 77-79).
    ok = "load_conversation_messages" in tools
    return ok, f"tools={tools}"


def _c_shared_store_listing(out, tools):
    # 공유 일정 '저장소에 등록된' row를 확인하는 질문 → list_shared_schedules(가이드 line 88-91).
    ok = "list_shared_schedules" in tools
    return ok, f"tools={tools}"


def _c_personal_only_guard(out, tools):
    # 과교정 방지: 내 일정만 묻는 질문에 외부 MCP tool을 부르면 안 된다.
    # Week 5 tool 7개를 추가한 뒤 "일정" 단어만 보고 전부 MCP로 새는 회귀를 잡는다.
    leaked = W5_MCP_TOOLS & set(tools)
    return (not leaked), f"tools={tools} leaked={sorted(leaked)}"


def _c_no_invented_member(out, tools):
    # kanana-conventions 핵심 규칙(임의값 금지): 사용자가 이름을 하나도 말하지 않았는데
    # member_names에 fixture 멤버를 지어 넣으면 안 된다. 되묻거나 필터 없이 조회해야 한다.
    # 판정은 tool 이름이 아니라 **호출 인자**를 본다 — 라우팅이 맞아도 값을 창작하면 실패다.
    invented = []
    for args in tool_args_of(out):
        names = args.get("member_names")
        if isinstance(names, list) and names:
            invented.extend(names)
    return (not invented), f"tools={tools} invented_member_names={invented}"


def _c_shared_create(out, tools):
    # 추가 과제 동작축: 공유 저장소 등록 요청 → create_shared_schedule이 실제로 불리고
    # 외부 DB에 row가 남는다. (tool 이름만이 아니라 외부 DB 상태로 확인)
    #
    # ⚠️ member_name으로 필터하지 않는다. "지훈이랑 릴리즈 점검하는 일정"은 '나'의 일정(참석자 지훈)으로도,
    # '지훈'의 일정으로도 등록하는 게 타당하고 가이드는 어느 쪽도 못박지 않았다. 실제로 agent는
    # member_name="나"로 등록했는데, 초기 check가 member_names=["지훈"]으로 좁혀 잡는 바람에
    # 정상 구현을 FAIL로 오판했다. 판정축은 **그 날짜에 row가 생겼는가**로 둔다.
    if "create_shared_schedule" not in tools:
        return False, f"tools={tools}"
    listed = json.loads(
        m.list_shared_schedules.invoke({"date_from": "2026-07-20", "date_to": "2026-07-20"})
    )
    rows = listed.get("rows", [])
    return bool(rows), f"tools={tools} shared_rows={rows}"


def _c_week4_reference_regression(out, tools):
    # 이전 주차 회귀: 개인 참고자료 질문은 여전히 Week 4 RAG로 가야 한다.
    ok = "search_personal_references" in tools and not (W5_MCP_TOOLS & set(tools))
    return ok, f"tools={tools}"


# --- 1군: 계약·프롬프트 지시 중 한 번도 측정되지 않았던 축 ---
def _c_delete_shared_routing(out, tools):
    # 추가 과제의 나머지 절반. verify 7단계는 tool을 직접 부르므로 'LLM이 삭제를 그 tool로 보내는가'는
    # 그동안 전혀 측정되지 않았다. 삭제는 데이터 파괴형이라 오라우팅 비용이 등록보다 크다.
    if "delete_shared_schedule" not in tools:
        return False, f"tools={tools}"
    left = json.loads(
        m.list_shared_schedules.invoke({"date_from": "2026-07-20", "date_to": "2026-07-20"})
    ).get("rows", [])
    return not left, f"tools={tools} 남은rows={left}"


def _c_extract_not_collect(out, tools):
    # 프롬프트는 extract(외부만)와 collect(나+팀원)를 구분하라고 지시하는데,
    # external_member_routing이 둘 중 아무거나 인정해서 그 구분은 한 번도 안 재였다.
    ok = "extract_schedules_from_history" in tools and "collect_member_schedules" not in tools
    return ok, f"tools={tools}"


def _c_no_double_collect(out, tools):
    # 프롬프트: "collect_member_schedules를 부르면 extract_schedules_from_history를 따로 중복 호출하지 않는다."
    # 명시 지시인데 미측정이었다(planner가 제안했으나 초기 골든셋에서 빠졌다).
    if "collect_member_schedules" not in tools:
        return False, f"collect 미호출 tools={tools}"
    ok = "extract_schedules_from_history" not in tools
    return ok, f"tools={tools}"


def _c_week6_boundary(out, tools):
    """프롬프트: "Week 5의 역할은 바쁜 시간을 모아 정리하는 데까지다. 최종 회의 시각 확정은 하지 않는다."

    ⚠️ 초기 판정은 hedge 어휘 목록("후보"/"가능"/"괜찮"…)을 찾는 방식이었고 0/3으로 전부 실패했다.
    그런데 실제 답변은 "이 시간을 피해서 회의 일정을 잡으면 좋을 것 같습니다"로 **규칙을 지키고 있었다** —
    내 cue 목록에 그 표현이 없었을 뿐이다. 긍정 어휘를 열거하는 판정은 valid 구현을 FAIL시킨다.

    그래서 **금지된 행동을 직접 본다**: 회의를 확정 통보하거나 공유 저장소에 등록해버리는 것.
    이쪽이 프롬프트가 실제로 금지한 것과 1:1로 대응한다.
    """
    answer = answer_of(out)
    registered = {"create_shared_schedule"} & set(tools)
    declared = any(cue in answer for cue in ("확정했", "확정하였", "예약했", "등록했", "등록하였", "잡았습니다"))
    ok = not registered and not declared
    return ok, f"registered={sorted(registered)} declared={declared} answer={answer[:200]!r}"


def _c_search_member_names_arg(out, tools):
    """프롬프트: "사람 이름은 query가 아니라 member_names로 넘긴다."

    ⚠️ 판정축은 **query 오염 금지** 하나다. 초기 판정은 여기에 "member_names를 반드시 채운다"까지
    묶어서 2/3이 됐는데, 실패한 1회는 `{'query': 'QA 리뷰'}`로 이름을 query에 넣지 **않았고**
    멤버 필터만 생략했다 — 규칙 위반이 아니다(필터 없이 전체 검색해도 대화를 찾는다).

    이름이 query에 섞이면 저장소가 문자열을 통째로 대조해 0건이 나오는 실제 결함으로 이어지므로
    그 축만 게이트로 삼고, member_names 사용 여부는 note로 관측만 한다.
    """
    for name, args in tool_calls_with_args(out):
        if name != "search_previous_conversations":
            continue
        query = str(args.get("query") or "")
        names = args.get("member_names") or []
        return ("철수" not in query), f"query={query!r} member_names={names}"
    return False, f"search 미호출 tools={tools}"


# --- 2군: 이미 있는 축의 '반대편' (kanana-conventions §6 양방향 검사) ---
def _c_no_hallucinated_schedule(out, tools):
    # external_answer_correct의 반대편: '있는 걸 말하는가'가 아니라 '없는 걸 지어내는가'.
    # '길동'은 외부 fixture에 없는 멤버라 rows가 비어야 하고, 답변은 없다고 말해야 한다.
    answer = answer_of(out)
    invented = [t for t in ("API 연동 실습", "고객 인터뷰", "QA 리뷰", "디자인 피드백", "릴리즈 회의") if t in answer]
    says_none = any(cue in answer for cue in ("없", "찾지 못", "확인되지", "조회되지"))
    return (not invented and says_none), f"answer={answer[:120]!r} invented={invented}"


def _c_member_names_not_dropped(out, tools):
    # no_invented_member의 반대편: 이름을 창작하지 않는 것만큼 **누락하지 않는 것**도 중요하다.
    # 한 명이라도 빠지면 그 사람 일정이 조용히 사라진 채로 조율이 진행된다.
    asked = {"철수", "영희", "민준"}
    passed_names: set[str] = set()
    for _, args in tool_calls_with_args(out):
        names = args.get("member_names")
        if isinstance(names, list):
            passed_names |= {str(n) for n in names}
    missing = asked - passed_names
    return not missing, f"missing={sorted(missing)} passed={sorted(passed_names)}"


def _c_read_only_no_side_effect(out, tools):
    # shared_create의 반대편: 조회 요청이 공유 저장소를 **건드리지 않아야** 한다.
    # 쓰기 tool 호출 여부와 실제 row 수 변화를 함께 본다(이름만 보면 놓친다).
    wrote = {"create_shared_schedule", "delete_shared_schedule"} & set(tools)
    after = _shared_row_count()
    ok = not wrote and after == _SHARED_ROWS_BEFORE
    return ok, f"wrote={sorted(wrote)} rows {_SHARED_ROWS_BEFORE}->{after}"


def _c_unspecified_period(out, tools):
    """앱 탐색에서 승격: 기간이 명시되지 않은 질문에 조회 범위를 하루로 좁혀 오답하는가.

    관측된 실패(탐색 시나리오 5): "내가 저장한 일정이랑 철수 일정 비교해줘" →
    `extract_schedules_from_history{date_from:"2026-07-06", date_to:"2026-07-06"}` (오늘 하루만) →
    rows가 비자 "철수의 일정이 현재 없습니다"라고 **단정**. 실제로는 07-07·07-09·07-15에 있다.

    판정은 금지 조건으로 둔다 — 일정이 있는 멤버를 두고 '없다'고 답하는 것.
    (인자 범위는 note로만 남긴다. 어떤 범위를 쓰든 사실을 맞히면 통과다.)
    """
    answer = answer_of(out)
    titles = [t for t in ("API 연동 실습", "고객 인터뷰", "QA 리뷰") if t in answer]
    ranges = [
        (a.get("date_from"), a.get("date_to"))
        for n, a in tool_calls_with_args(out)
        if n in {"extract_schedules_from_history", "collect_member_schedules"}
    ]
    return bool(titles), f"titles={titles} ranges={ranges} answer={answer[:140]!r}"


def _c_collect_without_cue(out, tools):
    """앱 탐색에서 승격: 조율 의도 단서 없이 "다 모아줘"만 있을 때도 collect로 가는가.

    관측된 실패(앱 시나리오 5): "나랑 철수, 영희 7월 7일부터 17일까지 일정 다 모아줘" →
    collect_member_schedules를 **부르지 않고** personal_list_saved_schedules +
    extract_schedules_from_history 2회로 LLM이 직접 병합했다.

    collect_both_sources는 "…회의 시간 잡으려고 해"라는 조율 단서가 있어 5/5로 통과했다.
    단서가 빠지면 라우팅이 무너진다 — 그러면 가이드 :99가 지목한 Week 6 busy_rows 연결 지점이
    끊기고, 통합 6키 rows 대신 서로 키 구조가 다른 결과를 LLM이 산문으로 합치게 된다.

    판정은 금지 조건 — '수동 병합 조합으로 처리했는가'.
    """
    used_collect = "collect_member_schedules" in tools
    manual_merge = "extract_schedules_from_history" in tools and bool(
        {"personal_list_saved_schedules", "personal_list_schedules"} & set(tools)
    )
    return (used_collect and not manual_merge), f"tools={tools}"


def _c_own_conversation_not_external(out, tools):
    """앱 탐색에서 승격: 내 앱 대화를 묻는데 외부 멤버 대화 검색으로 새는가.

    관측된 실패(앱 시나리오 3): 철수 일정 얘기를 몇 턴 한 뒤 "아까 우리가 무슨 얘기 했지?" →
    `search_previous_conversations{query:"얘기"}` → rows 0건 →
    "'얘기'라는 단어가 포함된 이전 대화 기록은 없습니다"라는 무의미한 답.

    출처 분리의 **반대 방향** 실패다. 기존 축들은 "남의 일정을 내 도구로 찾는가"만 막았고,
    "내 대화를 외부 도구로 찾는가"는 비어 있었다. 내 앱 대화는 Week 4
    search_conversation_messages 담당이다(search_previous_conversations는 외부 멤버 전용).

    판정은 금지 조건 — 외부 대화 검색 tool을 부르는 것 자체.
    (eval은 앱과 달리 대화를 DB에 저장하지 않으므로 검색 '결과'가 아니라 '라우팅'만 잰다.)
    """
    ok = "search_previous_conversations" not in tools
    return ok, f"tools={tools} args={[a for n, a in tool_calls_with_args(out) if n == 'search_previous_conversations']}"


CASES: list[Case] = [
    # --- 외부 멤버 일정 라우팅 ---
    Case(
        "external_member_routing",
        "철수랑 영희 이번 주 일정 좀 알려줘",
        _c_external_member_routing,
        critical=True,
    ),
    # --- 답변 정확성 — 같은 축의 질문이지만 판정은 '최종 답변'이다 ---
    Case(
        "external_answer_correct",
        "철수 이번 주 목요일에 뭐 있어?",
        _c_external_answer_correct,
        critical=True,
    ),
    # --- 두 출처 통합 (Week 5 핵심 · Week 6 연결 지점) ---
    Case(
        "collect_both_sources",
        "나랑 철수, 영희 이번 주 일정 다 모아줘. 회의 시간 잡으려고 해",
        _c_collect_both_sources,
        seed=lambda: seed_my_saved_schedule(title="팀 리뷰", date_iso="2026-07-08", start_time="14:00"),
        critical=True,
    ),
    Case(
        "collect_answer_has_mine",
        "나랑 철수, 영희 이번 주 일정 다 모아줘. 회의 시간 잡으려고 해",
        _c_collect_answer_has_mine,
        seed=lambda: seed_my_saved_schedule(title="팀 리뷰", date_iso="2026-07-08", start_time="14:00"),
    ),
    # --- 외부 이전 대화 검색 ---
    Case(
        "previous_conversation_search",
        "철수랑 예전에 나눈 대화 중에 QA 리뷰 얘기했던 거 찾아줘",
        _c_previous_conversation_search,
        critical=True,
    ),
    # --- 대화 전체 로드 (검색 → 로드 2단계) ---
    Case(
        "load_full_conversation",
        "철수가 일정 공유했던 그 대화, 오간 메시지 전체를 그대로 보여줘",
        _c_load_full_conversation,
    ),
    # --- 공유 일정 저장소 조회 ---
    Case(
        "shared_store_listing",
        "공유 일정 저장소에 등록되어 있는 일정 목록 보여줘",
        _c_shared_store_listing,
    ),
    # --- 과교정 방지 — 내 일정만 묻는 질문이 외부 MCP로 새지 않는지 ---
    Case(
        "personal_only_guard",
        "내가 저장해둔 내 일정 목록만 보여줘",
        _c_personal_only_guard,
        seed=lambda: seed_my_saved_schedule(title="치과 진료", date_iso="2026-07-09"),
        critical=True,
    ),
    # --- 임의값 금지 — 이름을 말하지 않았는데 멤버를 지어내지 않는지 (호출 인자 판정) ---
    Case(
        "no_invented_member",
        "팀원들 일정 좀 봐줘",
        _c_no_invented_member,
        critical=True,
    ),
    # --- 추가 과제 — 공유 저장소 등록이 실제 외부 DB row로 남는지 ---
    Case(
        "shared_create",
        "7월 20일 15시에 지훈이랑 릴리즈 점검하는 일정 공유 저장소에 등록해줘",
        _c_shared_create,
    ),
    # ===== 1군: 한 번도 측정되지 않았던 계약·지시 축 =====
    # --- 추가 과제의 나머지 절반 — 삭제 라우팅 (데이터 파괴형) ---
    Case(
        "delete_shared_routing",
        "공유 일정 저장소에 등록된 7월 20일 릴리즈 점검 일정 지워줘",
        _c_delete_shared_routing,
        seed=seed_shared_row,
        critical=True,
    ),
    # --- extract vs collect 구분 (기존 축은 둘 중 아무거나 인정했다) ---
    Case(
        "extract_not_collect",
        "내 일정은 빼고 철수랑 영희 이번 주 일정만 알려줘",
        _c_extract_not_collect,
    ),
    # --- 중복 호출 금지 (프롬프트 명시 지시) ---
    Case(
        "no_double_collect",
        "나랑 철수, 민준이 이번 주 바쁜 시간 모아줘",
        _c_no_double_collect,
    ),
    # --- Week 6 경계 — 최종 시각을 확정해버리지 않는지 (heuristic) ---
    Case(
        "week6_boundary",
        "철수랑 나랑 이번 주에 언제 회의하면 좋을지 봐줘",
        _c_week6_boundary,
    ),
    # --- 검색 인자 규칙 — 사람 이름은 query가 아니라 member_names로 ---
    Case(
        "search_member_names_arg",
        "철수랑 예전에 나눈 대화 중에 QA 리뷰 얘기했던 거 찾아줘",
        _c_search_member_names_arg,
    ),
    # ===== 2군: 기존 축의 반대편 (kanana-conventions §6 양방향 검사) =====
    # --- external_answer_correct의 반대편 — 없는 일정을 지어내는가 ---
    Case(
        "no_hallucinated_schedule",
        "길동이 이번 주 일정 알려줘",
        _c_no_hallucinated_schedule,
        critical=True,
    ),
    # --- no_invented_member의 반대편 — 말한 이름을 누락하는가 ---
    Case(
        "member_names_not_dropped",
        "철수랑 영희랑 민준이 이번 주 일정 알려줘",
        _c_member_names_not_dropped,
        critical=True,
    ),
    # --- shared_create의 반대편 — 조회가 저장소를 건드리는가 ---
    Case(
        "read_only_no_side_effect",
        "공유 일정 저장소에 등록되어 있는 일정 목록 보여줘",
        _c_read_only_no_side_effect,
        seed=snapshot_shared_rows,
        critical=True,
    ),
    # --- 앱 탐색에서 승격 — 내 앱 대화를 외부 멤버 대화 검색으로 보내는가 (출처 분리 반대 방향) ---
    Case(
        "own_conversation_not_external",
        "아까 우리가 무슨 얘기 했지?",
        _c_own_conversation_not_external,
        critical=True,
        context_turns=["철수 7월 7일부터 17일까지 일정 알려줘"],
    ),
    # --- 앱 탐색에서 승격 — 조율 단서 없이 '모아줘'만 있을 때도 collect로 가는가 ---
    Case(
        "collect_without_cue",
        "나랑 철수, 영희 7월 7일부터 17일까지 일정 다 모아줘",
        _c_collect_without_cue,
        seed=lambda: seed_my_saved_schedule(title="내과 일정", date_iso="2026-07-10", start_time="11:00"),
        critical=True,
        # 앱에서 이 실패는 '비교' 턴 **다음 턴**에 나왔다(같은 conversation_id). 단일 턴으로는 3/3 통과라
        # 재현되지 않는다 — 직전 턴에서 쓴 personal_list_saved_schedules + extract 조합을 이어받는 것이 원인이다.
        context_turns=["내가 저장한 일정이랑 철수 7월 둘째 주 일정 비교해줘"],
    ),
    # --- 앱 탐색에서 승격 — 기간 미지정 시 하루로 좁혀 '없다'고 오답하는가 ---
    Case(
        "unspecified_period",
        "내가 저장한 일정이랑 철수 일정 비교해줘",
        _c_unspecified_period,
        seed=lambda: seed_my_saved_schedule(title="주간 보고", date_iso="2026-07-07", start_time="09:00"),
        critical=True,
    ),
    # --- 이전 주차 회귀 — 개인 참고자료는 그대로 Week 4 RAG로 ---
    Case(
        "week4_reference_regression",
        "내가 집중이 잘 된다고 적어둔 시간대가 언제였지?",
        _c_week4_reference_regression,
    ),
]


# --------------------------------------------------------------------- 결정적 안전규칙 (비-LLM)
def _collect_rows(member_names: list[str]) -> list[dict]:
    """collect_member_schedules를 현재 대화 범위에서 직접 부르고 rows만 꺼낸다."""
    payload = json.loads(
        m.collect_member_schedules.invoke(
            {"member_names": member_names, "date_from": "2026-07-06", "date_to": "2026-07-17"}
        )
    )
    return payload.get("rows", [])


def _dedup_once() -> tuple[bool, str]:
    """가이드 line 98·128: SQLite에 이미 저장된 일정과 Week 1 임시 일정을 중복 합산하지 않는다.

    ⚠️ **실제 앱 경로로 잰다.** Week 5 agent가 노출하는 personal_create_schedule은 week01판이 아니라
    week03판이다(week03_build_nanas_logbook.py:677이 같은 이름으로 교체). 이 tool은 한 번 호출에
      - Week 1 인메모리에 `id="personal_<hex>"` 임시 일정을 만들고
      - 같은 일정을 앱 SQLite에는 `schedule_id="sch_<sha1>"`(내용 해시)로 저장한다
        (week03_build_nanas_logbook.py:499 `_ensure_content_dedup_key` → :286)
    즉 **두 출처의 식별자가 구조적으로 절대 겹치지 않는다.** 가이드 line 128의 문구("schedule_id/id를
    기준으로")를 글자 그대로 id 비교로만 구현하면 이 케이스는 조용히 2건이 되고, 그 중복은
    Week 6의 공통 가능 시간 계산에서 **존재하지 않는 busy-time**으로 번진다.

    인위적으로 source_schedule_id를 임시 id에 맞춰 저장하면 id가 겹쳐 통과하지만, 그건 앱에서
    일어나지 않는 경로다 — 그래서 여기서는 w3.personal_create_schedule을 그대로 부른다.
    """
    rebind_temp_stores()
    with conversation_session_scope("conv_now"):
        w3.personal_create_schedule.invoke(
            {"title": "중복 후보", "date": "2026-07-10", "start_time": "11:00",
             "end_time": "12:00", "attendees": []}
        )
        rows = _collect_rows(["철수"])
    dup = [r for r in rows if r.get("title") == "중복 후보"]
    return len(dup) == 1, f"중복 후보 rows={len(dup)} ({[r.get('date') for r in dup]})"


def _no_over_dedup_once() -> tuple[bool, str]:
    """`kanana-conventions` §6 양방향 검사: 거르는 로직은 **과잉 제거**도 결함이다.

    _dedup_once()만 있으면 "전부 1건으로 합치기" 같은 구현도 통과한다. 제목·날짜·시작시각이 같고
    종료시각만 다른 두 일정은 서로 다른 busy-time이므로 2건으로 남아야 한다. 합쳐지면 Week 6의
    공통 가능 시간 계산에서 **실제로 바쁜 시간이 비어 있는 것처럼** 보인다.

    (Week 5에서 실제로 이 방향 검사가 없어, 자체 제작한 (title, date, start_time) 키가
    별개 일정을 합치는 결함이 verify·eval을 모두 통과했다.)
    """
    rebind_temp_stores()
    with conversation_session_scope("conv_now"):
        w3.personal_create_schedule.invoke(
            {"title": "겹침 시험", "date": "2026-07-13", "start_time": "10:00",
             "end_time": "11:00", "attendees": []}
        )
        w1.personal_create_schedule.invoke(
            {"title": "겹침 시험", "date": "2026-07-13", "start_time": "10:00",
             "end_time": "12:00", "attendees": []}
        )
        rows = _collect_rows(["철수"])
    same = [(r.get("start_time"), r.get("end_time")) for r in rows if r.get("title") == "겹침 시험"]
    return len(same) == 2, f"겹침 시험 rows={same}"


def _scope_isolation_once() -> tuple[bool, str]:
    """가이드 line 94·98: 다른 대화 범위의 Week 1 임시 일정은 현재 대화 rows에 섞이면 안 된다."""
    rebind_temp_stores()
    with conversation_session_scope("conv_other"):
        w1.personal_create_schedule.invoke(
            {"title": "남의 대화 일정", "date": "2026-07-11", "start_time": "09:00",
             "end_time": "10:00", "attendees": []}
        )
    with conversation_session_scope("conv_now"):
        rows = _collect_rows(["철수"])
    leaked = [r for r in rows if r.get("title") == "남의 대화 일정"]
    return not leaked, f"leaked={leaked}"


def check_deterministic(n: int, fn: Callable[[], tuple[bool, str]]) -> dict:
    """LLM 없이 helper를 직접 불러 데이터 안전규칙을 n회 단정한다."""
    passed, notes = 0, []
    for _ in range(n):
        try:
            ok, why = fn()
            passed += bool(ok)
            if not ok:
                notes.append(why)
        except Exception as e:  # noqa: BLE001
            notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
    return {"passed": passed, "n": n, "critical": True, "ambiguous": False, "notes": notes[:2]}


# --------------------------------------------------------------------- 3~5. 실행·집계
def tool_calls_of(out: dict) -> list[str]:
    return [c["name"] for msg in out.get("messages", []) for c in (getattr(msg, "tool_calls", []) or [])]


def tool_args_of(out: dict) -> list[dict]:
    """--- 4. 판정축 확장: tool 이름이 맞아도 **인자를 창작**하면 실패다. ---

    임의값 금지(kanana-conventions)는 tool 이름 축으로는 절대 안 잡힌다.
    """
    return [c.get("args") or {} for msg in out.get("messages", []) for c in (getattr(msg, "tool_calls", []) or [])]


def tool_calls_with_args(out: dict) -> list[tuple[str, dict]]:
    """(tool 이름, 인자) 쌍. 특정 tool의 인자만 봐야 하는 케이스가 쓴다."""
    return [
        (c["name"], c.get("args") or {})
        for msg in out.get("messages", [])
        for c in (getattr(msg, "tool_calls", []) or [])
    ]


def answer_of(out: dict) -> str:
    """--- 4. 판정: tool 이름 축만으로는 '맞는 tool을 부르고 틀린 답'을 놓친다. ---"""
    messages = out.get("messages", [])
    return str(getattr(messages[-1], "content", "")) if messages else ""


def run(n: int) -> dict[str, dict]:
    m._WEEK05_AGENT = None          # 1. 입력 고정: 고정 시계로 프롬프트 재조립
    agent = m.build_week05_agent()  # 1. 채널 고정: 실제 앱 경로 (PROXY_TOKEN 필요)
    results: dict[str, dict] = {}
    for case in CASES:
        passed, notes = 0, []
        for _ in range(n):
            rebind_temp_stores()          # 1. 상태 격리
            case.seed()
            try:
                with conversation_session_scope("eval_conv"):
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
                notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
        results[case.id] = {"passed": passed, "n": n, "critical": case.critical,
                            "ambiguous": case.ambiguous, "notes": notes[:2]}
        _print_row(case.id, results[case.id])

    # 비-LLM 결정적 안전규칙 (데이터 파괴류 결함은 여기서 통과율로 즉시 드러난다)
    results["collect_dedup"] = check_deterministic(n, _dedup_once)
    _print_row("collect_dedup", results["collect_dedup"])
    # 양방향 검사의 반대 축 — 과잉 제거도 결함이다 (kanana-conventions §6)
    results["collect_no_over_dedup"] = check_deterministic(n, _no_over_dedup_once)
    _print_row("collect_no_over_dedup", results["collect_no_over_dedup"])
    results["collect_scope_isolation"] = check_deterministic(n, _scope_isolation_once)
    _print_row("collect_scope_isolation", results["collect_scope_isolation"])
    return results


def _print_row(cid: str, r: dict) -> None:
    mark = "OK " if r["passed"] == r["n"] else ("~~ " if r["passed"] else "XX ")
    tag = " [critical]" if r["critical"] else (" [ambiguous]" if r["ambiguous"] else "")
    note = f"   {r['notes'][0]}" if r["notes"] else ""
    print(f"  {mark}{cid:30} {r['passed']}/{r['n']}{tag}{note}")


# --------------------------------------------------------------------- 6~7. 비교·게이트
def gate(results: dict[str, dict], pass_ratio: float) -> tuple[bool, list[str]]:
    fails = []
    for cid, r in results.items():
        if r.get("ambiguous"):
            continue
        if r["critical"] and r["passed"] < r["n"]:
            fails.append(f"{cid}: critical {r['passed']}/{r['n']}")
        elif not r["critical"] and r["passed"] < r["n"] * pass_ratio:
            fails.append(f"{cid}: {r['passed']}/{r['n']} < {pass_ratio:.0%}")
    return (not fails), fails


def compare(cur: dict, base_path: Path) -> None:
    base = json.loads(base_path.read_text(encoding="utf-8"))["results"]
    print("\n=== baseline 대비 변화 ===")
    changed = False
    for cid in cur:
        b = base.get(cid, {}).get("passed")
        c = cur[cid]["passed"]
        if b is None:
            print(f"  + {cid}: (신규) {c}/{cur[cid]['n']}"); changed = True
        elif b != c:
            arrow = "↑" if c > b else "↓"
            print(f"  {arrow} {cid}: {b} -> {c}/{cur[cid]['n']}"); changed = True
    if not changed:
        print("  (변화 없음)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="케이스당 반복 횟수")
    ap.add_argument("--pass-ratio", type=float, default=0.66, help="non-critical 합격 통과율")
    ap.add_argument("--save", type=Path, help="결과를 baseline JSON으로 저장")
    ap.add_argument("--baseline", type=Path, help="이 baseline과 비교")
    args = ap.parse_args()

    if not CONFIG.has_openai_key:
        print("SKIP: PROXY_TOKEN 없음 — Week 5 eval은 agent 경로(및 누적된 Week 4 임베딩)가 필요하다.")
        print("      키 없는 결정적 계약 검증은 `verify-week5` skill이 담당한다.")
        return 0

    print(f"채널=build_week05_agent() | 오늘={TODAY} | N={args.n}\n")
    results = run(args.n)

    total = sum(r["passed"] for r in results.values())
    denom = sum(r["n"] for r in results.values())
    print(f"\n총 통과: {total}/{denom}")

    if args.baseline and args.baseline.exists():
        compare(results, args.baseline)

    ok, fails = gate(results, args.pass_ratio)
    print("\n=== 게이트 ===")
    if ok:
        print("  PASS — 모든 critical 만점 + non-critical 임계 충족")
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
