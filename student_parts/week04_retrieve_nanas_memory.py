from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fixed.config import CONFIG
from fixed.conversation_rag_store import ConversationRAGStore
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from fixed.app_store import AppSQLiteStore
from fixed.reference_store import PersonalReferenceStore
from fixed.session_scope import DEFAULT_SESSION_SCOPE, current_session_scope
from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week03_build_nanas_logbook import week03_prompt_parts, week03_tools


REFERENCE_STORE = PersonalReferenceStore(CONFIG.chroma_dir)
SQLITE_STORE = AppSQLiteStore(CONFIG.app_db_path)
CONVERSATION_RAG_STORE = ConversationRAGStore(CONFIG.chroma_dir)
_WEEK04_AGENT: Any | None = None

# 참고자료 검색 품질 기준. 앱의 실제 참고자료로 측정한 distance 분포에서 정했다.
#   관련 있는 질의 0.77~1.13 / 경계 1.46 / 무관한 질의 1.62 이상
REFERENCE_RELEVANCE_MAX_DISTANCE = 1.5
# 필요한 메모가 4위로 밀려 top_k=2에서 잘린 사례가 있어, 최소 후보 수를 확보한다.
# 이것은 "순위에서 잘리는 문제"에 대한 대응이고, 아래 임계값 판단과는 다른 문제를 다룬다.
REFERENCE_MIN_CANDIDATES = 4


