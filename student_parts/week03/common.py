from __future__ import annotations
from pydantic import ValidationError
import json
from typing import Any

from fixed.config import CONFIG
from fixed.app_store import AppSQLiteStore


def _store() -> AppSQLiteStore:
    return AppSQLiteStore(CONFIG.app_db_path)


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


def tool_result(tool_name: str, *, ok: bool = True, **payload: Any) -> dict[str, Any]:
    """Week 3 tool들이 공통으로 쓰는 JSON payload 껍데기를 만듭니다."""

    return {"ok": ok, "tool_name": tool_name, **payload}

def validation_error_result(
    tool_name: str,
    error: ValidationError,
) -> dict[str, Any]:
    return tool_result(
        tool_name,
        ok=False,
        error="validation_failed",
        validation_errors=[
            {
                "field": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ],
    )


def validation_error_payload(
    tool_name: str,
    error: ValidationError,
) -> str:
    return json_payload(
        validation_error_result(tool_name, error)
    )


def make_validation_error_handler(tool_name: str):
    def handler(error: ValidationError) -> str:
        return validation_error_payload(tool_name, error)

    return handler