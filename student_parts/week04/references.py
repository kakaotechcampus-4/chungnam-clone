from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from fixed.reference_store import PersonalReferenceStore
from student_parts.week04.common import json_payload, safe_limit
from student_parts.week04.schemas import (
    AddPersonalReferenceInput,
    SearchPersonalReferencesInput,
)
from student_parts.week04.stores import REFERENCE_STORE


def add_personal_reference_dict(
    reference_store: PersonalReferenceStore,
    *,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """개인 참고자료를 vector store에 추가하고 backend 정보를 반환합니다."""

    stored_reference = reference_store.add_personal_reference(
        title=title,
        content=content,
        tags=tags or [],
    )

    reference = dict(stored_reference)
    reference_backend = reference.pop("backend")

    return {
        "reference_backend": reference_backend,
        "reference": reference,
    }

def search_personal_reference_hits(
    reference_store: PersonalReferenceStore,
    *,
    query: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """ChromaDB 검색 결과를 tool이 바로 반환하기 쉬운 hit 구조로 정리합니다."""

    references = reference_store.search_personal_references(
        query=query,
        limit=top_k,
    )

    return [
        {
            "id": reference.get("id"),
            "content": reference.get("content", ""),
            "distance": reference.get("distance"),
            "metadata": {
                "title": reference.get("title", ""),
                "tags": reference.get("tags", ""),
            },
        }
        for reference in references
    ]


@tool(args_schema=AddPersonalReferenceInput)
def add_personal_reference(title: str, content: str, tags: list[str] | None = None) -> str:
    """개인 참고자료를 ChromaDB에 추가합니다."""

    payload = add_personal_reference_dict(
        REFERENCE_STORE,
        title=title,
        content=content,
        tags=tags or [],
    )
    return json_payload(payload)

@tool(args_schema=SearchPersonalReferencesInput)
def search_personal_references(query: str, top_k: int = 2) -> str:
    """개인 참고자료를 ChromaDB와 OpenAI embedding 기반으로 검색합니다."""

    limit = safe_limit(
        top_k,
        default=2,
        maximum=20,
    )
    hits = search_personal_reference_hits(
        REFERENCE_STORE,
        query=query,
        top_k=limit,
    )
    return json_payload({"hits": hits})