# [4주차 수강생 구현 가이드]
#
# 목표
#   Nana가 "내가 적어 둔 참고자료", "SQLite에 저장된 일정/할 일 기록",
#   "앱에 저장된 일반 채팅 발화"를 구분해서 검색하게 합니다.
#   Week 4의 핵심은 RAG를 하나의 마법 함수로 보지 않고, 데이터 출처별 검색 tool을 분리하는 것입니다.
#
# 과제 구성
#   - 메인과제: 개인 참고자료를 추가하고, 참고자료와 SQLite 저장 기록을 출처별로 검색하는
#     RAG 세로 슬라이스를 완성합니다.
#   - 추가 과제: 앱 대화 발화를 ChromaDB에 lazy sync해 검색하는 agentic RAG와
#     이전 버전 호환 통합 검색까지 확장합니다.
#
# 구현 위치와 사용할 코드
#   - 이 파일(student_parts/week04_retrieve_nanas_memory.py)의 개인 참고자료/RAG tool을 구현합니다.
#   - 개인 참고자료 저장소는 fixed/reference_store.py의 PersonalReferenceStore이며,
#     이 파일 상단의 REFERENCE_STORE가 CONFIG.chroma_dir 기준 인스턴스입니다.
#   - SQLite 저장 요청 검색은 fixed/app_store.py의 AppSQLiteStore를 사용하고,
#     이 파일 상단의 SQLITE_STORE가 CONFIG.app_db_path 기준 인스턴스입니다.
#   - 일반 채팅 발화 검색은 fixed/conversation_rag_store.py의 ConversationRAGStore를 사용하고,
#     이 파일 상단의 CONVERSATION_RAG_STORE가 CONFIG.chroma_dir 기준 인스턴스입니다.
#   - 각 tool 입력은 Pydantic args_schema로 검증하고,
#     search_personal_reference_hits(), search_saved_request_rows(), search_conversation_message_rows()에서 조회 결과를 정리합니다.
#   - tool 함수 add_personal_reference/search_personal_references/search_saved_requests/search_conversation_messages는
#     위 helper 결과를 json_payload()로 감싼 JSON 문자열로 반환합니다.
#   - top_k/limit 보정은 이 파일의 safe_limit()를 사용해 tool 안에서 처리합니다.
#   - week04_tools()는 student_parts/week03_build_nanas_logbook.py의 week03_tools() 위에
#     Week 4 RAG tool을 누적해 agent에 공개합니다.
#
# 메인과제 구현 대상
#   1. add_personal_reference
#      - title/content/tags를 REFERENCE_STORE.add_personal_reference에 넘깁니다.
#      - tags가 None이면 빈 list로 바꿉니다.
#      - 이 tool 안에서 reference_backend와 reference가 있는 JSON payload를 완성합니다.
#
#   2. search_personal_references
#      - query와 top_k로 ChromaDB 개인 참고자료를 검색합니다.
#      - top_k는 이 tool 안에서 안전한 범위로 정리합니다.
#      - course repo 기준 계약에 맞게 top-level {"hits": [...]} JSON을 반환합니다.
#      - hit에는 id, content, distance, metadata(title/tags)가 들어가야 답변 근거로 쓰기 쉽습니다.
#
#   3. search_saved_requests
#      - SQLITE_STORE.search_saved_requests(query, limit)를 호출합니다.
#      - top_k는 이 tool 안에서 안전한 범위로 정리합니다.
#      - 검색 결과가 없으면 rows=[]를 그대로 반환합니다.
#      - course repo 기준 계약에 맞게 top-level {"rows": [...]} JSON을 반환합니다.
#
# 추가 과제 구현 대상
#   1. search_conversation_messages
#      - SQLite에 저장된 앱 대화 메시지를 ConversationRAGStore.sync_from_sqlite(...)로 ChromaDB에 lazy sync합니다.
#      - conversation_id를 명시하지 않으면 현재 대화 범위는 검색에서 제외해 "방금 한 말"이 과거 검색처럼 섞이지 않게 합니다.
#      - 반환 JSON에는 hits와 rows에 같은 결과를 넣고, context/rag_backend/sync도 함께 둡니다.
#      - hit에는 conversation_id, role, content 등 대화 근거가 있어야 하며, assistant 발화만으로 사실을 확정하지 않습니다.
#
# 출처 구분
#   search_personal_references는 ChromaDB + OpenAI embedding 기반 reference 검색입니다.
#   search_saved_requests는 SQLite structured_requests/schedules 계열 기록 검색입니다.
#   search_conversation_messages는 SQLite conversations/messages를 대화 단위 청크로 sync해 검색하는 agentic RAG입니다.
#   LLM이 질문 성격에 따라 둘 중 하나 또는 둘 다 선택하도록 prompt가 준비되어 있습니다.
#
# 참고 코드
#   search_nana_memory는 reference_backend와 context를 함께 확인하는 compatibility helper입니다.
#   학생 핵심 구현 대상은 add_personal_reference, search_personal_references,
#   search_saved_requests, search_conversation_messages 4개입니다.
#   week04_tools()는 Week 1-3 도구에 이 RAG 도구들을 누적합니다.
#
# 검증 방법
#   - 메인과제: 참고자료를 추가한 뒤 관련 질문을 입력하고 trace에서 search_personal_references 호출을 확인합니다.
#     저장된 일정/할 일 질문은 search_saved_requests가 호출되는지, 결과 JSON top-level 키가 각각 hits, rows인지 확인합니다.
#   - 추가 과제: 일반 채팅 발화 질문은 search_conversation_messages가 호출되고 현재 대화가 제외되는지 확인합니다.
#
# 함수별 동작 설명 ([메인]/[추가]/[공통]은 각 함수가 속한 과제 티어입니다)
#   - [공통] _decode_attendees(raw_attendees)
#     SQLite row의 attendees_json 문자열을 list로 바꿉니다. 깨진 JSON이나 list가 아닌 값은 빈 list로 처리합니다.
#
#   - [공통] json_payload(payload)
#     tool 응답 dict를 한글이 보존되는 JSON 문자열로 바꿉니다.
#
#   - [공통] safe_limit(limit, default, maximum)
#     LLM이나 사용자가 넘긴 limit/top_k 값을 int로 바꾸고 1 이상 maximum 이하로 제한합니다.
#
#   - [메인] AddPersonalReferenceInput / SearchPersonalReferencesInput / SearchSavedRequestsInput
#     개인 참고자료 추가, 개인 참고자료 검색, SQLite 저장 요청 검색 tool의 입력 스키마입니다.
#
#   - [추가] SearchConversationMessagesInput / SearchNanaMemoryInput
#     앱 대화 RAG 검색과 기존 호환용 통합 검색 tool의 입력 스키마입니다.
#
#   - [메인] add_personal_reference_dict(...)
#     PersonalReferenceStore에 참고자료를 저장하고, 어떤 backend에 저장됐는지와 저장된 reference row를 dict로 반환합니다.
#
#   - [메인] search_personal_reference_hits(...)
#     vector store 검색 결과를 id/content/distance/metadata 구조로 정리합니다. tool은 이 list를 hits로 감싸 반환합니다.
#
#   - [메인] search_saved_request_rows(...)
#     AppSQLiteStore의 저장 요청 검색 결과를 rows 배열로 반환합니다. 일정/할 일/알림 구조화 기록을 찾을 때 사용합니다.
#
#   - [추가] search_conversation_messages_dict(...)
#     SQLite 대화 기록을 ConversationRAGStore에 lazy sync한 뒤 ChromaDB 검색을 수행합니다.
#     현재 대화는 기본적으로 제외해 "방금 한 말"이 과거 검색 결과처럼 섞이지 않게 합니다.
#
#   - [추가] search_conversation_message_rows(...)
#     search_conversation_messages_dict(...)에서 hits만 꺼내는 내부 helper입니다.
#
#   - [메인] add_personal_reference(...)
#     참고자료 추가 tool입니다. title/content/tags를 받아 vector store에 저장하고 JSON 문자열을 반환합니다.
#
#   - [메인] search_personal_references(...)
#     개인 참고자료 전용 검색 tool입니다. top-level hits 키를 반환하므로 LLM이 근거 문서를 바로 읽을 수 있습니다.
#
#   - [메인] search_saved_requests(...)
#     SQLite에 저장된 structured request/schedule 기록 검색 tool입니다. top-level rows 키를 반환합니다.
#
#   - [추가] search_conversation_messages(...)
#     앱에 저장된 일반 대화 발화를 검색하는 RAG tool입니다. 일정 DB 검색과 다른 출처임을 context/rag_backend/sync로 함께 보여줍니다.
#
#   - [추가] search_nana_memory(...)
#     이전 버전 호환용 통합 검색 tool입니다. 개인 참고자료 hit와 SQLite 일정 chunk를 한 번에 묶어 context 문자열을 만듭니다.
#
#   - [공통] week04_tools()
#     Week 3까지의 tool에 Week 4 RAG tool들을 누적해 agent에 공개합니다.
#
#   - [공통] week04_system_prompt() / week04_prompt_parts()
#     질문 성격에 따라 reference, saved request, conversation RAG 중 맞는 tool을 고르도록 system prompt를 만듭니다.
#
#   - [공통] build_week04_agent() / build_week_agent()
#     Week 1~4 tool을 가진 agent를 만들고 재사용합니다.


