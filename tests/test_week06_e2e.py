"""Week 6 위임 라우팅 e2e — 자연어 입력이 어느 하위 agent로 가는지 확인한다.

앱에서 채팅을 치는 것과 같은 경로(build_week_agent().invoke)를 실행하고 supervisor trace를
검증한다. 실제 LLM을 부르므로 느리다. 결정적 유닛은 tests/test_week06_decision.py에 있다.

이 주차의 실패는 tool 선택보다 한 층 위에서 난다. "어느 tool을 부를까"가 아니라 "어느 agent에게
보낼까"가 먼저다. 그래서 검증도 supervisor가 고른 위임 tool과 그 하위에서 실제로 불린 tool을
나눠서 본다.

케이스는 tests/week06_routing_cases.yaml에 있다. 수동으로 돌려보다 새 실패를 찾으면
yaml에 한 항목만 추가하면 되고 이 파일은 고치지 않는다.
(tests/data/ 아래에 두면 .gitignore의 data/ 규칙에 걸려 커밋되지 않는다.)

호출 순서는 프롬프트로 보장되지 않는다. Kana의 세 단계는 5회 측정에서 모두 순서를 지켰지만
보장이 아니라 실측이므로, 사이에 다른 호출이 끼는 것은 허용하고 부분 순서만 확인한다.
tool 선택에도 편차가 있어 케이스마다 한 번까지 재시도한다.

앱 DB·외부 DB·ChromaDB는 conftest가 세션 단위로 임시 경로에 격리한다. 조율 후 저장 케이스가
일정을 만들지만 임시 DB에만 남는다.

실행: uv run --with pytest pytest tests/test_week06_e2e.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.config import CONFIG  # noqa: E402
from student_parts.week06_kanamate_decides_schedule import (  # noqa: E402
    build_week_agent,
    extract_langchain_trace,
    kana_tools,
)
from student_parts.week04_retrieve_nanas_memory import week04_tools  # noqa: E402

pytestmark = pytest.mark.skipif(
    not CONFIG.has_openai_key, reason="PROXY_TOKEN이 없으면 agent를 실행할 수 없다"
)

CASES_PATH = Path(__file__).parent / "week06_routing_cases.yaml"
CASES = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))

DELEGATE_TOOLS = {"nana_agent", "kana_agent"}
# supervisor가 절대 직접 부를 수 없어야 하는 이름. 하위 agent에만 있어야 한다.
BUSINESS_TOOLS = {tool.name for tool in [*week04_tools(), *kana_tools()]} - DELEGATE_TOOLS


def run_case(query: str) -> dict[str, Any]:
    """앱과 같은 경로로 질문을 실행하고 trace를 돌려준다."""

    result = build_week_agent().invoke({"messages": [{"role": "user", "content": query}]})
    trace = extract_langchain_trace(result)
    trace["supervisor_tools"] = [
        event["tool_name"] for event in trace["events"] if event.get("event") == "tool_call"
    ]
    return trace


def is_subsequence(expected: list[str], actual: list[str]) -> bool:
    """expected가 actual 안에 이 순서로 들어 있는지. 사이에 다른 호출이 끼어도 된다."""

    remaining = list(expected)
    for name in actual:
        if remaining and name == remaining[0]:
            remaining.pop(0)
    return not remaining


def check_case(case: dict[str, Any], trace: dict[str, Any]) -> str | None:
    """기대와 어긋난 첫 항목을 설명으로 돌려준다. 다 맞으면 None."""

    supervisor_tools = trace["supervisor_tools"]
    inner = trace["inner_tool_names"]

    leaked = BUSINESS_TOOLS & set(supervisor_tools)
    if leaked:
        return f"supervisor가 업무 tool을 직접 호출했다: {sorted(leaked)}"

    expected_agents = case["expect_agents"]
    delegated = [name for name in supervisor_tools if name in DELEGATE_TOOLS]
    if delegated != expected_agents:
        return f"위임 대상이 다르다: 기대 {expected_agents}, 실제 {delegated}"

    any_of = case.get("expect_inner_any")
    if any_of and not set(any_of) & set(inner):
        return f"하위 tool에 {any_of} 중 아무것도 없다: {inner}"

    in_order = case.get("expect_inner_order")
    if in_order and not is_subsequence(in_order, inner):
        return f"하위 tool 순서가 다르다: 기대 {in_order}, 실제 {inner}"

    if case.get("expect_final_slot") and not (trace["final_slot_payload"] or {}).get("final_slot"):
        return f"최종 시간이 확정되지 않았다: {trace['final_slot_payload']}"

    return None


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_delegation_routing(case):
    # tool 선택에 편차가 있어 한 번까지 재시도한다. 단정을 느슨하게 하지는 않는다
    failures = []
    for _ in range(2):
        trace = run_case(case["input"])
        problem = check_case(case, trace)
        if problem is None:
            return
        failures.append(problem)
    assert False, " / ".join(failures)


def test_supervisor_sees_only_two_tools():
    # 위임 구조의 전제. 여기가 깨지면 라우팅 검증 전체가 의미를 잃는다
    from student_parts.week06_kanamate_decides_schedule import supervisor_tools

    assert {tool.name for tool in supervisor_tools()} == DELEGATE_TOOLS


def test_business_tools_live_only_in_subagents():
    # (대조) 하위 agent에는 업무 tool이 실제로 있어야 한다. 위 테스트만으로는 빈 목록도 통과한다
    assert len(BUSINESS_TOOLS) > 10
    assert "collect_member_schedules" in BUSINESS_TOOLS
    assert "decide_final_slot" in BUSINESS_TOOLS
