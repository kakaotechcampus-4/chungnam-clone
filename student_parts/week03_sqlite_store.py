from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from fixed.config import CONFIG
from fixed.stores import AppSQLiteStore


STORE = AppSQLiteStore(CONFIG.app_db_path)


def _coerce_payload(payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


@tool
def save_structured_request(payload: dict[str, Any] | str) -> str:
    """2주차 구조화 출력 페이로드를 정규화된 SQLite 테이블에 저장합니다."""

    # [3주차][학생 구현]
    # 2주차의 구조화 출력 결과를 structured_requests에 저장하고,
    # kind에 따라 schedules/todos/reminders 중 알맞은 테이블에도 정규화 저장하세요.
    #
    # [참고 답안]
    saved = STORE.save_structured_request(_coerce_payload(payload))
    return json.dumps({"ok": True, "tool_name": "save_structured_request", **saved}, ensure_ascii=False)


@tool
def list_saved_requests(
    kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """SQLite에 저장된 구조화 요청 목록을 조회합니다."""

    # [3주차][학생 구현]
    # kind/date_from/date_to 필터를 SQL WHERE 조건으로 반영해 저장 결과를 조회하세요.
    #
    # [참고 답안]
    rows = STORE.list_saved_requests(kind=kind, date_from=date_from, date_to=date_to)
    return json.dumps({"ok": True, "tool_name": "list_saved_requests", "rows": rows}, ensure_ascii=False)


@tool
def get_saved_request(request_id: str) -> str:
    """request_id로 구조화 요청 행 하나를 조회합니다."""

    # [3주차][학생 구현]
    # request_id로 structured_requests 행 하나를 찾아 반환하세요.
    #
    # [참고 답안]
    row = STORE.get_saved_request(request_id)
    return json.dumps({"ok": True, "tool_name": "get_saved_request", "row": row}, ensure_ascii=False)


def save_structured_request_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(save_structured_request.invoke({"payload": payload}))


def list_saved_request_dicts(
    kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    return json.loads(
        list_saved_requests.invoke({"kind": kind, "date_from": date_from, "date_to": date_to})
    )["rows"]