def _decode_attendees(raw_attendees: str | None) -> list[str]:
    try:
        decoded = json.loads(raw_attendees or "[]")
    except Exception:
        return []
    return decoded if isinstance(decoded, list) else []


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


def safe_limit(limit: int, default: int = 5, maximum: int = 50) -> int:
    """사용자/LLM이 넘긴 limit 값을 안전한 양의 정수 범위로 보정합니다."""

    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


class AddPersonalReferenceInput(BaseModel):
    """개인 참고자료 추가 입력입니다."""

    title: str
    content: str
    tags: list[str] | None = None


class SearchPersonalReferencesInput(BaseModel):
    """개인 참고자료 검색 입력입니다."""

    query: str
    top_k: int = Field(default=2, ge=1, le=20)


class SearchSavedRequestsInput(BaseModel):
    """SQLite 저장 요청 검색 입력입니다."""

    query: str
    top_k: int = Field(default=3, ge=1, le=50)


class SearchConversationMessagesInput(BaseModel):
    """앱 대화 RAG 검색 입력입니다."""

    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    conversation_id: str | None = None


class SearchNanaMemoryInput(BaseModel):
    """Week 4 호환 통합 검색 입력입니다."""

    query: str
    date_from: str | None = None
    date_to: str | None = None
    attendee: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


