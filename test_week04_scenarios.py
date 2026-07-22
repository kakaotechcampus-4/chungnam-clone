"""
4주차 메인과제 시나리오 테스트 스크립트.

각 함수마다 "이렇게 하면 이런 결과가 나와야 한다"는 시나리오를 먼저 적어두고,
실제로 tool을 호출해서 그 기대와 맞는지 확인한다.

"""

from __future__ import annotations

import json

from student_parts.week04_retrieve_nanas_memory import (
    add_personal_reference,
    search_personal_references,
    search_saved_requests,
)


def trace(tool_name: str, arguments: dict, result_json: str) -> dict:
    """tool_call/tool_result를 trace 탭에서 본 것과 같은 모양으로 출력한다."""

    print(json.dumps({"event": "tool_call", "tool_name": tool_name, "arguments": arguments}, ensure_ascii=False))
    try:
        content = json.loads(result_json)
    except Exception as e:
        print(f"  ⚠️ 결과를 JSON으로 못 읽음: {e!r}  (원본: {result_json!r})")
        return {}
    print(json.dumps({"event": "tool_result", "tool_name": tool_name, "content": content}, ensure_ascii=False, indent=2))
    return content


def check(label: str, condition: bool) -> None:
    mark = "✅" if condition else "❌"
    print(f"{mark} {label}")


def run_scenario(title: str, fn) -> None:
    print("=" * 70)
    print(title)
    print("=" * 70)
    try:
        fn()
    except Exception as e:
        print(f"❌ 시나리오 실행 중 에러 발생 (아직 구현 안 됐을 수 있음): {e!r}")
    print()

#search_saved_requests 함수 테스트
def scenario_search_saved_requests() -> None:
    args = {"query": "철수", "top_k": 3}
    result = search_saved_requests.invoke(args)
    content = trace("search_saved_requests", args, result)
    rows = content.get("rows", [])
    check("철수 관련 일정이 1개 이상 조회됨", len(rows) >= 1)
    check("조회된 일정에 철수가 포함됨", all("철수" in r.get("members_json", "") for r in rows))


#add_personal_reference 함수 테스트
def scenario_add_personal_reference() -> None:
    args = {
        "title": "점심시간 규칙",
        "content": "점심 시간 12:00-13:00은 되도록 회의 없이 비워둔다",
        "tags": ["업무규칙"],
    }
    result = add_personal_reference.invoke(args)
    content = trace("add_personal_reference", args, result)
    check("ok=True로 저장 성공", content.get("ok") is True)


#search_personal_references 함수 테스트
def scenario_search_personal_references() -> None:
    args = {"query": "점심 시간", "top_k": 2}
    result = search_personal_references.invoke(args)
    content = trace("search_personal_references", args, result)
    hits = content.get("hits", [])
    check("검색 결과가 1개 이상 나옴", len(hits) >= 1)
    check("점심시간 규칙 내용이 결과에 포함됨", any("점심" in h.get("content", "") for h in hits))


if __name__ == "__main__":
    run_scenario("시나리오 1: search_saved_requests(query='철수') → 철수 관련 일정이 나와야 한다", scenario_search_saved_requests)
    run_scenario("시나리오 2: add_personal_reference 저장 → ok=True가 나와야 한다", scenario_add_personal_reference)
    run_scenario("시나리오 3: search_personal_references(query='점심 시간') → 방금 저장한 내용이 나와야 한다", scenario_search_personal_references)
