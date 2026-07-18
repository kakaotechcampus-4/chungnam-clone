from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from fixed.app_store import AppSQLiteStore
from student_parts.week02_structure_natural_language_requests import (
    StructuredRequest,
    extract_structured_request,
)
from student_parts.week03.schemas import SaveStructuredRequestInput
from student_parts.week03.common import _store, json_payload, tool_result,make_validation_error_handler


@tool
def save_request(query: str) -> str:
    """사용자의 자연어 요청을 구조화하여 SQLite에 저장합니다."""
    batch = extract_structured_request(query)

    results = [
        save_structured_request_payload(request)
        for request in batch.requests
    ]

    return json_payload(
    tool_result(
        "save_request",
        saved_count=len(results),
        results=results,
    )
)


def _save_input_from(value: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str) -> SaveStructuredRequestInput:
    """저장 입력을 SaveStructuredRequestInput 하나로 모읍니다."""

    if isinstance(value, SaveStructuredRequestInput):
        return value

    if isinstance(value, StructuredRequest):
        value = value.model_dump()

    elif isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            batch=extract_structured_request(value)
            
            if len(batch.requests)!=1:
                raise ValueError("저장할 요청은 하나여야 합니다.")
            
            value=batch.requests[0].model_dump()

    return SaveStructuredRequestInput.model_validate(value)


def save_structured_request_payload(
    request: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str,
    *,
    store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    """검증된 structured request를 앱 DB에 저장합니다."""

    validated = _save_input_from(request)
    app_store = store or _store()

    saved = app_store.save_structured_request(
        validated.model_dump(exclude_none=True)
    )

    return tool_result("save_structured_request", **saved)

save_request.handle_validation_error = (
    make_validation_error_handler("save_request")
)