def add_personal_reference_dict(
    reference_store: PersonalReferenceStore,
    *,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """개인 참고자료를 vector store에 추가하고 backend 정보를 반환합니다."""

    # 실제 임베딩 저장은 store가 담당한다. tags가 None이면 빈 list로 바꿔
    # 항상 list 타입으로 넘긴다(저장 메타데이터가 일관되게 유지되도록).
    return reference_store.add_personal_reference(title=title, content=content, tags=tags or [])


def search_personal_reference_hits(
    reference_store: PersonalReferenceStore,
    *,
    query: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    """ChromaDB 검색 결과를 tool이 바로 반환하기 쉬운 hit 구조로 정리합니다."""

    # store는 top_k가 아니라 limit 인자를 쓰므로 이름을 맞춰 넘긴다.
    raw_hits = reference_store.search_personal_references(query=query, limit=top_k)

    # store가 준 평평한 hit(id/title/content/tags/distance)를 가이드 계약 형식으로 재정리한다.
    # title/tags는 문서 본문이 아니라 부가정보이므로 metadata 안으로 묶는다.
    return [
        {
            "id": hit["id"],
            "content": hit["content"],
            "distance": hit["distance"],
            "metadata": {"title": hit.get("title", ""), "tags": hit.get("tags", "")},
        }
        for hit in raw_hits
    ]


def search_saved_request_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """SQLite 저장 요청을 검색하고 실제 검색 결과만 반환합니다."""

    # store가 structured_requests를 LIKE '%query%'로 검색해 row 목록을 준다.
    # top_k를 store의 limit으로 매핑한다. 결과가 없으면 빈 list가 그대로 반환된다.
    return sqlite_store.search_saved_requests(query=query, limit=top_k)


def search_conversation_messages_dict(
    sqlite_store: AppSQLiteStore,
    conversation_rag_store: ConversationRAGStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """SQLite 대화 목록을 lazy sync한 뒤 ChromaDB conversation RAG 결과를 반환합니다."""

    # ① SQLite 대화를 ChromaDB에 lazy sync — 바뀐 대화만 반영(sync 통계 반환).
    sync = conversation_rag_store.sync_from_sqlite(sqlite_store)

    # ② 현재 대화 제외 규칙: conversation_id를 명시하면 그 대화 안에서 검색하고,
    #    명시하지 않으면 지금 진행 중인 대화(current_session_scope)를 검색에서 제외한다.
    #    → "방금 내가 한 말"이 과거 검색 결과처럼 섞이는 것을 막는다.
    exclude = None if conversation_id else current_session_scope()
    hits = conversation_rag_store.search(
        query=query,
        top_k=top_k,
        exclude_conversation_id=exclude,
        conversation_id=conversation_id,
    )

    # ③ hits/rows(같은 데이터)와 함께 근거 문자열(context)·검색 backend·sync 통계를 담아 반환.
    return {
        "hits": hits,
        "rows": hits,
        "context": conversation_rag_store.context_from_hits(hits),
        "rag_backend": conversation_rag_store.backend_info(),
        "sync": sync,
    }


def search_conversation_message_rows(
    sqlite_store: AppSQLiteStore,
    *,
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """앱 SQLite에 저장된 일반 채팅 대화 청크를 RAG 검색합니다."""

    # worker가 조립한 결과에서 hits만 떼어 반환하는 얇은 헬퍼(모듈 싱글턴 store 사용).
    result = search_conversation_messages_dict(
        sqlite_store, CONVERSATION_RAG_STORE, query=query, top_k=top_k, conversation_id=conversation_id
    )
    return result["hits"]


@tool(args_schema=AddPersonalReferenceInput)
def add_personal_reference(title: str, content: str, tags: list[str] | None = None) -> str:
    """개인 참고자료를 ChromaDB에 추가합니다."""

    # 모듈 상단에 준비된 REFERENCE_STORE 싱글턴에 저장한다.
    saved = add_personal_reference_dict(REFERENCE_STORE, title=title, content=content, tags=tags)
    # 저장 위치(reference_backend)와 저장된 참고자료 본문(reference)을 나눠 반환한다.
    # LLM은 이 결과를 보고 "무엇을 어디에 기록했는지" 답할 수 있다.
    return json_payload(
        {
            "reference_backend": saved["backend"],
            "reference": {key: saved[key] for key in ("reference_id", "title", "content", "tags")},
        }
    )


@tool(args_schema=SearchPersonalReferencesInput)
def search_personal_references(query: str, top_k: int = 2) -> str:
    """개인 참고자료를 ChromaDB와 OpenAI embedding 기반으로 검색합니다."""

    # ① 요청 top_k를 1~20으로 보정하고, 최소 후보 수를 확보한다.
    #    top_k가 너무 작으면 필요한 메모가 순위에서 잘려 답변 근거가 빠진다.
    requested = max(safe_limit(top_k, default=2, maximum=20), REFERENCE_MIN_CANDIDATES)
    hits = search_personal_reference_hits(REFERENCE_STORE, query=query, top_k=requested)

    # ② 최상위 distance로 "이 결과를 근거로 써도 되는가"를 판단한다. distance가 클수록 의미가 먼 문서다.
    #    ①(최소 후보 확보)이 순위에서 잘리는 문제를 다룬다면, 이 판단은 결과의 사용 가능 여부를 다룬다.
    best_distance = hits[0]["distance"] if hits else None
    sufficient = best_distance is not None and best_distance <= REFERENCE_RELEVANCE_MAX_DISTANCE

    # ③ 근거가 약할 때 같은 질의로 개수만 늘리면 최상위 품질은 그대로이고 무관한 자료만 늘어난다.
    #    (측정: best 1.382 → 확장 후에도 1.382, 무관 자료 0건 → 1건 / 질의 재작성 시 best 0.547)
    #    그래서 여기서 확장하지 않고, 판단 결과만 돌려주어 질의 재작성이나 "못 찾음" 답변으로 이어지게 한다.
    retrieval: dict[str, Any] = {
        "requested_top_k": requested,
        "returned": len(hits),
        "best_distance": best_distance,
        "sufficient": sufficient,
    }
    if not sufficient:
        retrieval["hint"] = "관련도가 낮습니다. 질의를 더 구체적으로 바꿔 다시 검색하거나, 근거를 찾지 못했다고 답하세요."

    return json_payload({"hits": hits, "retrieval": retrieval})


@tool(args_schema=SearchSavedRequestsInput)
def search_saved_requests(query: str, top_k: int = 3) -> str:
    """SQLite에 저장된 구조화 일정/할 일/알림 row를 검색합니다. query에는 LLM이 고른 일정/할 일/알림 핵심어를 넣습니다."""

    # top_k를 1~50 범위로 보정한 뒤 헬퍼로 검색한다.
    rows = search_saved_request_rows(SQLITE_STORE, query=query, top_k=safe_limit(top_k, default=3, maximum=50))
    # 가이드 계약: top-level 키는 rows 하나. 결과가 없으면 rows=[]가 그대로 나간다.
    return json_payload({"rows": rows})


@tool(args_schema=SearchConversationMessagesInput)
def search_conversation_messages(
    query: str,
    top_k: int = 5,
    conversation_id: str | None = None,
) -> str:
    """앱 SQLite 대화 목록을 대화 단위 ChromaDB RAG로 검색합니다. query에는 LLM이 고른 짧은 핵심 명사나 구를 넣습니다."""

    # 모듈 싱글턴 store를 worker에 넘겨 검색하고 결과를 JSON 문자열로 반환한다.
    result = search_conversation_messages_dict(
        SQLITE_STORE,
        CONVERSATION_RAG_STORE,
        query=query,
        top_k=safe_limit(top_k, default=5, maximum=50),
        conversation_id=conversation_id,
    )
    return json_payload(result)


@tool(args_schema=SearchNanaMemoryInput)
def search_nana_memory(
    query: str,
    date_from: str | None = None,
    date_to: str | None = None,
    attendee: str | None = None,
    limit: int = 5,
) -> str:
    """개인 참고자료와 SQLite 저장 일정을 한 번에 검색하고 일정 chunk를 반환합니다."""

    # TODO: compatibility 통합 검색이 필요하면 개인 참고자료와 SQLite 일정 chunk를 함께 구성하세요.
    ...

def week04_tools() -> list[Any]:
    """3주차까지의 도구에 4주차 RAG 도구를 누적한 목록입니다."""

    return [
        *week03_tools(),
        add_personal_reference,
        search_personal_references,
        search_saved_requests,
        search_conversation_messages,
    ]


def week04_system_prompt() -> str:
    """4주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week04_prompt_parts())


def week04_prompt_parts() -> list[str]:
    """1~4주차 system prompt 조각을 누적합니다."""

    today = current_app_date_iso()

    return [
        *week03_prompt_parts(),
        (
            f"너는 4주차부터 기억을 검색해 오는 나나이기도 하다. 오늘은 {today}이다. "
            "질문에 답하기 전에, 근거가 필요한 질문이면 먼저 알맞은 검색 tool을 호출한다. "
            "검색 대상을 출처에 따라 구분한다: "
            "① 사용자가 자유롭게 적어 둔 메모·참고자료·선호(예: 회의 선호 시간, 점심 규칙)는 "
            "search_personal_references(ChromaDB 의미검색)로 찾는다. "
            "② 저장된 일정/할 일/알림 같은 구조화 기록은 search_saved_requests(SQLite 검색)로 찾는다. "
            "③ 지난 대화에서 무슨 이야기를 했는지 묻는 질문은 search_conversation_messages(대화 RAG)로 찾는다. "
            "한 질문에 여러 출처가 필요하면 해당 tool을 여러 개 호출해도 된다. "
            "검색 결과의 hits 또는 rows를 근거로 답하고, 근거가 없으면 지어내지 말고 못 찾았다고 말한다. "
            "3주차까지의 저장/조회/수정/삭제 규칙은 그대로 유지하되, 이번 주부터는 RAG 검색을 함께 사용한다. "
            # 시나리오 검증에서 발견한 경계 케이스 교정 규칙:
            "④ '~해도 돼?/괜찮아?/가능해?'처럼 가능 여부를 묻는 질문에는 다른 tool보다 먼저 "
            "search_personal_references를 호출해 관련 선호·규칙을 확인한 뒤 답한다. "
            "저장된 일정이 비어 있다는 것만으로 '가능하다'고 답하지 않는다. "
            "참고자료에 제약(예: 특정 요일이나 시간대를 피한다)이 있으면 그 제약을 답변에 그대로 언급한다. "
            "가능 여부를 묻는 질문은 일정 확정 요청이 아니므로 저장 tool은 호출하지 않는다. "
            "⑤ 질문이 어느 출처인지 모호하면(예: '팀 회의 정보') 참고자료와 저장기록을 모두 검색한 뒤 종합해 답한다. "
            "⑥ 종류를 특정하지 않은 일정 조회(예: '내일 일정 뭐야')는 개인 일정만 보는 "
            "personal_list_saved_schedules로 끝내지 말고 저장기록 전체(개인/그룹/할 일/알림)를 확인한다. "
            "이때 날짜나 종류가 분명하면 list_saved_requests에 date_from/date_to/kind 필터를 넘겨 조회하고, "
            "제목·내용의 키워드로 찾아야 하면 search_saved_requests를 쓴다. "
            "⑦ 참고자료 검색 질의에는 사용자 문장의 구체 조건(요일·시간대·대상 등)을 그대로 남긴다. "
            "'선호 시간'처럼 일반적인 표현으로 바꾸면 정작 필요한 메모가 상위에 오지 않는다. "
            "⑧ 참고자료 검색 결과의 retrieval.sufficient가 false면 지금 결과를 근거로 쓰기 어렵다는 뜻이다. "
            "같은 질의로 개수만 늘려 다시 부르지 말고, 질의를 더 구체적으로 바꿔(사용자 문장의 조건을 되살려) "
            "반드시 한 번은 다시 검색한 뒤에 결론을 낸다. 같은 질의를 그대로 반복하지 않는다. "
            "두 번째 검색도 sufficient가 false면 지어내지 말고 근거를 찾지 못했다고 답한다."
        ),
    ]


def build_week04_agent() -> object:
    """Week 1-4 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK04_AGENT
    if _WEEK04_AGENT is None:
        _WEEK04_AGENT = create_agent(
            model=chat_model(),
            tools=week04_tools(),
            system_prompt=week04_system_prompt(),
        )
    return _WEEK04_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week04_agent()
