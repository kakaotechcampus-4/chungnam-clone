from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
MCP_TOOL_NAMES = {
    "search_previous_conversations",
    "load_conversation_messages",
    "extract_schedules_from_history",
    "collect_member_schedules",
    "list_shared_schedules",
}


@dataclass(frozen=True)
class BatchCase:
    id: str
    category: str
    question: str
    expected_tools: tuple[str, ...]
    expected_arguments: dict[str, Any]
    answer_terms: tuple[str, ...]


CASES = (
    BatchCase(
        "conversation_search_01",
        "conversation_search",
        "철수와 나눈 과거 대화에서 API 연동 실습 관련 기록을 찾아줘.",
        ("search_previous_conversations",),
        {"query_contains": "API 연동 실습", "member_names": ["철수"], "include_messages": False},
        ("API 연동 실습",),
    ),
    BatchCase(
        "conversation_search_02",
        "conversation_search",
        "영희의 이전 대화에서 콘텐츠 점검 관련 기록을 검색해줘.",
        ("search_previous_conversations",),
        {"query_contains": "콘텐츠 점검", "member_names": ["영희"], "include_messages": False},
        ("콘텐츠 점검",),
    ),
    BatchCase(
        "conversation_search_03",
        "conversation_search",
        "서연과 나눈 과거 대화에서 UX 워크숍 이야기를 찾아줘.",
        ("search_previous_conversations",),
        {"query_contains": "UX 워크숍", "member_names": ["서연"], "include_messages": False},
        ("UX 워크숍",),
    ),
    BatchCase(
        "conversation_search_04",
        "conversation_search",
        "하린의 이전 대화 중 파트너 콜 관련 내용을 검색해줘.",
        ("search_previous_conversations",),
        {"query_contains": "파트너 콜", "member_names": ["하린"], "include_messages": False},
        ("파트너 콜",),
    ),
    BatchCase(
        "conversation_load_01",
        "conversation_load",
        "민준의 과거 대화에서 백엔드 리뷰를 검색한 다음 그 대화 전체 내용을 보여줘.",
        ("search_previous_conversations",),
        {"query_contains": "백엔드 리뷰", "member_names": ["민준"], "include_messages": True},
        ("데이터 정리", "백엔드 리뷰", "운영 회의"),
    ),
    BatchCase(
        "conversation_load_02",
        "conversation_load",
        "철수의 과거 대화에서 고객 인터뷰를 검색하고 해당 대화의 전체 메시지를 불러와줘.",
        ("search_previous_conversations",),
        {"query_contains": "고객 인터뷰", "member_names": ["철수"], "include_messages": True},
        ("API 연동 실습", "고객 인터뷰", "QA 리뷰"),
    ),
    BatchCase(
        "conversation_load_03",
        "conversation_load",
        "영희의 이전 대화에서 디자인 피드백을 찾고 그 대화 전문을 보여줘.",
        ("search_previous_conversations",),
        {"query_contains": "디자인 피드백", "member_names": ["영희"], "include_messages": True},
        ("디자인 피드백", "콘텐츠 점검", "발표 리허설"),
    ),
    BatchCase(
        "conversation_load_04",
        "conversation_load",
        "지훈의 과거 대화에서 보안 점검을 검색한 뒤 전체 대화 내용을 확인해줘.",
        ("search_previous_conversations",),
        {"query_contains": "보안 점검", "member_names": ["지훈"], "include_messages": True},
        ("모델 평가", "보안 점검", "릴리즈 회의"),
    ),
    BatchCase(
        "external_schedule_01",
        "external_schedule",
        "민준의 2026년 7월 7일부터 7월 17일까지 일정을 알려줘.",
        ("extract_schedules_from_history",),
        {"member_names": ["민준"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("데이터 정리", "백엔드 리뷰", "운영 회의"),
    ),
    BatchCase(
        "external_schedule_02",
        "external_schedule",
        "서연의 2026년 7월 7일부터 7월 17일까지 바쁜 시간을 조회해줘.",
        ("extract_schedules_from_history",),
        {"member_names": ["서연"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("사용자 테스트", "보고서 정리", "UX 워크숍"),
    ),
    BatchCase(
        "external_schedule_03",
        "external_schedule",
        "지훈이 2026년 7월 7일부터 7월 17일까지 언제 바쁜지 알려줘.",
        ("extract_schedules_from_history",),
        {"member_names": ["지훈"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("모델 평가", "보안 점검", "릴리즈 회의"),
    ),
    BatchCase(
        "external_schedule_04",
        "external_schedule",
        "하린의 2026년 7월 7일부터 7월 17일까지 일정을 조회해줘.",
        ("extract_schedules_from_history",),
        {"member_names": ["하린"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("온보딩 세션", "파트너 콜", "회고 준비"),
    ),
    BatchCase(
        "collect_schedule_01",
        "collect_schedule",
        "내 일정과 민준의 일정을 2026년 7월 7일부터 7월 17일까지 함께 조회해줘.",
        ("collect_member_schedules",),
        {"member_names": ["민준"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("민준", "데이터 정리", "백엔드 리뷰", "운영 회의"),
    ),
    BatchCase(
        "collect_schedule_02",
        "collect_schedule",
        "2026년 7월 7일부터 7월 17일까지 내 일정과 서연의 바쁜 시간을 같이 보여줘.",
        ("collect_member_schedules",),
        {"member_names": ["서연"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("서연", "사용자 테스트", "보고서 정리", "UX 워크숍"),
    ),
    BatchCase(
        "collect_schedule_03",
        "collect_schedule",
        "나와 철수의 2026년 7월 7일부터 7월 17일까지 일정을 한 번에 모아줘.",
        ("collect_member_schedules",),
        {"member_names": ["철수"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("철수", "API 연동 실습", "고객 인터뷰", "QA 리뷰"),
    ),
    BatchCase(
        "collect_schedule_04",
        "collect_schedule",
        "내 일정과 영희의 일정을 2026년 7월 7일부터 7월 17일까지 같이 확인해줘.",
        ("collect_member_schedules",),
        {"member_names": ["영희"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("영희", "디자인 피드백", "콘텐츠 점검", "발표 리허설"),
    ),
    BatchCase(
        "shared_schedule_01",
        "shared_schedule",
        "공유 일정 저장소에 등록된 철수의 2026년 7월 7일부터 7월 17일까지 일정을 직접 확인해줘.",
        ("list_shared_schedules",),
        {"member_names": ["철수"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("API 연동 실습", "고객 인터뷰", "QA 리뷰"),
    ),
    BatchCase(
        "shared_schedule_02",
        "shared_schedule",
        "공유 일정 저장소에서 민준의 2026년 7월 7일부터 7월 17일까지 등록 일정을 조회해줘.",
        ("list_shared_schedules",),
        {"member_names": ["민준"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("데이터 정리", "백엔드 리뷰", "운영 회의"),
    ),
    BatchCase(
        "shared_schedule_03",
        "shared_schedule",
        "공유 일정 저장소에 있는 지훈의 2026년 7월 7일부터 7월 17일까지 일정을 직접 보여줘.",
        ("list_shared_schedules",),
        {"member_names": ["지훈"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("모델 평가", "보안 점검", "릴리즈 회의"),
    ),
    BatchCase(
        "shared_schedule_04",
        "shared_schedule",
        "공유 일정 저장소에서 하린의 2026년 7월 7일부터 7월 17일까지 일정을 직접 확인해줘.",
        ("list_shared_schedules",),
        {"member_names": ["하린"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ("온보딩 세션", "파트너 콜", "회고 준비"),
    ),
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    position = 0
    for name in actual:
        if name == expected[position]:
            position += 1
            if position == len(expected):
                return True
    return False


def _matching_tool_calls(expected: list[str], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    position = 0
    for call in calls:
        if position < len(expected) and call.get("tool_name") == expected[position]:
            matched.append(call)
            position += 1
    return matched if position == len(expected) else []


def _arguments_match(case: BatchCase, calls: list[dict[str, Any]]) -> bool:
    matched = _matching_tool_calls(list(case.expected_tools), calls)
    if not matched:
        return False

    arguments = _as_dict(matched[0].get("arguments"))
    for key, expected in case.expected_arguments.items():
        if key == "query_contains":
            if _normalize_text(expected) not in _normalize_text(arguments.get("query")):
                return False
            continue
        if key == "member_names":
            actual_members = arguments.get("member_names") or []
            if isinstance(actual_members, str):
                actual_members = [actual_members]
            normalized_members = {_normalize_text(member) for member in actual_members}
            if not all(_normalize_text(member) in normalized_members for member in expected):
                return False
            continue
        if key == "include_messages":
            if _as_bool(arguments.get(key), default=False) is not expected:
                return False
            continue
        if _normalize_text(arguments.get(key)) != _normalize_text(expected):
            return False
    return True


def _tool_result_payload(events: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    for event in events:
        if event.get("event") == "tool_result" and event.get("tool_name") == tool_name:
            return _as_dict(event.get("content"))
    return {}


def _result_matches(case: BatchCase, events: list[dict[str, Any]]) -> bool:
    payload = _tool_result_payload(events, case.expected_tools[-1])
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return False
    if case.category == "conversation_load":
        return any(isinstance(row, dict) and row.get("messages") for row in rows)
    return True


def _answer_matches(case: BatchCase, answer: str) -> bool:
    normalized_answer = _normalize_text(answer)
    return all(_normalize_text(term) in normalized_answer for term in case.answer_terms)


def _trace_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    events = trace.get("events") or []
    return [event for event in events if isinstance(event, dict)]


def run_case(case: BatchCase, repeat: int, run_id: str) -> dict[str, Any]:
    from fixed.session_scope import conversation_session_scope
    from fixed.week_agent_registry import run_active_week_agent

    started = time.perf_counter()
    result = None
    raised_error: BaseException | None = None
    try:
        with conversation_session_scope(f"batch:{run_id}:{case.id}:{repeat}"):
            result = run_active_week_agent(
                5,
                [{"role": "user", "content": case.question}],
            )
    except BaseException as exc:
        raised_error = exc
    elapsed = round(time.perf_counter() - started, 3)

    if result is None:
        answer = ""
        trace: dict[str, Any] = {}
    else:
        answer = result.answer
        trace = result.trace if isinstance(result.trace, dict) else {}

    events = _trace_events(trace)
    tool_calls = [
        {
            "tool_name": event.get("tool_name"),
            "arguments": event.get("arguments"),
            "id": event.get("id"),
        }
        for event in events
        if event.get("event") == "tool_call"
    ]
    actual_tools = [str(call.get("tool_name")) for call in tool_calls if call.get("tool_name")]
    expected_tools = list(case.expected_tools)

    error = str(raised_error) if raised_error else trace.get("error")
    error_type = type(raised_error).__name__ if raised_error else trace.get("error_type")
    core_checks = {
        "execution": error is None,
        "tool_sequence": _is_subsequence(expected_tools, actual_tools),
        "result": _result_matches(case, events),
    }
    quality_checks = {
        "exact_tool_sequence": actual_tools == expected_tools,
        "arguments": _arguments_match(case, tool_calls),
        "answer": _answer_matches(case, answer),
    }
    warnings = [name for name, passed in quality_checks.items() if not passed]

    return {
        "id": case.id,
        "repeat": repeat,
        "input": case.question,
        "category": case.category,
        "expected_tools": expected_tools,
        "actual_tools": actual_tools,
        "tool_calls": actual_tools,
        "tool_call_details": tool_calls,
        "core_checks": core_checks,
        "quality_checks": quality_checks,
        "warnings": warnings,
        "passed": all(core_checks.values()),
        "answer": answer,
        "elapsed_seconds": elapsed,
        "error": error,
        "error_type": error_type,
    }


def _preflight_payload_ok(name: str, value: Any) -> bool:
    payload = _as_dict(value)
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return False
    if name == "search_previous_conversations_full":
        return any(isinstance(row, dict) and row.get("messages") for row in rows)
    return True


def run_preflight(run_id: str) -> list[dict[str, Any]]:
    from fixed.session_scope import conversation_session_scope
    from student_parts.week05.history_tools import (
        extract_schedules_from_history,
        search_previous_conversations,
    )
    from student_parts.week05.member_schedules import collect_member_schedules
    from student_parts.week05.shared_tools import list_shared_schedules

    checks = (
        (
            "search_previous_conversations_brief",
            search_previous_conversations,
            {
                "query": "API 연동 실습",
                "member_names": ["철수"],
                "limit": 5,
                "include_messages": False,
            },
        ),
        (
            "search_previous_conversations_full",
            search_previous_conversations,
            {
                "query": "백엔드 리뷰",
                "member_names": ["민준"],
                "limit": 5,
                "include_messages": True,
            },
        ),
        (
            "extract_schedules_from_history",
            extract_schedules_from_history,
            {"member_names": ["민준"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ),
        (
            "list_shared_schedules",
            list_shared_schedules,
            {"member_names": ["철수"], "date_from": "2026-07-07", "date_to": "2026-07-17", "limit": 50},
        ),
        (
            "collect_member_schedules",
            collect_member_schedules,
            {"member_names": ["민준"], "date_from": "2026-07-07", "date_to": "2026-07-17"},
        ),
    )

    results: list[dict[str, Any]] = []
    for name, tool, arguments in checks:
        started = time.perf_counter()
        error = None
        error_type = None
        output: Any = None
        try:
            with conversation_session_scope(f"batch:{run_id}:preflight:{name}"):
                output = tool.invoke(arguments)
            passed = _preflight_payload_ok(name, output)
        except Exception as exc:
            passed = False
            error = str(exc)
            error_type = type(exc).__name__
        results.append(
            {
                "name": name,
                "passed": passed,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error": error,
                "error_type": error_type,
            }
        )
        print(f"[PREFLIGHT {'PASS' if passed else 'FAIL'}] {name} {results[-1]['elapsed_seconds']}s")
    return results


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def build_summary(
    results: list[dict[str, Any]],
    preflight: list[dict[str, Any]],
    *,
    cache_warm_before_cases: bool,
) -> dict[str, Any]:
    total = len(results)
    passed = sum(bool(result["passed"]) for result in results)
    errors = sum(result.get("error") is not None for result in results)
    elapsed_values = [float(result["elapsed_seconds"]) for result in results]

    core_checks: dict[str, dict[str, int]] = {}
    quality_checks: dict[str, dict[str, int]] = {}
    for section_name, target in (("core_checks", core_checks), ("quality_checks", quality_checks)):
        names = {name for result in results for name in result.get(section_name, {})}
        for name in sorted(names):
            target[name] = {
                "passed": sum(bool(result.get(section_name, {}).get(name)) for result in results),
                "total": total,
            }

    category_counts: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["category"]].append(result)
    for category, category_results in sorted(grouped.items()):
        category_passed = sum(bool(result["passed"]) for result in category_results)
        category_total = len(category_results)
        category_counts[category] = {
            "passed": category_passed,
            "total": category_total,
            "accuracy": round(category_passed / category_total, 4) if category_total else 0.0,
        }

    tool_sequences = Counter(
        " -> ".join(result["actual_tools"]) if result["actual_tools"] else "(none)"
        for result in results
    )
    tool_call_counts = Counter(
        tool_name
        for result in results
        for tool_name in result["actual_tools"]
    )
    warning_counts = Counter(
        warning
        for result in results
        for warning in result["warnings"]
    )
    total_mcp_calls = sum(tool_call_counts[name] for name in MCP_TOOL_NAMES)
    discovery_starts = 0 if cache_warm_before_cases or total_mcp_calls == 0 else 1
    estimated_case_process_starts = total_mcp_calls + discovery_starts
    preflight_calls = len(preflight)
    estimated_preflight_process_starts = preflight_calls + (1 if preflight_calls else 0)

    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "errors": errors,
        "average_seconds": round(statistics.mean(elapsed_values), 3) if elapsed_values else 0.0,
        "latency_seconds": {
            "total": round(sum(elapsed_values), 3),
            "average": round(statistics.mean(elapsed_values), 3) if elapsed_values else 0.0,
            "p50": _percentile(elapsed_values, 0.50),
            "p95": _percentile(elapsed_values, 0.95),
        },
        "core_checks": core_checks,
        "quality_checks": quality_checks,
        "warning_counts": dict(sorted(warning_counts.items())),
        "by_category": category_counts,
        "tool_sequences": dict(sorted(tool_sequences.items())),
        "tool_call_counts": dict(sorted(tool_call_counts.items())),
        "total_mcp_calls": total_mcp_calls,
        "estimated_mcp_process_starts": estimated_case_process_starts,
        "mcp_process_start_estimate": {
            "case_cache_warm_at_start": cache_warm_before_cases,
            "case_tool_invocations": total_mcp_calls,
            "case_discovery_starts": discovery_starts,
            "case_total_starts": estimated_case_process_starts,
            "preflight_tool_invocations": preflight_calls,
            "preflight_total_starts": estimated_preflight_process_starts,
            "run_total_starts": estimated_case_process_starts + estimated_preflight_process_starts,
            "assumption": "cached tool discovery starts one MCP process; each MCP tool invocation starts one stdio process",
        },
        "preflight": {
            "passed": sum(bool(item["passed"]) for item in preflight),
            "total": len(preflight),
            "results": preflight,
        },
    }


def build_report(
    label: str,
    created_at: str,
    results: list[dict[str, Any]],
    preflight: list[dict[str, Any]],
    *,
    cache_warm_before_cases: bool,
) -> dict[str, Any]:
    return {
        "label": label,
        "created_at": created_at,
        "active_week": 5,
        "case_count": len(results),
        "summary": build_summary(
            results,
            preflight,
            cache_warm_before_cases=cache_warm_before_cases,
        ),
        "results": results,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def default_output_path(label: str, created_at: datetime) -> Path:
    safe_label = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", label).strip("-") or "run"
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    return Path("results") / f"week05_batch_{safe_label}_{timestamp}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Week05 MCP 도구 선택·성능 배치 테스트")
    parser.add_argument("--label", default="after-optimization")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--category", action="append", choices=sorted({case.category for case in CASES}))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat는 1 이상이어야 합니다.")

    from fixed.config import CONFIG
    from student_parts.week05.mcp_client import (
        _LOCAL_MCP_TOOLS_CACHE,
        clear_local_mcp_tools_cache,
    )

    created_at_dt = datetime.now(KST)
    created_at = created_at_dt.isoformat()
    run_id = f"{created_at_dt.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = args.output or default_output_path(args.label, created_at_dt)
    selected_cases = [case for case in CASES if not args.category or case.category in args.category]

    clear_local_mcp_tools_cache()
    preflight = [] if args.skip_preflight else run_preflight(run_id)
    cache_warm_before_cases = bool(_LOCAL_MCP_TOOLS_CACHE)

    results: list[dict[str, Any]] = []
    initial_report = build_report(
        args.label,
        created_at,
        results,
        preflight,
        cache_warm_before_cases=cache_warm_before_cases,
    )
    write_report(output_path, initial_report)

    if preflight and not all(item["passed"] for item in preflight):
        initial_report["summary"]["aborted_reason"] = "preflight_failed"
        write_report(output_path, initial_report)
        print("preflight가 실패하여 LLM 배치를 실행하지 않습니다.")
        print(f"결과: {output_path.resolve()}")
        return 2
    if not CONFIG.has_openai_key:
        initial_report["summary"]["aborted_reason"] = "missing_proxy_token"
        write_report(output_path, initial_report)
        print(".env의 PROXY_TOKEN이 없어 LLM 배치를 실행하지 않습니다.")
        print(f"preflight 결과: {output_path.resolve()}")
        return 2

    total_attempts = len(selected_cases) * args.repeat
    current_attempt = 0
    for repeat in range(1, args.repeat + 1):
        for case in selected_cases:
            current_attempt += 1
            print(f"[{current_attempt}/{total_attempts}] {case.id} repeat={repeat}")
            result = run_case(case, repeat, run_id)
            results.append(result)
            print(
                f"  {'PASS' if result['passed'] else 'FAIL'} "
                f"tools={result['actual_tools']} elapsed={result['elapsed_seconds']}s"
            )
            write_report(
                output_path,
                build_report(
                    args.label,
                    created_at,
                    results,
                    preflight,
                    cache_warm_before_cases=cache_warm_before_cases,
                ),
            )

    report = build_report(
        args.label,
        created_at,
        results,
        preflight,
        cache_warm_before_cases=cache_warm_before_cases,
    )
    write_report(output_path, report)
    summary = report["summary"]
    print(
        f"완료: {summary['passed']}/{summary['total']} "
        f"({summary['accuracy'] * 100:.2f}%), 평균 {summary['average_seconds']}초"
    )
    print(f"결과: {output_path.resolve()}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
