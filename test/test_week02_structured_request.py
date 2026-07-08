"""Week 2 자연어 요청 구조화 검증 스크립트.

논리 테스트 케이스: 총 7개
- TC-01: StructuredRequest 기본값이 안전한 값(None/빈 목록/빈 문자열)인지 검증한다.
- TC-02: RequestKind 밖의 kind 값이 ValidationError로 거부되는지 검증한다.
- TC-03: StructuredRequestBatch 기본값이 빈 목록과 current_app_date_iso() 기준일인지 검증한다.
- TC-04: StructuredRequestBatch가 StructuredRequest 목록을 담는지 검증한다.
- TC-05: week02_tools()가 Week 1 tool 3개를 그대로 반환하는지 검증한다.
- TC-06: week02_system_prompt()에 구조화 규칙과 기준일이 담기는지 검증한다.
- TC-07: LLM이 자연어 요청을 StructuredRequestBatch structured_response로 반환하는지 검증한다.
  (PROXY_TOKEN이 없으면 건너뛴다.)

실행:
    python test/test_week02_structured_request.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError

from fixed.config import CONFIG
from fixed.runtime_clock import current_app_date_iso
from student_parts.week01_wake_up_nana import week01_tools
from student_parts.week02_structure_natural_language_requests import (
    StructuredRequest,
    StructuredRequestBatch,
    build_week02_agent,
    week02_system_prompt,
    week02_tools,
)


def test_structured_request_defaults() -> None:
    request = StructuredRequest(kind="personal_schedule")

    assert request.kind == "personal_schedule"
    assert request.title is None
    assert request.date is None
    assert request.start_time is None
    assert request.end_time is None
    assert request.members == []
    assert request.priority is None
    assert request.reason is None
    assert request.original_text == ""


def test_structured_request_rejects_unknown_kind() -> None:
    try:
        StructuredRequest(kind="lunch_menu")
    except ValidationError:
        return
    raise AssertionError("RequestKind 밖의 kind 값이 허용되었습니다.")


def test_structured_request_batch_defaults() -> None:
    batch = StructuredRequestBatch()

    assert batch.requests == []
    assert batch.base_date == current_app_date_iso()


def test_structured_request_batch_holds_requests() -> None:
    request = StructuredRequest(
        kind="personal_schedule",
        title="회의",
        date="2026-07-14",
        start_time="15:00",
        members=["철수"],
        original_text="다음 주 화요일 오후 3시에 철수랑 회의 잡아줘",
    )
    batch = StructuredRequestBatch(requests=[request])

    assert len(batch.requests) == 1
    assert batch.requests[0].title == "회의"
    assert batch.requests[0].members == ["철수"]


def test_week02_tools_reuse_week01_tools() -> None:
    tools = week02_tools()

    assert tools == week01_tools()
    assert [tool.name for tool in tools] == [
        "personal_create_schedule",
        "personal_list_schedules",
        "personal_delete_schedule",
    ]


def test_week02_system_prompt_contains_structuring_rules() -> None:
    prompt = week02_system_prompt()

    assert "StructuredRequestBatch" in prompt
    assert "structured_response" in prompt
    assert "created_schedule" in prompt
    assert current_app_date_iso() in prompt
    assert "SQLite" in prompt


def test_llm_structured_response_flow() -> None:
    if not CONFIG.has_openai_key:
        print("PROXY_TOKEN이 없어 LLM 연동 검증을 건너뜁니다.")
        return

    kanana_agent = build_week02_agent()
    result = kanana_agent.invoke(
        {"messages": [{"role": "user", "content": "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"}]}
    )
    structured_response = result["structured_response"]

    assert isinstance(structured_response, StructuredRequestBatch)
    assert structured_response.requests
    assert structured_response.base_date == current_app_date_iso()

    first_request = structured_response.requests[0]
    assert first_request.kind == "personal_schedule"
    assert "철수" in first_request.members

    print(structured_response)


if __name__ == "__main__":
    test_structured_request_defaults()
    test_structured_request_rejects_unknown_kind()
    test_structured_request_batch_defaults()
    test_structured_request_batch_holds_requests()
    test_week02_tools_reuse_week01_tools()
    test_week02_system_prompt_contains_structuring_rules()
    test_llm_structured_response_flow()
    print("2주차 자연어 요청 구조화 검증 통과")
