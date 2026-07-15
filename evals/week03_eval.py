"""Week 3 tool-calling + SQLite 영속 agent 재현 가능 eval.

Week 2 eval(evals/week02_eval.py)의 7단계 골격을 그대로 이식하되, Week 3 맥락
(자연어 → 구조화 → SQLite 저장/조회/수정/삭제)에 맞춘다.

  1. 입력 고정   — 시계·상태(temp DB)·**호출 채널**을 못 박는다
  2. 검사 항목   — CASES 골든셋
  3. 반복        — --n (기본 3)
  4. 판정        — tool 호출 목록 + temp DB 상태 단정
  5. 집계        — 케이스별 통과율 n/N
  6. 비교        — --baseline out.json 저장 / 다음 실행과 diff
  7. 게이트      — critical 케이스 1회 실패 = 전체 실패, non-zero exit

⚠️ Week 3 agent는 response_format이 없다 → structured_response 필드를 단정할 수 없다.
그래서 판정은 tool_calls_of(out)(어떤 tool을 호출했나)와 db_schedules()(temp DB에
무엇이 남았나)로 한다. build_week03_agent()(실제 앱)로만 잰다.

⚠️ 상태 격리가 핵심이다. 반복마다 새 temp SQLite DB를 만들어 m._store를 재바인딩한다.
tool 본문이 호출 시점에 m._store()를 부르므로 매 반복 새 DB가 반영된다.

이 파일은 `student_parts/`·`fixed/`를 **import만** 한다. 과제 코드는 수정하지 않는다.

실행:
  uv run python -X utf8 evals/week03_eval.py --n 3
  uv run python -X utf8 evals/week03_eval.py --n 5 --save evals/week03_baseline.json
  uv run python -X utf8 evals/week03_eval.py --n 3 --baseline evals/week03_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

# --- 1. 입력 고정: 시계를 few-shot 예시 날짜(week02 05-11/19, week03 예시 A 05-11/12)와 ---
#     겹치지 않는 날로 못 박는다. 겹치면 모델이 예시를 베껴도 정답처럼 보여 오염 버그를 못 잡는다.
FROZEN_TODAY = date(2026, 3, 4)  # 수요일

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fixed.runtime_clock as rc  # noqa: E402

rc.APP_TODAY = FROZEN_TODAY  # import 시점 고정값을 덮어쓴다 (프롬프트 조립 전에)

import fixed.app_store as store_mod  # noqa: E402
import student_parts.week03_build_nanas_logbook as m  # noqa: E402

TODAY = rc.current_app_date_iso()                       # 2026-03-04
TOMORROW = (FROZEN_TODAY + timedelta(days=1)).isoformat()  # 2026-03-05

# 외부 공유 저장소 동기화는 eval 대상이 아니다 → import 시 1회 no-op 으로 스텁한다.
# app_store 내부 함수들이 모듈 전역 이름으로 이 함수들을 참조하므로 setattr 로 덮어쓰면 된다.
for _name in (
    "sync_personal_schedule_to_shared",
    "sync_group_schedule_to_shared",
    "delete_personal_schedule_from_shared",
    "delete_group_schedule_from_shared",
):
    setattr(store_mod, _name, lambda *a, **k: {"ok": True, "stub": True})


# --------------------------------------------------------------------- 상태/DB 헬퍼
def db_schedules() -> list[dict]:
    """판정 시점의 현재 temp DB 일정 상태를 조회한다."""
    return m._store().list_schedules(limit=200)


def seed_schedules(items: list[dict]) -> None:
    """--- 1. 입력 고정: 실제 저장 tool로 temp DB에 사전 일정을 심는다 ---"""
    for it in items:
        m.save_structured_request.invoke(
            {
                "kind": "personal_schedule",
                "title": it["title"],
                "date": it["date"],
                "start_time": it.get("start_time", "10:00"),
            }
        )


@dataclass
class Case:
    id: str
    text: str
    check: Callable[[Any, list[str]], tuple[bool, str]]
    seed: list[dict] = field(default_factory=list)  # 사전 일정 (title/date/start_time)
    critical: bool = False
    ambiguous: bool = False  # 본질적으로 모호한 입력 — 통과율만 관측하고 게이트에서 제외


# --------------------------------------------------------------------- 2. 골든셋
def _c_save_routing(out, tools):
    # 저장 라우팅: 구조화 → 저장 경로만 타고 Week 1 호환 생성은 쓰지 않는다 (이중 저장 방지)
    db = db_schedules()
    ok = (
        "extract_schedule_request" in tools
        and "save_structured_request" in tools
        and "personal_create_schedule" not in tools
        and len(db) == 1
    )
    return ok, f"tools={tools} db={len(db)}"


def _c_list_query(out, tools):
    # 조회는 personal_list_saved_schedules 로만. 저장/삭제 tool 을 건드리면 안 되고 DB 도 그대로.
    db = db_schedules()
    ok = (
        "personal_list_saved_schedules" in tools
        and not (
            {
                "save_structured_request",
                "personal_create_schedule",
                "personal_delete_saved_schedules",
                "personal_delete_schedule",
            }
            & set(tools)
        )
        and len(db) == 2
    )
    return ok, f"tools={tools} db={len(db)}"


def _c_delete_specific(out, tools):
    # 특정 삭제: 삭제 tool 호출 + 대상만 사라지고 나머지 1건 보존
    db = db_schedules()
    titles = sorted(s["title"] for s in db)
    ok = "personal_delete_saved_schedules" in tools and titles == ["팀 회의"]
    return ok, f"tools={tools} kept={titles}"


def _c_delete_vague(out, tools):
    # 오삭제 방지: 대상이 특정되지 않으면 삭제 tool 미호출 + 2건 보존
    db = db_schedules()
    ok = "personal_delete_saved_schedules" not in tools and len(db) == 2
    return ok, f"tools={tools} db={len(db)}"


def _c_update_time(out, tools):
    # 시간 수정: update tool 호출 + 치과 진료 start_time 이 14:00 으로 반영
    db = db_schedules()
    ok = "personal_update_saved_schedule" in tools and any(
        (s.get("title") == "치과 진료" or "치과" in (s.get("title") or ""))
        and s.get("start_time") == "14:00"
        for s in db
    )
    return ok, f"tools={tools} db={[(s.get('title'), s.get('start_time')) for s in db]}"


_TWO_SCHEDULES = [
    {"title": "치과 진료", "date": "2026-03-20"},
    {"title": "팀 회의", "date": "2026-03-21"},
]


CASES: list[Case] = [
    Case("save_routing", "내일 오전 10시에 철수랑 개인 코칭 저장해줘", _c_save_routing, critical=True),
    Case("list_query", "내 저장된 일정 전부 보여줘", _c_list_query, seed=_TWO_SCHEDULES),
    Case("delete_specific", "치과 진료 일정 지워줘", _c_delete_specific, seed=_TWO_SCHEDULES),
    Case("delete_vague", "아까 그거 지워줘", _c_delete_vague, critical=True, seed=_TWO_SCHEDULES),
    Case(
        "update_time",
        "치과 진료 시간을 오후 2시로 바꿔줘",
        _c_update_time,
        seed=[{"title": "치과 진료", "date": "2026-03-20", "start_time": "10:00"}],
    ),
]


# --------------------------------------------------------------------- 3~5. 실행·집계
def tool_calls_of(out: dict) -> list[str]:
    return [c["name"] for msg in out.get("messages", []) for c in (getattr(msg, "tool_calls", []) or [])]


def run(n: int) -> dict[str, dict]:
    m._WEEK03_AGENT = None            # 1. 입력 고정: 고정 시계로 프롬프트 재조립
    agent = m.build_week03_agent()    # 1. 채널 고정: 실제 앱 경로 (PROXY_TOKEN 필요)
    results: dict[str, dict] = {}
    for case in CASES:
        passed, notes = 0, []
        for _ in range(n):
            # 1. 상태 격리: 반복마다 새 temp DB 를 만들고 _store 를 재바인딩한다.
            tmp = Path(tempfile.mkdtemp()) / "app.sqlite3"
            m._store = (lambda tmp=tmp: store_mod.AppSQLiteStore(tmp))
            seed_schedules(case.seed)
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

    print(f"채널=build_week03_agent() | 오늘={TODAY} 내일={TOMORROW} | N={args.n}\n")
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
