"""Week 4 출처별 RAG 라우팅 + 대화 제외 안전규칙 재현 가능 eval.

Week 3 eval(evals/week03_eval.py)의 7단계 골격을 그대로 이식하되, Week 4 맥락
(질문 성격 → 개인 참고자료 / 저장 일정 / 일반 대화 중 맞는 RAG tool 선택)에 맞춘다.

  1. 입력 고정   — 시계·상태(temp SQLite + temp ChromaDB)·**호출 채널**을 못 박는다
  2. 검사 항목   — CASES 골든셋(출처별 라우팅 + 현재 대화 제외)
  3. 반복        — --n (기본 3)
  4. 판정        — tool 호출 목록(어느 출처 tool을 골랐나) + 대화 제외는 helper 직접 단정
  5. 집계        — 케이스별 통과율 n/N
  6. 비교        — --baseline out.json 저장 / 다음 실행과 diff
  7. 게이트      — critical 케이스 1회 실패 = 전체 실패, non-zero exit

⚠️ Week 4 핵심은 "RAG를 하나의 마법 함수로 보지 않고 출처별 tool로 분리"다. 그래서 판정축은
tool_calls_of(out) — 질문 성격에 맞는 tool(search_personal_references / search_saved_requests /
search_conversation_messages)을 골랐는가 — 이다. build_week04_agent()(실제 앱)로만 잰다.

⚠️ ChromaDB add/query는 OpenAI embedding proxy(PROXY_TOKEN)를 부른다 → 이 eval은 키가 있어야 돈다.
키가 없으면 즉시 SKIP(exit 0)한다.

⚠️ 상태 격리가 핵심이다. 반복마다 새 temp SQLite + 새 temp ChromaDB dir을 만들어
m.SQLITE_STORE / m.REFERENCE_STORE / m.CONVERSATION_RAG_STORE 를 재바인딩한다.
tool 본문이 호출 시점에 이 모듈 전역들을 읽으므로 매 반복 새 store가 반영된다.

이 파일은 `student_parts/`·`fixed/`를 **import만** 한다. 과제 코드는 수정하지 않는다.

실행:
  uv run python -X utf8 evals/week04_eval.py --n 3
  uv run python -X utf8 evals/week04_eval.py --n 5 --save evals/week04_baseline.json
  uv run python -X utf8 evals/week04_eval.py --n 3 --baseline evals/week04_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

# --- 1. 입력 고정: 시계를 week02/week03 few-shot 예시 날짜와 겹치지 않는 날로 못 박는다. ---
FROZEN_TODAY = date(2026, 3, 4)  # 수요일

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
import student_parts.week04_retrieve_nanas_memory as m  # noqa: E402

TODAY = rc.current_app_date_iso()  # 2026-03-04


# --------------------------------------------------------------------- 상태/store 헬퍼
def rebind_temp_stores() -> Path:
    """--- 1. 상태 격리: 반복마다 새 temp SQLite + ChromaDB dir로 세 store를 재바인딩한다. ---

    PersonalReferenceStore 생성 시 has_openai_key면 DEFAULT_REFERENCES를 seed하므로
    개인 참고자료 라우팅 케이스는 별도 seed 없이 성립한다.
    """
    tmp = Path(tempfile.mkdtemp())
    m.SQLITE_STORE = store_mod.AppSQLiteStore(tmp / "app.sqlite3")
    m.REFERENCE_STORE = PersonalReferenceStore(tmp / "chroma")          # seed됨
    m.CONVERSATION_RAG_STORE = ConversationRAGStore(tmp / "chroma")
    return tmp


def seed_saved_request(*, title: str, date_iso: str, start_time: str = "10:00") -> None:
    """--- 1. 입력 고정: SQLite structured_requests에 저장 일정을 심는다(임베딩 불필요). ---"""
    m.SQLITE_STORE.save_structured_request(
        {"kind": "personal_schedule", "title": title, "date": date_iso, "start_time": start_time}
    )


def seed_conversation(*, title: str, turns: list[tuple[str, str]]) -> str:
    """--- 1. 입력 고정: 과거 대화 하나를 messages 테이블에 심고 conversation_id를 돌려준다. ---"""
    conv = m.SQLITE_STORE.create_conversation(title=title)
    cid = conv["conversation_id"]
    for role, content in turns:
        m.SQLITE_STORE.append_message(cid, role, content)
    return cid


@dataclass
class Case:
    id: str
    text: str
    check: Callable[[Any, list[str]], tuple[bool, str]]
    seed: Callable[[], None] = lambda: None  # rebind 이후 실행할 사전 상태
    critical: bool = False
    ambiguous: bool = False
    llm: bool = True  # False면 helper 직접 호출(LLM 없이 결정적으로 판정)


# --------------------------------------------------------------------- 2. 골든셋
def _c_ref_routing(out, tools):
    # 개인 취향/메모 질문 → 개인 참고자료 RAG로 라우팅
    ok = "search_personal_references" in tools
    return ok, f"tools={tools}"


def _c_saved_routing(out, tools):
    # 저장한 일정/할 일 질문 → SQLite 저장 요청 검색으로 라우팅
    ok = "search_saved_requests" in tools
    return ok, f"tools={tools}"


def _c_conversation_routing(out, tools):
    # 예전 "대화"에서 나눈 얘기 질문 → 대화 RAG로 라우팅
    ok = "search_conversation_messages" in tools
    return ok, f"tools={tools}"


# --- probe: CoT workflow가 필요한지 측정하는 어려운 케이스 (현재 프롬프트를 깨보는 용도) ---
def _c_multi_source(out, tools):
    # 다중출처 결합: 한 질문이 "저장된 일정" 소스 + "개인 참고자료" 소스를 둘 다 요구.
    # 판정축은 tool 식별이 아니라 "두 출처를 엮었는가"다. 저장 일정 소스는 Week4 keyword
    # 검색(search_saved_requests)이든 Week3 일정 나열(personal_list_saved_schedules)이든
    # 유효하므로 둘 중 하나면 인정한다. 참고자료 소스는 search_personal_references.
    saved_source = {"search_saved_requests", "personal_list_saved_schedules"} & set(tools)
    ok = bool(saved_source) and "search_personal_references" in tools
    return ok, f"tools={tools} saved_source={sorted(saved_source)}"


def _c_add_reference_routing(out, tools):
    # 지속적 선호/원칙/메모를 적어 두라는 요청 → add_personal_reference(참고자료 추가)로 가야 한다.
    # Week3 리마인더 저장(save_structured_request)으로 새면 실패(수동 테스트에서 관측된 오라우팅).
    ok = "add_personal_reference" in tools
    return ok, f"tools={tools}"


def _c_add_reminder_guard(out, tools):
    # 과교정 방지: 시간·행동에 묶인 리마인더/할 일은 여전히 저장(save) 경로로 가야 하고
    # add_personal_reference로 새면 안 된다(참고자료로 오분류하면 실패).
    ok = "add_personal_reference" not in tools
    return ok, f"tools={tools}"


def _c_reference_rule_routing(out, tools):
    # 내가 적어둔 규칙/선호를 확인하는 질문("점심에 회의 잡지 말라 했던 거 맞아?")은
    # 개인 참고자료(search_personal_references)로 가야 시드된 '점심 시간 보호'를 찾는다.
    # 저장요청/일정(search_saved_requests)으로만 가면 못 찾고 오답한다(수동 테스트 #12 관측).
    ok = "search_personal_references" in tools
    return ok, f"tools={tools}"


CASES: list[Case] = [
    Case(
        "ref_routing",
        "내가 집중이 잘 된다고 적어둔 시간대가 언제였지?",
        _c_ref_routing,
        critical=True,
    ),
    Case(
        "saved_routing",
        "내가 저장해둔 치과 진료 일정 언제였는지 찾아줘",
        _c_saved_routing,
        seed=lambda: seed_saved_request(title="치과 진료", date_iso="2026-03-20"),
        critical=True,
    ),
    Case(
        "conversation_routing",
        "예전 대화에서 얘기했던 제주도 여행 계획 다시 정리해줘",
        _c_conversation_routing,
        seed=lambda: seed_conversation(
            title="제주도 여행",
            turns=[
                ("user", "다음 달 제주도 여행 3박 4일로 가고 싶어"),
                ("assistant", "제주도 3박 4일이면 동선을 동/서로 나누는 게 좋아요"),
            ],
        ),
    ),
    # --- 다중출처 결합 — 저장 일정 소스 + 개인 참고자료 소스를 둘 다 엮는지 (probe에서 승격) ---
    Case(
        "multi_source_combine",
        "다음 주 팀 회의 일정 알려주고, 회의 잡을 때 내가 선호한다고 적어둔 것도 같이 알려줘",
        _c_multi_source,
        seed=lambda: seed_saved_request(title="팀 회의", date_iso="2026-03-25"),
    ),
    # --- add 라우팅 — 지속적 선호/메모를 적어 두라는 요청은 참고자료 추가로 (수동 테스트에서 발굴) ---
    Case(
        "add_reference_routing",
        "회의는 45분 넘기지 말자고 메모해둬",
        _c_add_reference_routing,
        critical=True,
    ),
    # --- add 과교정 방지 — 시간·행동에 묶인 리마인더는 여전히 저장 경로로 ---
    Case(
        "add_reminder_guard",
        "내일 오후 3시에 약 먹으라고 알려줘",
        _c_add_reminder_guard,
        critical=True,
    ),
    # --- 참고자료 규칙 확인 라우팅 (알려진 어려운 경계 케이스, 관측용) ---
    # "했던 거 맞아?"가 참고자료·대화 사이에 걸치고 "회의" 키워드가 saved_requests로 끈다.
    # 현재 프롬프트는 ~1/3만 맞추며, 억지로 올리면 conversation_routing이 회귀(0/3)하는 zero-sum이라
    # 게이트에서 제외하고 통과율만 관측한다(수동 테스트 #12에서 발굴).
    Case(
        "reference_rule_routing",
        "점심시간엔 회의 잡지 말라고 했던 거 맞아?",
        _c_reference_rule_routing,
        ambiguous=True,
    ),
]


# --------------------------------------------------------------------- 현재 대화 제외 (helm 직접, 비-LLM)
def check_current_conversation_excluded(n: int) -> dict:
    """가이드 line 129·155: conversation_id 미지정 시 현재 대화는 검색에서 제외되어야 한다.

    LLM 없이 helper를 직접 부른다: 과거 대화와 '현재' 대화 둘 다 같은 주제를 담고,
    conversation_session_scope(current_id) 안에서 conversation_id=None으로 검색했을 때
    현재 대화가 hit에 섞이지 않아야 한다(데이터 안전규칙).
    """
    passed, notes = 0, []
    for _ in range(n):
        rebind_temp_stores()
        seed_conversation(
            title="지난 회고",
            turns=[("user", "지난주 스프린트 회고에서 배포 자동화를 논의했어")],
        )
        current_id = seed_conversation(
            title="오늘 대화",
            turns=[("user", "방금 배포 자동화 얘기 다시 꺼냈어")],
        )
        try:
            with conversation_session_scope(current_id):
                res = m.search_conversation_messages_dict(
                    m.SQLITE_STORE,
                    m.CONVERSATION_RAG_STORE,
                    query="배포 자동화",
                    top_k=5,
                    conversation_id=None,
                )
            hit_ids = {h.get("conversation_id") for h in res.get("hits", [])}
            ok = current_id not in hit_ids
            passed += bool(ok)
            if not ok:
                notes.append(f"현재 대화 {current_id} 가 hits에 섞임: {hit_ids}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
    return {"passed": passed, "n": n, "critical": True, "ambiguous": False, "notes": notes[:2]}


# --------------------------------------------------------------------- 3~5. 실행·집계
def tool_calls_of(out: dict) -> list[str]:
    return [c["name"] for msg in out.get("messages", []) for c in (getattr(msg, "tool_calls", []) or [])]


def run(n: int) -> dict[str, dict]:
    m._WEEK04_AGENT = None          # 1. 입력 고정: 고정 시계로 프롬프트 재조립
    agent = m.build_week04_agent()  # 1. 채널 고정: 실제 앱 경로 (PROXY_TOKEN 필요)
    results: dict[str, dict] = {}
    for case in CASES:
        passed, notes = 0, []
        for _ in range(n):
            rebind_temp_stores()          # 1. 상태 격리
            case.seed()
            try:
                out = agent.invoke({"messages": [{"role": "user", "content": case.text}]})
                ok, why = case.check(out, tool_calls_of(out))
                passed += bool(ok)
                if not ok:
                    notes.append(why)
            except Exception as e:  # noqa: BLE001
                notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
        results[case.id] = {"passed": passed, "n": n, "critical": case.critical,
                            "ambiguous": case.ambiguous, "notes": notes[:2]}
        _print_row(case.id, results[case.id])

    # 비-LLM 결정적 안전규칙
    results["current_conversation_excluded"] = check_current_conversation_excluded(n)
    _print_row("current_conversation_excluded", results["current_conversation_excluded"])
    return results


def _print_row(cid: str, r: dict) -> None:
    mark = "OK " if r["passed"] == r["n"] else ("~~ " if r["passed"] else "XX ")
    tag = " [critical]" if r["critical"] else (" [ambiguous]" if r["ambiguous"] else "")
    note = f"   {r['notes'][0]}" if r["notes"] else ""
    print(f"  {mark}{cid:28} {r['passed']}/{r['n']}{tag}{note}")


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
        print("SKIP: PROXY_TOKEN 없음 — Week 4 eval은 ChromaDB 임베딩이 필요하다.")
        return 0

    print(f"채널=build_week04_agent() | 오늘={TODAY} | N={args.n}\n")
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
