"""Week 4 검색 tool 단위 테스트.

실행: uv run --with pytest pytest tests/test_week04_memory.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fixed.session_scope import conversation_session_scope  # noqa: E402
from fixed.store_base import new_id, now_iso  # noqa: E402
from student_parts.week04_retrieve_nanas_memory import (  # noqa: E402
    CONVERSATION_RAG_STORE,
    REFERENCE_MIN_CANDIDATES,
    REFERENCE_RELEVANCE_MAX_DISTANCE,
    REFERENCE_STORE,
    REFERENCE_WIDENED_CANDIDATES,
    SQLITE_STORE,
    AddPersonalReferenceInput,
    SearchConversationMessagesInput,
    SearchNanaMemoryInput,
    SearchPersonalReferencesInput,
    SearchSavedRequestsInput,
    add_personal_reference,
    add_personal_reference_dict,
    json_payload,
    safe_limit,
    search_conversation_message_rows,
    search_conversation_messages,
    search_conversation_messages_dict,
    search_personal_reference_hits,
    search_personal_references,
    search_saved_request_rows,
    search_saved_requests,
    week04_prompt_parts,
    week04_system_prompt,
    week04_tools,
    _decode_attendees,
)

MARKER = "[W4TEST]"


# ============================================================
# safe_limit — 경계 / 비정상 타입 전수
# ============================================================
@pytest.mark.parametrize(
    "limit, default, maximum, expected",
    [
        (5, 5, 50, 5),
        (1, 5, 50, 1),
        (50, 5, 50, 50),
        (0, 5, 50, 1),
        (-10, 5, 50, 1),
        (51, 5, 50, 50),
        (999, 5, 50, 50),
        ("7", 5, 50, 7),
        ("abc", 5, 50, 5),
        (None, 5, 50, 5),
        (3.9, 5, 50, 3),
        (2.0, 5, 50, 2),
        (50.0, 5, 50, 50),
        (True, 5, 50, 1),
        (False, 5, 50, 1),
        ([], 5, 50, 5),
        ({}, 5, 50, 5),
        (10, 3, 10, 10),
        (11, 3, 10, 10),
        (0, 3, 10, 1),
        (7, 3, 10, 7),
        (1, 1, 1, 1),
        (5, 1, 1, 1),
    ],
)
def test_safe_limit(limit, default, maximum, expected):
    # 경계·음수·비정수 입력이 [1, maximum] 정수로 보정되는지
    assert safe_limit(limit, default=default, maximum=maximum) == expected


def test_safe_limit_defaults():
    # 인자 생략 시 기본 default=5 / maximum=50 동작
    assert safe_limit(999) == 50
    assert safe_limit(-1) == 1
    assert safe_limit("bad") == 5
    assert safe_limit(5) == 5


# ============================================================
# _decode_attendees — JSON 파싱 방어
# ============================================================
@pytest.mark.parametrize(
    "raw, expected",
    [
        ('["민수", "지아"]', ["민수", "지아"]),
        ("[]", []),
        (None, []),
        ("", []),
        (" ", []),
        ("  \n ", []),
        ("not json", []),
        ('{"a": 1}', []),
        ("123", []),
        ('"hello"', []),
        ("true", []),
        ("null", []),
        ('["철수"]', ["철수"]),
        ("[1, 2, 3]", [1, 2, 3]),
        ("[[1]]", [[1]]),
        ('[{"x": 1}]', [{"x": 1}]),
    ],
)
def test_decode_attendees(raw, expected):
    # JSON list면 그대로, 그 외(None/빈문자/깨진JSON/비-list)는 빈 list로
    assert _decode_attendees(raw) == expected


# ============================================================
# json_payload — 한글 보존 + 왕복
# ============================================================
def test_json_payload_returns_str():
    # 반환 타입이 문자열인지
    assert isinstance(json_payload({"a": 1}), str)


def test_json_payload_empty_dict():
    # 빈 dict → "{}"
    assert json_payload({}) == "{}"


def test_json_payload_preserves_korean():
    # 한글이 \u 이스케이프 없이 그대로 보존되는지
    out = json_payload({"title": "회의", "tags": ["업무", "긴급"]})
    assert "회의" in out and "업무" in out
    assert "\\u" not in out


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"a": 1},
        {"rows": []},
        {"hits": [{"id": "x", "content": "한글", "distance": 0.5, "metadata": {"title": "제목", "tags": "t"}}]},
        {"nested": {"ko": "값", "list": ["가", "나"]}},
    ],
)
def test_json_payload_roundtrip(payload):
    # json.loads로 원래 구조가 그대로 복원되는지
    assert json.loads(json_payload(payload)) == payload


# ============================================================
# 입력 스키마 — 유효 / 범위초과 / 타입강제 / 필수누락
# ============================================================
@pytest.mark.parametrize("top_k", [1, 2, 10, 20])
def test_ref_input_valid_top_k(top_k):
    # 참고자료 검색 top_k 유효 범위(1~20) 통과
    assert SearchPersonalReferencesInput(query="q", top_k=top_k).top_k == top_k


@pytest.mark.parametrize("top_k", [0, -1, 21, 100])
def test_ref_input_rejects_out_of_range(top_k):
    # 범위(1~20) 밖 top_k 거부
    with pytest.raises(ValidationError):
        SearchPersonalReferencesInput(query="q", top_k=top_k)


@pytest.mark.parametrize("raw, expected", [(2.0, 2), ("5", 5), (20.0, 20)])
def test_ref_input_coerces_whole_number(raw, expected):
    # 정수형 float / 숫자 문자열은 int로 강제변환
    assert SearchPersonalReferencesInput(query="q", top_k=raw).top_k == expected


@pytest.mark.parametrize("bad", [2.5, "abc", "3.5", None])
def test_ref_input_rejects_bad_top_k_type(bad):
    # 소수·비숫자 문자열·None은 거부
    with pytest.raises(ValidationError):
        SearchPersonalReferencesInput(query="q", top_k=bad)


def test_ref_input_default_top_k():
    # top_k 기본값 2
    assert SearchPersonalReferencesInput(query="q").top_k == 2


def test_ref_input_query_required():
    # query는 필수 필드
    with pytest.raises(ValidationError):
        SearchPersonalReferencesInput(top_k=2)


def test_ref_input_empty_query_allowed():
    # 빈 문자열 query는 허용(길이 제약 없음)
    assert SearchPersonalReferencesInput(query="").query == ""


@pytest.mark.parametrize("top_k", [1, 25, 50])
def test_saved_input_valid_top_k(top_k):
    # 저장기록 검색 top_k 유효 범위(1~50) 통과
    assert SearchSavedRequestsInput(query="q", top_k=top_k).top_k == top_k


@pytest.mark.parametrize("top_k", [0, -1, 51, 999])
def test_saved_input_rejects_out_of_range(top_k):
    # 범위(1~50) 밖 top_k 거부
    with pytest.raises(ValidationError):
        SearchSavedRequestsInput(query="q", top_k=top_k)


@pytest.mark.parametrize("raw, expected", [(3.0, 3), ("10", 10)])
def test_saved_input_coerces_whole_number(raw, expected):
    # 정수형 float / 숫자 문자열 강제변환
    assert SearchSavedRequestsInput(query="q", top_k=raw).top_k == expected


def test_saved_input_default_top_k():
    # top_k 기본값 3
    assert SearchSavedRequestsInput(query="q").top_k == 3


def test_saved_input_query_required():
    # query는 필수 필드
    with pytest.raises(ValidationError):
        SearchSavedRequestsInput(top_k=3)


def test_add_input_requires_title_and_content():
    # title/content는 필수 — 하나만 있으면 거부
    with pytest.raises(ValidationError):
        AddPersonalReferenceInput(content="내용만")
    with pytest.raises(ValidationError):
        AddPersonalReferenceInput(title="제목만")


def test_add_input_tags_default_none():
    # tags 기본값 None
    assert AddPersonalReferenceInput(title="t", content="c").tags is None


def test_add_input_tags_list_ok():
    # tags에 문자열 리스트 허용
    assert AddPersonalReferenceInput(title="t", content="c", tags=["a", "b"]).tags == ["a", "b"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": 123, "content": "c"},
        {"title": "t", "content": 123},
        {"title": "t", "content": "c", "tags": "notalist"},
        {"title": "t", "content": "c", "tags": [1, 2]},
    ],
)
def test_add_input_rejects_wrong_types(kwargs):
    # 잘못된 타입(문자열 자리에 int, tags에 str/int리스트) 거부
    with pytest.raises(ValidationError):
        AddPersonalReferenceInput(**kwargs)


def test_conversation_input_default_top_k():
    # 대화검색 top_k 기본값 5
    assert SearchConversationMessagesInput(query="q").top_k == 5


@pytest.mark.parametrize("top_k", [0, -1, 51])
def test_conversation_input_rejects_out_of_range(top_k):
    # 범위(1~50) 밖 top_k 거부
    with pytest.raises(ValidationError):
        SearchConversationMessagesInput(query="q", top_k=top_k)


def test_conversation_input_conversation_id_optional():
    # conversation_id는 옵셔널(기본 None, 지정 가능)
    assert SearchConversationMessagesInput(query="q").conversation_id is None
    assert SearchConversationMessagesInput(query="q", conversation_id="conv_1").conversation_id == "conv_1"


def test_conversation_input_query_required():
    # query는 필수 필드
    with pytest.raises(ValidationError):
        SearchConversationMessagesInput(top_k=5)


def test_nana_input_defaults():
    # 호환 스키마 기본값(date_from/date_to/attendee=None, limit=5)
    m = SearchNanaMemoryInput(query="q")
    assert (m.date_from, m.date_to, m.attendee, m.limit) == (None, None, None, 5)


@pytest.mark.parametrize("limit", [1, 10, 20])
def test_nana_input_valid_limit(limit):
    # limit 유효 범위(1~20) 통과
    assert SearchNanaMemoryInput(query="q", limit=limit).limit == limit


@pytest.mark.parametrize("limit", [0, -1, 21])
def test_nana_input_rejects_out_of_range(limit):
    # 범위 밖 limit 거부
    with pytest.raises(ValidationError):
        SearchNanaMemoryInput(query="q", limit=limit)


def test_nana_input_query_required():
    # query는 필수 필드
    with pytest.raises(ValidationError):
        SearchNanaMemoryInput(limit=5)


# ============================================================
# search_saved_requests — SQLite (오프라인)
# ============================================================
@pytest.fixture()
def seeded_sqlite():
    # 마커 붙은 더미 저장기록 6건을 심고, 테스트 후 마커로 전부 삭제
    rows = [
        ("todo", f"{MARKER} 보고서 제출", "월말 마감이라 중요"),
        ("todo", f"{MARKER} 회의 자료 준비", "발표 전날까지"),
        ("reminder", f"{MARKER} 약 먹기", "아침 식후"),
        ("personal_schedule", f"{MARKER} 치과 예약", "스케일링"),
        ("group_schedule", f"{MARKER} 팀 회의", "주간 싱크"),
        ("unknown", f"{MARKER} 그냥 메모", "분류 불가"),
    ]
    with SQLITE_STORE.connect() as conn:
        for kind, title, reason in rows:
            conn.execute(
                """INSERT INTO structured_requests
                   (request_id, kind, title, date, start_time, end_time, members_json, priority, reason, raw_json, created_at)
                   VALUES (?, ?, ?, NULL, NULL, NULL, '[]', NULL, ?, ?, ?)""",
                (new_id("req"), kind, title, reason,
                 json.dumps({"kind": kind, "title": title}, ensure_ascii=False), now_iso()),
            )
    yield rows
    with SQLITE_STORE.connect() as conn:
        conn.execute("DELETE FROM structured_requests WHERE title LIKE ?", (f"{MARKER}%",))


def test_saved_helper_finds_all_marker_rows(seeded_sqlite):
    # 헬퍼가 마커로 심은 전건을 찾는지
    rows = search_saved_request_rows(SQLITE_STORE, query=MARKER, top_k=50)
    marker = [r["title"] for r in rows if (r.get("title") or "").startswith(MARKER)]
    assert len(marker) == len(seeded_sqlite)


def test_saved_helper_nonexistent_returns_empty(seeded_sqlite):
    # 없는 검색어 → 빈 리스트(예외 없음)
    assert search_saved_request_rows(SQLITE_STORE, query="없는것xyzzy", top_k=10) == []


def test_saved_helper_big_top_k_no_crash(seeded_sqlite):
    # 헬퍼에 큰 top_k를 직접 줘도 크래시 없음
    assert isinstance(search_saved_request_rows(SQLITE_STORE, query=MARKER, top_k=999), list)


def test_saved_tool_returns_rows_key(seeded_sqlite):
    # tool 반환 top-level에 rows 키
    out = json.loads(search_saved_requests.invoke({"query": MARKER, "top_k": 50}))
    assert "rows" in out and isinstance(out["rows"], list)
    assert len(out["rows"]) >= len(seeded_sqlite)


def test_saved_tool_keyword_title(seeded_sqlite):
    # 제목에 '회의' 든 기록을 키워드 검색으로 찾는지
    out = json.loads(search_saved_requests.invoke({"query": "회의", "top_k": 50}))
    titles = [r["title"] for r in out["rows"] if (r.get("title") or "").startswith(MARKER)]
    assert f"{MARKER} 회의 자료 준비" in titles
    assert f"{MARKER} 팀 회의" in titles


def test_saved_tool_matches_reason_field(seeded_sqlite):
    # 제목뿐 아니라 reason 필드도 검색 대상인지
    out = json.loads(search_saved_requests.invoke({"query": "스케일링", "top_k": 10}))
    assert f"{MARKER} 치과 예약" in [r["title"] for r in out["rows"]]


@pytest.mark.parametrize("nonexistent", ["존재하지않음xyzzy", "@@@", "1234567890zzz"])
def test_saved_tool_empty_result(seeded_sqlite, nonexistent):
    # 매칭 0건이면 rows=[]
    out = json.loads(search_saved_requests.invoke({"query": nonexistent, "top_k": 5}))
    assert out["rows"] == []


@pytest.mark.parametrize("top_k", [1, 2, 3])
def test_saved_tool_top_k_limits(seeded_sqlite, top_k):
    # top_k가 결과 개수 상한으로 작동
    out = json.loads(search_saved_requests.invoke({"query": MARKER, "top_k": top_k}))
    assert len(out["rows"]) <= top_k


@pytest.mark.parametrize("bad_top_k", [0, 51, 999])
def test_saved_tool_rejects_out_of_range_top_k(seeded_sqlite, bad_top_k):
    # tool 호출 시 스키마 범위 밖 top_k는 실행 전 거부
    with pytest.raises(Exception):
        search_saved_requests.invoke({"query": MARKER, "top_k": bad_top_k})


def test_saved_tool_empty_query_no_crash(seeded_sqlite):
    # 빈 쿼리에도 크래시 없이 list 반환
    out = json.loads(search_saved_requests.invoke({"query": "", "top_k": 5}))
    assert isinstance(out["rows"], list)


# ============================================================
# 참고자료 저장 — add_personal_reference_dict (헬퍼) / add_personal_reference (tool)
# ============================================================
@pytest.fixture()
def ref_cleanup():
    # 테스트가 만든 참고자료 id를 모아 끝나면 ChromaDB에서 삭제
    created: list[str] = []
    yield created
    if created:
        REFERENCE_STORE.collection.delete(ids=created)


def test_add_dict_returns_expected_keys(ref_cleanup):
    # 헬퍼 반환 dict가 필수 키를 갖는지
    saved = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} 키확인", content="본문")
    ref_cleanup.append(saved["reference_id"])
    assert set(saved) >= {"reference_id", "title", "content", "tags", "backend"}


def test_add_dict_tags_none_becomes_empty_list(ref_cleanup):
    # tags=None이면 빈 리스트로 저장
    saved = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} 태그없음", content="본문", tags=None)
    ref_cleanup.append(saved["reference_id"])
    assert saved["tags"] == []


def test_add_dict_tags_preserved(ref_cleanup):
    # tags를 주면 그대로 보존
    saved = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} 태그있음", content="본문", tags=["업무", "긴급"])
    ref_cleanup.append(saved["reference_id"])
    assert saved["tags"] == ["업무", "긴급"]


def test_add_dict_preserves_title_and_content(ref_cleanup):
    # title/content가 변형 없이 저장되는지
    saved = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} 제목", content="정확한 본문")
    ref_cleanup.append(saved["reference_id"])
    assert saved["title"] == f"{MARKER} 제목"
    assert saved["content"] == "정확한 본문"


def test_add_dict_backend_is_dict(ref_cleanup):
    # backend 정보가 dict이며 vector_store 키를 갖는지
    saved = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} 백엔드", content="본문")
    ref_cleanup.append(saved["reference_id"])
    assert isinstance(saved["backend"], dict)
    assert "vector_store" in saved["backend"]


def test_add_dict_unique_ids(ref_cleanup):
    # 저장할 때마다 reference_id가 유일한지
    a = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} A", content="본문 A")
    b = add_personal_reference_dict(REFERENCE_STORE, title=f"{MARKER} B", content="본문 B")
    ref_cleanup.extend([a["reference_id"], b["reference_id"]])
    assert a["reference_id"] != b["reference_id"]


def test_add_tool_returns_backend_and_reference(ref_cleanup):
    # tool 반환에 reference_backend + reference(필수 키) 포함
    out = json.loads(add_personal_reference.invoke({"title": f"{MARKER} 툴", "content": "본문", "tags": ["t"]}))
    ref_cleanup.append(out["reference"]["reference_id"])
    assert "reference_backend" in out
    assert set(out["reference"]) >= {"reference_id", "title", "content", "tags"}
    assert out["reference"]["tags"] == ["t"]


def test_add_tool_tags_none_becomes_empty_list(ref_cleanup):
    # tool에서 tags 미지정 시 빈 리스트로
    out = json.loads(add_personal_reference.invoke({"title": f"{MARKER} 툴무태그", "content": "본문"}))
    ref_cleanup.append(out["reference"]["reference_id"])
    assert out["reference"]["tags"] == []


# ============================================================
# 참고자료 검색 — search_personal_reference_hits (헬퍼) / search_personal_references (tool)
# ============================================================
@pytest.fixture(scope="module")
def seeded_references():
    # 의미검색 검증용 더미 참고자료 4건(커피/운동/보고서/집중)을 심고 끝나면 삭제
    docs = [
        ("커피", "나는 오후에 아메리카노를 즐겨 마신다.", ["음료"]),
        ("운동", "주말 아침에는 공원에서 조깅을 한다.", ["건강"]),
        ("보고서", "매달 말일에는 지출 보고서를 제출한다.", ["업무"]),
        ("집중", "회의는 오전 10시에서 12시 사이가 집중이 잘 된다.", ["업무", "회의"]),
    ]
    created = []
    for title, content, tags in docs:
        created.append(REFERENCE_STORE.add_personal_reference(title=f"{MARKER} {title}", content=content, tags=tags))
    yield {c["content"]: c for c in created}
    REFERENCE_STORE.collection.delete(ids=[c["reference_id"] for c in created])


def test_hits_helper_returns_list(seeded_references):
    # 헬퍼가 list를 반환하는지
    assert isinstance(search_personal_reference_hits(REFERENCE_STORE, query="커피", top_k=3), list)


def test_hits_helper_structure(seeded_references):
    # hit가 id/content/distance/metadata(title,tags) 구조인지
    hit = search_personal_reference_hits(REFERENCE_STORE, query="커피", top_k=1)[0]
    assert set(hit) >= {"id", "content", "distance", "metadata"}
    assert set(hit["metadata"]) >= {"title", "tags"}


@pytest.mark.parametrize("top_k", [1, 2, 3])
def test_hits_helper_top_k_respected(seeded_references, top_k):
    # 헬퍼가 top_k 개수 상한을 지키는지
    assert len(search_personal_reference_hits(REFERENCE_STORE, query="업무", top_k=top_k)) <= top_k


def test_hits_helper_distances_sorted(seeded_references):
    # 검색 결과가 distance 오름차순(가까운 순)인지
    hits = search_personal_reference_hits(REFERENCE_STORE, query="회의 집중 시간", top_k=5)
    distances = [h["distance"] for h in hits]
    assert distances == sorted(distances)


def test_ref_tool_returns_hits_key(seeded_references):
    # tool 반환 top-level에 hits 키
    out = json.loads(search_personal_references.invoke({"query": "커피", "top_k": 3}))
    assert "hits" in out and isinstance(out["hits"], list)


def test_ref_tool_semantic_beats_keyword(seeded_references):
    # 의미검색: '카페인' 질문에 커피 메모가 조깅 메모보다 가까움
    out = json.loads(search_personal_references.invoke({"query": "카페인 있는 음료 마시고 싶다", "top_k": 20}))
    by_content = {h["content"]: h["distance"] for h in out["hits"]}
    assert by_content["나는 오후에 아메리카노를 즐겨 마신다."] < by_content["주말 아침에는 공원에서 조깅을 한다."]


def test_ref_tool_finance_query(seeded_references):
    # 의미검색: '지출 정리' 질문에 보고서 메모가 조깅 메모보다 가까움
    out = json.loads(search_personal_references.invoke({"query": "돈 관리 지출 정리", "top_k": 20}))
    by_content = {h["content"]: h["distance"] for h in out["hits"]}
    assert by_content["매달 말일에는 지출 보고서를 제출한다."] < by_content["주말 아침에는 공원에서 조깅을 한다."]


@pytest.mark.parametrize("top_k", [1, 2, 3])
def test_ref_tool_enforces_min_candidates(seeded_references, top_k):
    # 작은 top_k로도 최소 후보 수를 확보하고, 반환 개수는 요청/확장 상한 안에 있는지
    out = json.loads(search_personal_references.invoke({"query": "업무", "top_k": top_k}))
    retrieval = out["retrieval"]
    assert retrieval["requested_top_k"] == max(top_k, REFERENCE_MIN_CANDIDATES)
    cap = REFERENCE_WIDENED_CANDIDATES if retrieval["widened"] else retrieval["requested_top_k"]
    assert len(out["hits"]) <= cap
    assert retrieval["returned"] == len(out["hits"])


def test_ref_tool_retrieval_report_structure(seeded_references):
    # 검색 품질 정보(retrieval)가 기대한 키를 갖는지
    out = json.loads(search_personal_references.invoke({"query": "커피", "top_k": 2}))
    assert set(out["retrieval"]) == {"requested_top_k", "returned", "best_distance", "sufficient", "widened"}


def test_ref_tool_marks_relevant_query_sufficient(seeded_references):
    # 관련 있는 질의는 근거 충분으로 판단하고 확장 재검색을 하지 않는지
    out = json.loads(search_personal_references.invoke({"query": "아메리카노 즐겨 마신다", "top_k": 2}))
    assert out["retrieval"]["best_distance"] <= REFERENCE_RELEVANCE_MAX_DISTANCE
    assert out["retrieval"]["sufficient"] is True
    assert out["retrieval"]["widened"] is False


def test_ref_tool_widens_when_evidence_weak(seeded_references):
    # 무관한 질의는 근거 부족으로 판단하고 후보를 넓혀 재검색하는지
    out = json.loads(search_personal_references.invoke({"query": "양자컴퓨터 논문 초록 요약", "top_k": 2}))
    assert out["retrieval"]["best_distance"] > REFERENCE_RELEVANCE_MAX_DISTANCE
    assert out["retrieval"]["sufficient"] is False
    assert out["retrieval"]["widened"] is True
    assert out["retrieval"]["requested_top_k"] < REFERENCE_WIDENED_CANDIDATES


def test_ref_tool_min_candidates_surface_lower_ranked_hit(seeded_references):
    # 회귀: 필요한 메모가 상위 2건 밖에 있어도 최소 후보 확보로 결과에 포함되는지
    # (시나리오 #5에서 top_k=2로 잘려 답변 근거가 빠졌던 상황)
    out = json.loads(search_personal_references.invoke({"query": "업무 관련 메모", "top_k": 2}))
    assert len(out["hits"]) >= REFERENCE_MIN_CANDIDATES


@pytest.mark.parametrize("bad_top_k", [0, 21, 999])
def test_ref_tool_rejects_out_of_range_top_k(seeded_references, bad_top_k):
    # 스키마 범위(1~20) 밖 top_k는 실행 전 거부
    with pytest.raises(Exception):
        search_personal_references.invoke({"query": "커피", "top_k": bad_top_k})


# ============================================================
# 대화 RAG — search_conversation_messages
# ============================================================
@pytest.fixture(scope="module")
def seeded_conversations():
    # 서로 다른 주제의 대화 2개를 SQLite에 만들고, 끝나면 삭제 후 재sync로 ChromaDB 청크까지 정리
    ca = SQLITE_STORE.create_conversation(title=f"{MARKER} 김치찌개")["conversation_id"]
    SQLITE_STORE.append_message(ca, "user", "김치찌개에는 돼지고기를 넣어야 국물이 깊고 진해진다.")
    SQLITE_STORE.append_message(ca, "assistant", "돼지고기 김치찌개 국물 팁을 기억해 두겠습니다.")
    cb = SQLITE_STORE.create_conversation(title=f"{MARKER} 파이썬")["conversation_id"]
    SQLITE_STORE.append_message(cb, "user", "파이썬 데코레이터는 함수를 감싸 기능을 덧붙이는 문법이다.")
    SQLITE_STORE.append_message(cb, "assistant", "데코레이터 개념을 기록해 두겠습니다.")
    yield {"a": ca, "b": cb}
    for conv in (ca, cb):
        SQLITE_STORE.delete_conversation(conv)
    CONVERSATION_RAG_STORE.sync_from_sqlite(SQLITE_STORE)


def test_conv_dict_keys(seeded_conversations):
    # worker 반환에 hits/rows/context/rag_backend/sync 키가 모두 있는지
    result = search_conversation_messages_dict(SQLITE_STORE, CONVERSATION_RAG_STORE, query="돼지고기 국물", top_k=5)
    assert set(result) >= {"hits", "rows", "context", "rag_backend", "sync"}


def test_conv_rows_equals_hits(seeded_conversations):
    # rows와 hits는 같은 데이터를 담는지
    result = search_conversation_messages_dict(SQLITE_STORE, CONVERSATION_RAG_STORE, query="데코레이터", top_k=5)
    assert result["rows"] == result["hits"]


def test_conv_conversation_id_filter(seeded_conversations):
    # conversation_id를 주면 그 대화의 청크만 반환하는지
    ca = seeded_conversations["a"]
    result = search_conversation_messages_dict(SQLITE_STORE, CONVERSATION_RAG_STORE, query="국물", top_k=5, conversation_id=ca)
    assert result["hits"]
    assert all(h["conversation_id"] == ca for h in result["hits"])


def test_conv_excludes_current_conversation(seeded_conversations):
    # 현재 대화(scope=ca)에서 tool을 부르면 ca 청크가 빠지는지 — 대조로 공허한 통과를 방지
    ca = seeded_conversations["a"]
    query = "돼지고기 국물 진하게"
    # (대조) 제외 없이는 ca가 결과에 포함되어야 이 테스트가 의미를 가진다
    base = search_conversation_messages_dict(SQLITE_STORE, CONVERSATION_RAG_STORE, query=query, top_k=10)
    assert any(h["conversation_id"] == ca for h in base["hits"]), "제외 전에는 ca가 잡혀야 대조가 성립"
    # 현재 대화=ca로 두면 ca 청크는 결과에서 빠져야 한다
    with conversation_session_scope(ca):
        out = json.loads(search_conversation_messages.invoke({"query": query, "top_k": 10}))
    assert all(h["conversation_id"] != ca for h in out["hits"])


def test_conv_tool_json_keys(seeded_conversations):
    # tool 반환 JSON에 hits/rows/context/sync 키가 있는지
    out = json.loads(search_conversation_messages.invoke({"query": "데코레이터", "top_k": 5}))
    assert set(out) >= {"hits", "rows", "context", "sync"}


def test_conv_message_rows_helper(seeded_conversations):
    # _rows 헬퍼가 hit dict들의 list(각 hit에 conversation_id/content 포함)를 반환하는지
    # (독립된 두 RAG 검색의 정확한 일치를 기대하면 임베딩 재호출로 flaky하므로 구조만 검증)
    rows = search_conversation_message_rows(SQLITE_STORE, query="김치찌개 돼지고기", top_k=5)
    assert isinstance(rows, list)
    assert all(isinstance(h, dict) and "conversation_id" in h and "content" in h for h in rows)


@pytest.mark.parametrize("top_k", [1, 2, 3])
def test_conv_top_k_respected(seeded_conversations, top_k):
    # top_k가 결과 개수 상한으로 작동하는지
    result = search_conversation_messages_dict(SQLITE_STORE, CONVERSATION_RAG_STORE, query="회의", top_k=top_k)
    assert len(result["hits"]) <= top_k


@pytest.mark.parametrize("bad_top_k", [0, 51, 999])
def test_conv_tool_rejects_out_of_range_top_k(seeded_conversations, bad_top_k):
    # 스키마 범위(1~50) 밖 top_k는 실행 전 거부
    with pytest.raises(Exception):
        search_conversation_messages.invoke({"query": "김치찌개", "top_k": bad_top_k})


# ============================================================
# 배선 조립 — week04_tools / prompt_parts / system_prompt (프록시 불필요)
# ============================================================
def test_week04_tools_includes_week4_rag_tools():
    # 4주차 RAG 도구 4개가 목록에 노출되는지
    names = {t.name for t in week04_tools()}
    assert {"add_personal_reference", "search_personal_references",
            "search_saved_requests", "search_conversation_messages"} <= names


def test_week04_tools_accumulates_week3_tools():
    # week03 도구(저장/조회/수정/삭제)가 그대로 누적되는지
    names = {t.name for t in week04_tools()}
    assert {"save_structured_request", "personal_list_saved_schedules",
            "personal_delete_saved_schedules", "extract_schedule_request"} <= names


def test_week04_tool_names_unique():
    # 같은 이름 도구가 중복 노출되지 않는지(week1 create를 week4용으로 교체하므로)
    names = [t.name for t in week04_tools()]
    assert len(names) == len(set(names))


def test_week04_prompt_parts_are_strings():
    # 프롬프트 조각이 모두 비어있지 않은 문자열인지
    parts = week04_prompt_parts()
    assert parts and all(isinstance(p, str) and p.strip() for p in parts)


def test_week04_prompt_accumulates_on_week3():
    # week03 프롬프트 위에 week4 조각이 누적되는지(개수가 더 많아야)
    from student_parts.week03_build_nanas_logbook import week03_prompt_parts
    assert len(week04_prompt_parts()) > len(week03_prompt_parts())


def test_week04_system_prompt_mentions_all_search_tools():
    # 라우팅 안내에 3개 검색 도구가 모두 언급되는지(LLM이 고를 근거)
    sp = week04_system_prompt()
    assert isinstance(sp, str) and sp.strip()
    for name in ("search_personal_references", "search_saved_requests", "search_conversation_messages"):
        assert name in sp
