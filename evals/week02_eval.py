"""Week 2 구조화 agent 재현 가능 eval (7단계 파이프라인).

멘토 피드백("프롬프트가 미묘하게 달라질 때마다 결과값이 달라진다. 테스트 파이프라인을 어떻게
구성하면 결과값이 동일하게 나오는가?")에 대한 실증 답이다. 7단계를 코드로 구현한다.

  1. 입력 고정   — 시계·상태·**호출 채널**을 못 박는다
  2. 검사 항목   — CASES 골든셋
  3. 반복        — --n (기본 3)
  4. 판정        — 필드 단정(structured output). reason 등 자유 서술은 검사하지 않는다
  5. 집계        — 케이스별 통과율 n/N
  6. 비교        — --baseline out.json 저장 / 다음 실행과 diff
  7. 게이트      — critical 케이스 1회 실패 = 전체 실패, non-zero exit

⚠️ 채널 고정이 핵심이다. build_week02_agent()(실제 앱)로만 잰다.
`with_structured_output`으로 재면 같은 프롬프트에서도 결과가 달라진다
(예: "아침에" -> 08:00 vs None). 서비스와 다른 경로로 잰 수치는 무의미하다.

이 파일은 `student_parts/`·`fixed/`를 **import만** 한다. 과제 코드는 수정하지 않는다.

실행:
  uv run python -X utf8 evals/week02_eval.py --n 3
  uv run python -X utf8 evals/week02_eval.py --n 3 --save evals/baseline.json
  uv run python -X utf8 evals/week02_eval.py --n 3 --baseline evals/baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

# --- 1. 입력 고정: 시계를 few-shot 예시 날짜(2026-05-11/19)와 겹치지 않는 날로 못 박는다 ---
#     겹치면 모델이 예시를 베껴도 정답처럼 보여 오염 버그를 못 잡는다.
FROZEN_TODAY = date(2026, 3, 4)  # 수요일

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fixed.runtime_clock as rc  # noqa: E402

rc.APP_TODAY = FROZEN_TODAY  # import 시점 고정값을 덮어쓴다 (프롬프트 조립 전에)

import student_parts.week01_wake_up_nana as w01  # noqa: E402
import student_parts.week02_structure_natural_language_requests as m  # noqa: E402

TODAY = rc.current_app_date_iso()        # 2026-03-04
NEXT_TUE = rc.next_weekday_iso(1)        # 2026-03-10
NEXT_MON = rc.next_weekday_iso(0)        # 2026-03-09


# --------------------------------------------------------------------- 판정 헬퍼
def _req(batch, n=1):
    return batch.requests[0] if (n == 1 and len(batch.requests) == 1) else None


@dataclass
class Case:
    id: str
    text: str
    check: Callable[[Any, list[str]], tuple[bool, str]]
    seed: list[dict] = field(default_factory=list)  # 사전 일정 (title/date/start_time)
    critical: bool = False
    ambiguous: bool = False  # 본질적으로 모호한 입력 — 통과율만 관측하고 게이트에서 제외


def seed_schedules(items: list[dict]) -> None:
    """--- 1. 입력 고정: 상태 초기화 후 필요한 일정만 seed ---"""
    w01.PERSONAL_SCHEDULES[:] = []
    for it in items:
        w01.personal_create_schedule.invoke(
            {"title": it["title"], "date": it["date"], "start_time": it.get("start_time", "10:00")}
        )


# --------------------------------------------------------------------- 2. 골든셋
def _c_personal_full(b, tools):
    r = _req(b)
    if r is None:
        return False, f"requests={len(b.requests)}"
    ok = (r.kind == "personal_schedule" and r.date == NEXT_TUE and r.start_time == "15:00"
          and r.members == ["철수"] and r.end_time is None and "personal_create_schedule" in tools)
    return ok, f"kind={r.kind} date={r.date} start={r.start_time} members={r.members} tools={tools}"


def _c_personal_boundary(b, tools):
    r = _req(b)
    if r is None:
        return False, f"requests={len(b.requests)}"
    # "팀원들이랑" 이지만 Nana가 직접 잡는 개인 일정 → personal
    return r.kind == "personal_schedule", f"kind={r.kind}"


def _c_group_vague(b, tools):
    r = _req(b)
    if r is None:
        return False, f"requests={len(b.requests)}"
    return (r.kind == "group_schedule" and r.date is None and r.members == []), \
        f"kind={r.kind} date={r.date} members={r.members}"


def _c_todo(b, tools):
    r = _req(b)
    if r is None:
        return False, f"requests={len(b.requests)}"
    return (r.kind == "todo" and r.start_time is None), f"kind={r.kind} start={r.start_time}"


def _c_reminder_notime(b, tools):
    r = _req(b)
    if r is None:
        return False, f"requests={len(b.requests)}"
    # "아침에"는 시각이 아니다 → start_time 은 None 이어야 한다 (08:00 지어내면 실패)
    return (r.kind == "reminder" and r.date == NEXT_MON and r.start_time is None), \
        f"kind={r.kind} date={r.date} start={r.start_time!r}"


def _c_multi(b, tools):
    return len(b.requests) == 2, f"requests={len(b.requests)}"


def _c_date_leak(b, tools):
    # few-shot 예시 날짜가 실제 출력로 새어나오면 실패
    dump = str(b.model_dump())
    return (b.base_date == TODAY and "2026-05-1" not in dump), f"base_date={b.base_date}"


def _c_delete_vague(b, tools):
    # 오삭제 방지: 삭제 tool 미호출 + 일정 2건 보존 + unknown
    r = _req(b)
    kept = len(w01.PERSONAL_SCHEDULES)
    ok = ("personal_delete_schedule" not in tools and kept == 2 and r is not None and r.kind == "unknown")
    return ok, f"tools={tools} kept={kept} kind={r and r.kind}"


def _c_delete_specific(b, tools):
    titles = [s["title"] for s in w01.PERSONAL_SCHEDULES]
    r = _req(b)
    ok = ("personal_delete_schedule" in tools and titles == ["팀 회의"] and r is not None and r.kind == "unknown")
    return ok, f"tools={tools} kept={titles} kind={r and r.kind}"


def _c_list(b, tools):
    return ("personal_list_schedules" in tools and len(b.requests) == 2), \
        f"tools={tools} requests={len(b.requests)}"


CASES: list[Case] = [
    Case("personal_full", "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘", _c_personal_full),
    # "팀원들이랑" (참석자=팀) 인데 "잡아줘" (시점 이미 지시) — personal/group 신호가 충돌하는
    # 본질적으로 모호한 입력. 측정상 personal ~56% (10/18). 프롬프트로 억지로 한쪽에 밀지 않고
    # 통과율만 관측한다(게이트 제외).
    Case("personal_boundary", "팀원들이랑 회의 잡아줘", _c_personal_boundary, ambiguous=True),
    Case("group_vague", "조만간 팀 회식 날짜 다 같이 정하자", _c_group_vague),
    Case("todo_deadline", "금요일까지 보고서 초안 마무리해야 해", _c_todo),
    Case("reminder_notime", "다음 주 월요일 아침에 우산 챙기라고 알려줘", _c_reminder_notime, critical=True),
    Case("multi_request", "다음 주 화요일 3시에 철수랑 회의 잡고, 회식도 조만간 한번 하자", _c_multi),
    Case("date_leak", "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘", _c_date_leak, critical=True),
    Case("delete_vague", "그 일정 지워줘", _c_delete_vague, critical=True,
         seed=[{"title": "치과 진료", "date": "2026-03-20"}, {"title": "팀 회의", "date": "2026-03-21"}]),
    Case("delete_specific", "치과 진료 일정 지워줘", _c_delete_specific,
         seed=[{"title": "치과 진료", "date": "2026-03-20"}, {"title": "팀 회의", "date": "2026-03-21"}]),
    Case("list_query", "내 일정 전부 보여줘", _c_list,
         seed=[{"title": "치과 진료", "date": "2026-03-20"}, {"title": "팀 회의", "date": "2026-03-21"}]),
]


# --------------------------------------------------------------------- 3~5. 실행·집계
def tool_calls_of(out: dict) -> list[str]:
    return [c["name"] for msg in out.get("messages", []) for c in (getattr(msg, "tool_calls", []) or [])]


def run(n: int) -> dict[str, dict]:
    m._WEEK02_AGENT = None            # 1. 입력 고정: 고정 시계로 프롬프트 재조립
    agent = m.build_week02_agent()    # 1. 채널 고정: 실제 앱 경로
    results: dict[str, dict] = {}
    for case in CASES:
        passed, notes = 0, []
        for _ in range(n):
            seed_schedules(case.seed)
            try:
                out = agent.invoke({"messages": [{"role": "user", "content": case.text}]})
                ok, why = case.check(out["structured_response"], tool_calls_of(out))
                passed += bool(ok)
                if not ok:
                    notes.append(why)
            except Exception as e:  # noqa: BLE001
                notes.append(f"ERR:{type(e).__name__}:{str(e)[:60]}")
        results[case.id] = {"passed": passed, "n": n, "critical": case.critical,
                            "ambiguous": case.ambiguous, "notes": notes[:2]}
        mark = "OK " if passed == n else ("~~ " if passed else "XX ")
        tag = " [critical]" if case.critical else (" [ambiguous]" if case.ambiguous else "")
        print(f"  {mark}{case.id:18} {passed}/{n}{tag}" + (f"   {notes[0]}" if notes else ""))
    return results


# --------------------------------------------------------------------- 6~7. 비교·게이트
def gate(results: dict[str, dict], pass_ratio: float) -> tuple[bool, list[str]]:
    fails = []
    for cid, r in results.items():
        if r.get("ambiguous"):
            continue  # 모호 케이스는 통과율만 관측, 게이트에서 제외
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

    print(f"채널=build_week02_agent() | 오늘={TODAY} | 다음주 화={NEXT_TUE} 월={NEXT_MON} | N={args.n}\n")
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
