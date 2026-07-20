from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from fixed.config import CONFIG
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso
from fixed.app_store import AppSQLiteStore
from student_parts.week01_wake_up_nana import (
    join_system_prompt,
    personal_create_schedule as week01_personal_create_schedule,
    week01_tools,
)
from student_parts.week02_structure_natural_language_requests import (
    RequestKind,
    StructuredRequest,
    extract_schedule_request,
    extract_structured_request,
    week02_prompt_parts,
)


_WEEK03_AGENT: Any | None = None

# Week 3 영속 메모리 규칙: SQLite 기록장은 대화·재시작과 무관하게 유지된다.
SQLITE_MEMORY_PROMPT = (
    "SQLite에 저장된 일정/할 일/알림은 영구 기록장이다. "
    "대화가 바뀌거나 앱을 다시 시작해도 유지되며, 과거 대화에서 저장한 항목도 조회 tool로 찾을 수 있다. "
    "'저장', '기록', '기억해 둬' 같은 요청은 현재 대화용 임시 메모가 아니라 SQLite 기록장에 저장한다."
)

# Week 3 tool 호출 순서 규칙: 구조화 → 저장 → 조회/수정/삭제.
# 마지막 줄 "조건 없는 삭제 금지"는 _delete_saved_schedules의 guard와 이중으로 막는다
# (프롬프트 1차 + 코드 2차 심층 방어).
WEEK03_TOOL_CALL_PROMPT = (
    "자연어 저장 요청은 먼저 extract_schedule_request로 구조화한 뒤, "
    "그 결과의 structured_request 필드 값들을 save_structured_request 인자로 전달해 저장한다. "
    "저장된 일정 조회는 personal_list_saved_schedules, 저장 요청 이력 조회는 list_saved_requests, "
    "단건 확인은 get_saved_request를 쓴다. "
    "수정은 personal_update_saved_schedule, 삭제는 personal_delete_saved_schedules를 쓰며, "
    "schedule_id를 모르면 먼저 personal_list_saved_schedules로 후보를 조회해 확인한다. "
    "삭제는 schedule_ids나 명시적인 날짜/제목 조건 없이 호출하지 않는다. "
    # 이름이 비슷한 Week 1 인메모리 tool과의 혼동 방지 규칙 (두 저장소는 결과가 다르다).
    "주의: personal_list_schedules와 personal_delete_schedule은 현재 대화 전용 임시 메모리만 보는 "
    "Week 1 tool이라 SQLite 기록장과 결과가 다르다. "
    "저장된 일정의 조회와 수정/삭제 후보 확인에는 반드시 personal_list_saved_schedules를 쓴다."
)


# [3주차 수강생 구현 가이드]
#
# 목표
#   Week 2에서 만든 StructuredRequest를 Pydantic 입력 스키마로 검증한 뒤 SQLite에 저장하고,
#   저장된 요청/일정을 다시 조회/수정/삭제합니다. 여기서부터 Nana는 Week 1의 임시 메모리 대신
#   앱 DB에 남는 "기록장"을 갖게 됩니다.
#
# 과제 구성
#   - 메인과제: 구조화 결과를 SQLite에 저장하고 다시 조회하는 세로 슬라이스를 완성해
#     "저장 → 조회 → 새 대화에서도 유지"가 동작하는 최소 기록장을 만듭니다.
#   - 추가 과제: 저장된 일정을 수정/삭제하고 외부 공유 저장소와 동기화하며,
#     Week 1 호환 생성과 레거시 payload 정규화까지 다루는 확장 기능을 완성합니다.
#
# 핵심 흐름
#   1. LLM은 extract_schedule_request(query=사용자 요청)를 호출해 자연어를 Week 2 StructuredRequest로 바꿉니다.
#   2. LLM은 structured_request의 kind/title/date/start_time/end_time/members/priority/reason/original_text를
#      save_structured_request 인자로 그대로 전달합니다.
#   3. 각 tool에 붙은 @tool(args_schema=...)가 Pydantic class로 입력을 검증합니다.
#   4. Python tool 본문은 이미 검증된 인자를 AppSQLiteStore에 넘기고, 결과를 JSON 문자열로 반환합니다.
#
# 구현 위치와 사용할 코드
#   - StructuredRequest와 RequestKind는 week02_structure_natural_language_requests.py에서 재사용합니다.
#   - SaveStructuredRequestInput은 Week 2 StructuredRequest를 상속하고, Week 1 호환용 source_schedule_id만 추가합니다.
#   - SavedRequestListInput, SavedRequestGetInput, SavedScheduleListInput,
#     SavedScheduleUpdateInput, SavedScheduleDeleteInput은 조회/수정/삭제 tool 인자 스키마입니다.
#   - 실제 DB 접근은 fixed/app_store.py의 AppSQLiteStore를 사용하고, _store()가 CONFIG.app_db_path 기준
#     store 객체를 만들어 줍니다.
#   - save_structured_request_payload()와 delete_saved_schedules_dict()는 테스트/직접 호출/이전 trace 호환용 helper입니다.
#     agent가 일반적으로 호출하는 경로는 @tool(args_schema=...)가 붙은 tool 함수입니다.
#
# 메인과제 구현 대상
#   1. save_structured_request
#      - @tool(args_schema=SaveStructuredRequestInput)으로 Week 2 구조화 결과를 검증합니다.
#      - tool 본문에서는 Pydantic class를 다시 만들지 말고, 함수 인자로 들어온 값을 바로 저장 dict로 정리합니다.
#      - 자연어 문자열이나 ok/tool_name/base_date wrapper를 직접 저장하지 않습니다.
#
#   2. list_saved_requests / get_saved_request
#      - list는 kind/date_from/date_to 필터를 AppSQLiteStore.list_saved_requests(...)에 그대로 넘깁니다.
#      - get은 request_id 하나로 단건 조회합니다.
#      - 조회 결과가 없어도 예외를 던지지 말고 rows=[] 또는 row=None 형태를 유지합니다.
#
#   3. personal_list_saved_schedules
#      - 저장된 일정 목록을 반환해 "내 일정 보여줘" 같은 조회 질문과 이후 수정/삭제 후보 확인에 씁니다.
#      - 날짜가 명확한 조회는 date_from/date_to로 범위를 좁히고, 너무 많은 row가 들어가지 않게 limit을 사용합니다.
#
# 추가 과제 구현 대상
#   1. personal_update_saved_schedule
#      - AppSQLiteStore.update_schedule(...) 결과를 JSON 응답으로 완성하고, 공유 일정 복사본 동기화 결과(shared_sync)도 함께 반환합니다.
#      - None으로 들어온 필드는 "수정하지 않음"이라는 뜻입니다. ID를 못 찾으면 ok=False로 답합니다.
#
#   2. personal_delete_saved_schedules
#      - schedule_ids, date, title, start_time, time_unspecified, delete_all 조건을 받습니다.
#      - 조건 없이 삭제하지 않도록 _delete_saved_schedules(...)에서 안전 규칙을 확인합니다.
#      - deleted_count, filters, deleted를 유지해야 trace에서 무엇이 지워졌는지 확인할 수 있습니다.
#
#   3. personal_create_schedule (Week 1 호환)
#      - Week 1과 같은 이름을 유지하면서 임시 일정 생성 결과를 SQLite에도 저장하는 이중 기록 tool입니다.
#      - week01_personal_create_schedule 결과를 structured_request_from_week01_schedule()로 변환해 저장합니다.
#
#   4. 레거시 payload 정규화
#      - SaveStructuredRequestInput.unwrap_legacy_payload는 예전 trace/테스트의 payload/structured_request wrapper를 저장 스키마로 풉니다.
#      - _save_input_from / save_structured_request_payload는 tool 없이 dict/JSON/자연어를 직접 저장할 때 쓰는 helper입니다.
#
# 반환 규칙
#   모든 @tool은 JSON 문자열을 반환합니다.
#   ok와 tool_name은 기본으로 넣고, 조회는 rows/row, 삭제는 deleted_count/filters/deleted를 유지하세요.
#
# 참고 코드
#   week03_tools()는 Week 1-2 도구에 SQLite 도구를 누적해 공개합니다.
#   Week 1 호환 personal_create_schedule은 week01_personal_create_schedule 결과를
#   structured_request_from_week01_schedule()로 SaveStructuredRequestInput에 맞춘 뒤 SQLite에 저장합니다.
#   삭제 요청은 먼저 personal_list_saved_schedules로 후보를 확인한 뒤
#   personal_delete_saved_schedules에 schedule_ids 또는 명시 필터를 넘기는 흐름으로 처리합니다.
#
# 검증 방법
#   - 메인과제: ./run.sh --week3에서 "내일 10시 개인 코칭 저장해줘"처럼 입력합니다.
#     trace에서 extract_schedule_request 다음에 save_structured_request가 호출되는지 보고,
#     이어서 "내 일정 보여줘"가 personal_list_saved_schedules로 조회되며, 앱을 다시 시작하거나
#     새 대화를 열어도 저장된 일정이 그대로 보이면 메인과제가 동작하는 것입니다.
#   - 추가 과제: 저장된 일정을 personal_list_saved_schedules로 확인한 뒤 personal_update_saved_schedule로 시간을 바꾸고,
#     personal_delete_saved_schedules에 schedule_ids 또는 명시 필터를 넘겨 삭제한 일정이 목록에서 사라지는지 봅니다.
#
# 함수별 동작 설명 ([메인]/[추가]/[공통]은 각 함수가 속한 과제 티어입니다)
#   - [공통] _store()
#     현재 CONFIG.app_db_path를 기준으로 AppSQLiteStore를 생성합니다. SQL은 store.py가 담당하고,
#     이 파일의 tool들은 store 메서드를 호출하는 얇은 입구 역할만 합니다.
#
#   - [공통] _tool_name(item)
#     LangChain tool 객체와 일반 함수 객체 모두에서 이름을 안전하게 꺼냅니다. week03_tools()에서 Week 1 tool을 교체할 때 사용합니다.
#
#   - [공통] json_payload(payload)
#     tool 결과 dict를 한글이 깨지지 않는 JSON 문자열로 바꿉니다.
#
#   - [공통] tool_result(tool_name, ok, **payload)
#     여러 tool이 공통으로 쓰는 응답 껍데기를 만듭니다. 필수 구조는 아니지만 ok/tool_name 반복을 줄이는 작은 helper입니다.
#
#   - [메인] SaveStructuredRequestInput
#     Week 2 StructuredRequest를 상속한 저장 입력 스키마입니다. LangChain의 @tool(args_schema=...)가 이 class를 보고
#     save_structured_request 인자를 검증합니다.
#
#   - [추가] SaveStructuredRequestInput.unwrap_legacy_payload(value)
#     예전 trace나 테스트에서 들어올 수 있는 payload/structured_request wrapper를 저장 스키마 형태로 풀어 줍니다.
#     일반적인 agent 경로에서는 LLM이 필드를 직접 넘기므로 이 함수가 크게 개입하지 않습니다.
#
#   - [추가] _save_input_from(value)
#     테스트나 직접 호출 helper에서 dict, JSON 문자열, StructuredRequest를 SaveStructuredRequestInput 하나로 맞춥니다.
#     자연어 문자열이 들어오면 Week 2 extract_structured_request(...)로 먼저 구조화합니다.
#
#   - [추가] save_structured_request_payload(...)
#     tool wrapper 없이 직접 저장을 테스트해야 할 때 쓰는 helper입니다. 입력을 검증한 뒤 AppSQLiteStore.save_structured_request(...)에 넘깁니다.
#
#   - [메인/추가] SavedRequestListInput / SavedRequestGetInput / SavedScheduleListInput / SavedScheduleUpdateInput / SavedScheduleDeleteInput
#     조회, 단건 조회, 일정 목록, 일정 수정, 일정 삭제 tool의 입력 스키마입니다. Pydantic이 기본값과 범위를 검증합니다.
#     앞의 셋(list/get/schedule list)은 메인과제, 수정/삭제 스키마는 추가 과제에서 씁니다.
#
#   - [추가] _delete_saved_schedules(...)
#     삭제 조건이 비어 있는지 먼저 확인하고, delete_all인지 필터 삭제인지에 따라 store 삭제 메서드를 호출합니다.
#     실제 SQL 삭제는 AppSQLiteStore가 수행하고, 이 함수는 안전 규칙과 응답 모양을 정리합니다.
#
#   - [추가] structured_request_from_week01_schedule(schedule)
#     Week 1의 임시 schedule dict를 Week 3 저장 입력으로 변환합니다. personal_create_schedule 호환 wrapper에서 사용합니다.
#
#   - [추가] personal_create_schedule(...)
#     Week 1과 같은 이름을 유지하는 호환 tool입니다. 먼저 Week 1 임시 일정을 만들고, 같은 내용을 SQLite에도 저장합니다.
#
#   - [메인] save_structured_request(...)
#     Week 2 structured_request 필드를 직접 받아 SQLite에 저장하는 Week 3 핵심 tool입니다.
#     args_schema가 입력 검증을 끝낸 뒤 들어오므로, 본문은 저장 dict를 만들어 store에 넘기는 일만 합니다.
#
#   - [메인] list_saved_requests(...) / get_saved_request(...)
#     SQLite에 저장된 structured_requests 원본 기록을 목록 또는 단건으로 조회합니다.
#
#   - [메인] personal_list_saved_schedules(...)
#     저장된 일정 row를 조회합니다. 수정/삭제 전 후보 schedule_id를 확인하거나 사용자의 일정 조회 질문에 답할 때 사용합니다.
#
#   - [추가] delete_saved_schedules_dict(...)
#     테스트나 내부 코드에서 tool invoke 없이 삭제 로직을 호출할 수 있게 만든 dict 반환 helper입니다.
#
#   - [추가] personal_update_saved_schedule(...)
#     schedule_id로 저장 일정을 찾아 제목/날짜/시간/참석자를 수정합니다. 공유 일정 동기화 결과도 함께 반환합니다.
#
#   - [추가] personal_delete_saved_schedules(...)
#     schedule_ids나 날짜/제목/시간 필터로 저장 일정을 삭제하는 tool입니다. 조건 없는 삭제는 실패 응답으로 막습니다.
#
#   - [공통] week03_tools()
#     Week 1 tool 목록에 Week 2 구조화 tool과 Week 3 SQLite tool을 누적합니다. Week 1 personal_create_schedule은
#     SQLite 저장까지 수행하는 이 파일의 호환 tool로 교체합니다.
#
#   - [공통] week03_system_prompt() / week03_prompt_parts()
#     Week 3 agent가 "구조화 후 저장" 흐름을 따르도록 system prompt를 조립합니다.
#
#   - [공통] build_week03_agent() / build_week_agent()
#     Week 1~3 tool을 가진 agent를 한 번만 만들고 재사용합니다. build_week_agent()는 실행기가 호출하는 표준 entry point입니다.


def _store() -> AppSQLiteStore:
    return AppSQLiteStore(CONFIG.app_db_path)


def _tool_name(item: Any) -> str:
    return getattr(item, "name", getattr(item, "__name__", str(item)))


def json_payload(payload: dict[str, Any]) -> str:
    """도구 반환용 dict를 한글이 깨지지 않는 JSON 문자열로 변환합니다."""

    return json.dumps(payload, ensure_ascii=False)


def tool_result(tool_name: str, *, ok: bool = True, **payload: Any) -> dict[str, Any]:
    """Week 3 tool들이 공통으로 쓰는 JSON payload 껍데기를 만듭니다."""

    return {"ok": ok, "tool_name": tool_name, **payload}


class SaveStructuredRequestInput(StructuredRequest):
    """SQLite 저장 직전에 검증하는 Week 3 입력 스키마입니다."""

    kind: RequestKind = Field(default="unknown", description="분류된 요청 종류")
    source_schedule_id: str | None = Field(default=None, description="Week 1 임시 일정에서 넘어온 원본 일정 ID")

    @model_validator(mode="before")
    @classmethod
    def unwrap_legacy_payload(cls, value: Any) -> Any:
        """예전 trace의 payload wrapper만 짧게 풀고 실제 검증은 필드 스키마에 맡깁니다."""

        # ① StructuredRequest(또는 하위 클래스) 객체가 오면 dict로 풀어 필드 검증에 맡긴다.
        if isinstance(value, StructuredRequest):
            return value.model_dump()
        # ② 예전 trace 봉투를 벗긴다: {"structured_request": {...}} / {"payload": {...}} 형태면
        #    내용물만 꺼낸다. ok/tool_name 같은 통신용 키는 여기서 자연스럽게 버려진다.
        #    신뢰 기준: structured_request(현행)를 payload(구형)보다 우선하고,
        #    빈 봉투({})는 정보가 없으므로 건너뛰고 다음 후보로 넘어간다.
        if isinstance(value, dict):
            for wrapper_key in ("structured_request", "payload"):
                inner = value.get(wrapper_key)
                if isinstance(inner, dict) and inner:
                    return inner
        # ③ 그 외는 손대지 않고 그대로 필드 스키마 검증에 넘긴다.
        return value


def _save_input_from(value: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str) -> SaveStructuredRequestInput:
    """저장 입력을 SaveStructuredRequestInput 하나로 모읍니다."""

    # ① 이미 저장 입력이면 그대로 통과 (불필요한 재검증 없음).
    if isinstance(value, SaveStructuredRequestInput):
        return value

    # ② StructuredRequest 객체와 dict는 model_validate로 —
    #    검증 직전에 unwrap_legacy_payload(1층)가 자동으로 봉투를 벗긴다.
    if isinstance(value, (StructuredRequest, dict)):
        return SaveStructuredRequestInput.model_validate(value)

    # ③ 문자열: JSON이면 dict로 풀어 검증하고, JSON이 아니면 자연어로 보고
    #    Week 2 bridge(extract_structured_request, LLM 호출)로 구조화한다.
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            return SaveStructuredRequestInput.model_validate(decoded)
        return SaveStructuredRequestInput.model_validate(extract_structured_request(value).model_dump())

    # ④ 그 외 타입은 조용히 통과시키지 않는다 (fail fast).
    raise RuntimeError(f"저장 입력으로 변환할 수 없는 타입입니다: {type(value).__name__}")

# save_structured_request_payload   ← 3층: 정규화된 입력을 실제로 저장
#     └─ _save_input_from           ← 2층: 타입별 분류 (객체/dict/JSON/자연어)
#         └─ unwrap_legacy_payload  ← 1층: 봉투 벗기기 (Pydantic 검증 직전 자동 실행)

def save_structured_request_payload(
    request: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str,
    *,
    store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    """검증된 structured request를 앱 DB에 저장합니다."""

    # ① 어떤 형태(객체/dict/JSON/자연어)든 2층(_save_input_from)으로 정규화·검증한다.
    save_input = _save_input_from(request)

    # ② None 값을 제외한 dict로 바꿔 저장한다 — save_structured_request tool과 같은 규칙.
    payload = {key: value for key, value in save_input.model_dump().items() if value is not None}
    result = (store or _store()).save_structured_request(payload)

    # ③ tool과 같은 껍데기로 돌려주되 JSON 문자열이 아니라 dict를 반환한다 —
    #    이 helper는 LLM이 아니라 파이썬 코드가 직접 부르는 경로이기 때문이다.
    return tool_result("save_structured_request", **result)


class SavedRequestListInput(BaseModel):
    """저장 요청 목록 조회 입력입니다."""

    kind: RequestKind | None = None
    date_from: str | None = None
    date_to: str | None = None


class SavedRequestGetInput(BaseModel):
    """저장 요청 단건 조회 입력입니다."""

    request_id: str


class SavedScheduleListInput(BaseModel):
    """저장 일정 목록 조회 입력입니다."""

    limit: int = Field(default=50, ge=1, le=200)
    kind: RequestKind | None = None
    date_from: str | None = None
    date_to: str | None = None


class SavedScheduleUpdateInput(BaseModel):
    """저장 일정 수정 입력입니다."""

    schedule_id: str
    title: str | None = None
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    attendees: list[str] | None = None


class SavedScheduleDeleteInput(BaseModel):
    """저장 일정 삭제 입력입니다."""

    schedule_ids: list[str] | None = None
    date: str | None = None
    title: str | None = None
    start_time: str | None = None
    time_unspecified: bool = False
    delete_all: bool = False


def _delete_saved_schedules(
    *,
    store: AppSQLiteStore,
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
) -> dict[str, Any]:
    """삭제 guard와 DB 호출을 한 곳에 둡니다."""

    # 어떤 조건으로 삭제를 시도했는지 결과에 그대로 되돌려준다(자기서술적 응답).
    filters = {
        "schedule_ids": schedule_ids,
        "date": date,
        "title": title,
        "start_time": start_time,
        "time_unspecified": time_unspecified,
        "delete_all": delete_all,
    }

    # ① 삭제 조건이 하나도 없으면 거부한다(프롬프트 규칙이 뚫려도 여기서 오삭제를 막는 2차 guard).
    #    schedule_ids는 None과 빈 리스트 모두 "조건 없음"으로 취급한다.
    partial_conditions = bool(schedule_ids) or any([date, title, start_time, time_unspecified])
    if not (partial_conditions or delete_all):
        return tool_result(
            "personal_delete_saved_schedules",
            ok=False,
            status="rejected",
            error="삭제 조건이 없습니다. schedule_ids 또는 날짜/제목 같은 명시 조건이 필요합니다.",
            deleted_count=0,
            filters=filters,
            deleted=[],
        )

    # ② delete_all과 개별 조건이 "함께" 오면 의도가 섞인 충돌 입력이다.
    #    전체 삭제는 영향이 가장 크므로 조건을 임의로 무시하지 않고 거부해 의도를 재확인시킨다.
    if delete_all and partial_conditions:
        return tool_result(
            "personal_delete_saved_schedules",
            ok=False,
            status="rejected",
            error="delete_all과 개별 조건(schedule_ids/날짜/제목 등)이 함께 들어왔습니다. "
            "전체 삭제 의도면 delete_all만, 조건 삭제 의도면 개별 조건만 지정해 다시 호출하세요.",
            deleted_count=0,
            filters=filters,
            deleted=[],
        )

    # ③ 전체 삭제는 delete_all=True라는 "명시적 의사표시"가 있을 때만 별도 경로로 실행한다.
    #    나머지는 명시 필터에 맞는 일정만 골라 지운다. (공유 저장소 복사본 정리는 store 담당)
    if delete_all:
        deleted = store.delete_all_schedules()
    else:
        deleted = store.delete_schedules_by_filter(
            schedule_ids=schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
        )

    # ④ 무엇을 근거로(filters) 몇 건이(deleted_count) 정확히 뭐가(deleted) 지워졌는지 반환한다.
    #    ok는 "tool 실행 성공"만 말하고, 실제 결과는 status("deleted"/"no_match")가 말한다 —
    #    매칭 0건을 LLM이 "삭제 성공"으로 오해하지 않게 하기 위한 역할 분리.
    status = "deleted" if deleted else "no_match"
    payload: dict[str, Any] = {
        "status": status,
        "deleted_count": len(deleted),
        "filters": filters,
        "deleted": deleted,
    }
    if status == "no_match":
        # LLM이 그대로 읽고 사용자에게 안내할 수 있는 명시적 설명을 담는다.
        payload["message"] = "조건에 일치하는 일정이 없어 아무것도 삭제되지 않았습니다. schedule_id나 날짜/제목 조건을 다시 확인하세요."
    return tool_result("personal_delete_saved_schedules", **payload)


def structured_request_from_week01_schedule(schedule: dict[str, Any]) -> SaveStructuredRequestInput:
    """Week 1 임시 일정 dict를 Week 3 저장 입력으로 변환합니다."""

    # Week 1 dict와 Week 3 스키마의 필드명을 매핑한다:
    #   attendees → members, id → source_schedule_id(store의 중복 저장 방지 키).
    # end_time의 Week 1 기본값 "미정"은 자리표시 문자열이므로 None으로 정규화한다.
    end_time = schedule.get("end_time")
    return SaveStructuredRequestInput(
        kind="personal_schedule",
        title=schedule.get("title"),
        date=schedule.get("date"),
        start_time=schedule.get("start_time"),
        end_time=None if end_time == "미정" else end_time,
        members=schedule.get("attendees") or [],
        source_schedule_id=schedule.get("id"),
        reason="Week 1 임시 일정을 SQLite 기록장에 이중 기록",
    )


# @tool: 함수를 LangChain StructuredTool로 감싼다 — 이름/docstring/args_schema가
# LLM에게 전달되는 도구 명세가 되고, 인자는 함수 실행 "전"에 Pydantic으로 검증되며,
# agent 루프가 tool_call 이름을 찾아 .invoke()로 실행해 결과를 tool_result로 되돌린다.
@tool("personal_create_schedule")
def personal_create_schedule(
    title: str,
    date: str,
    start_time: str,
    end_time: str = "미정",
    attendees: list[str] | None = None,
) -> str:
    """Nana의 개인 일정을 생성하고 Week 3+ 앱 SQLite DB에도 저장합니다."""

    # ① Week 1 임시 일정 tool을 먼저 실행한다 — 현재 대화용 인메모리 기록(기존 동작 유지).
    created = json.loads(
        week01_personal_create_schedule.invoke(
            {
                "title": title,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "attendees": attendees,
            }
        )
    )

    # ② 생성된 임시 일정을 Week 3 저장 입력으로 번역해 SQLite에도 기록한다(이중 기록).
    #    변환기의 id → source_schedule_id 매핑 덕분에 같은 일정이 재호출돼도 중복 저장되지 않는다.
    save_input = structured_request_from_week01_schedule(created["created_schedule"])
    sqlite_save = save_structured_request_payload(save_input)

    # ③ Week 1 결과 위에 structured_request(번역본)와 sqlite_save(영구 저장 결과)를 얹어 반환한다 —
    #    trace 한 화면에서 임시 기록과 영구 기록을 모두 추적할 수 있다.
    return json_payload(
        {
            **created,
            "structured_request": save_input.model_dump(),
            "sqlite_save": sqlite_save,
        }
    )


@tool(args_schema=SaveStructuredRequestInput)
def save_structured_request(
    kind: RequestKind = "unknown",
    title: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    members: list[str] | None = None,
    priority: str | None = None,
    reason: str | None = None,
    original_text: str = "",
    source_schedule_id: str | None = None,
) -> str:
    """Week 2 structured_request 필드를 검증한 뒤 SQLite에 저장합니다."""

    # ① 함수 인자들을 저장용 dict 하나로 모은다.
    #    이 함수가 호출됐다는 것 자체가 args_schema(SaveStructuredRequestInput) 검증을
    #    이미 통과했다는 뜻이므로, 여기서 다시 Pydantic 검증을 만들 필요가 없다.
    payload = {
        "kind": kind,
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "members": members or [],
        "priority": priority,
        "reason": reason,
        "original_text": original_text,
        "source_schedule_id": source_schedule_id,
    }

    # ② None 값은 제외한다 — "모르는 값"을 DB의 원본 감사 로그(raw_json)에 남기지 않는다.
    #    store가 .get()으로 읽으므로 키가 없어도 안전하게 None으로 처리된다.
    payload = {key: value for key, value in payload.items() if value is not None}

    # ③ 실제 SQL(원본 저장 + kind별 테이블 분기 + 공유 저장소 동기화)은 fixed store가 담당한다.
    result = _store().save_structured_request(payload)

    # ④ ok/tool_name 껍데기에 store 결과(request_id/kind/saved_rows/shared_sync)를 합쳐
    #    JSON 문자열로 반환한다. shared_sync는 store가 외부 공유 저장소에 일정 사본을
    #    동기화한 결과로, 이 tool은 그대로 통과시킨다.
    return json_payload(tool_result("save_structured_request", **result))


@tool(args_schema=SavedRequestListInput)
def list_saved_requests(
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """SQLite에 저장된 구조화 요청 목록을 조회합니다."""

    # ① 필터를 그대로 store에 넘긴다. WHERE 절 조립과 ? 바인딩(SQL 인젝션 방지)은 store 담당.
    rows = _store().list_saved_requests(kind=kind, date_from=date_from, date_to=date_to)

    # ② 결과가 없어도 예외 없이 rows=[] 그대로 반환한다 — "없음"도 정상적인 조회 결과다.
    return json_payload(tool_result("list_saved_requests", rows=rows))


@tool(args_schema=SavedRequestGetInput)
def get_saved_request(request_id: str) -> str:
    """request_id로 구조화 요청 행 하나를 조회합니다."""

    # ① 영수증 번호(request_id)로 단건 조회. store가 못 찾으면 None을 돌려준다.
    row = _store().get_saved_request(request_id)

    # ② 못 찾아도 예외를 던지지 않고 row=None을 유지한다 —
    #    LLM이 "해당 요청을 찾지 못했다"고 자연스럽게 답할 수 있게 한다.
    return json_payload(tool_result("get_saved_request", row=row))


@tool(args_schema=SavedScheduleListInput)
def personal_list_saved_schedules(
    limit: int = 50,
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """앱 DB에 저장된 일정 목록을 날짜/종류 필터로 반환합니다. Nana가 조회/수정/삭제 후보를 볼 때 사용합니다."""

    # ① kind를 지정하지 않으면 개인 일정을 기본으로 본다 — 이 tool의 주 용도가 "내 일정 보여줘"라서다.
    effective_kind = kind or "personal_schedule"

    # ② 실제 적용된 필터를 결과에 같이 담는다(echo). LLM과 trace를 읽는 사람이
    #    "무슨 기준으로 조회된 목록인지"를 결과만 보고 알 수 있다.
    filters = {"kind": effective_kind, "date_from": date_from, "date_to": date_to, "limit": limit}

    # ③ 서랍(schedules 테이블) 조회. 날짜/시간순 정렬과 request_kind 결합은 store 담당.
    schedules = _store().list_schedules(limit=limit, kind=effective_kind, date_from=date_from, date_to=date_to)

    return json_payload(tool_result("personal_list_saved_schedules", filters=filters, schedules=schedules))


def delete_saved_schedules_dict(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
    app_store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    """tool invoke 없이 저장 일정 삭제 로직을 직접 호출합니다."""

    # 주입받은 store가 있으면 그것을, 없으면 기본 store를 쓴다(테스트 때 임시 DB 주입 가능).
    # guard와 실제 삭제는 전부 핵심부(_delete_saved_schedules)가 담당하고,
    # 이 함수는 파이썬 코드용 입구로서 인자를 그대로 전달만 한다.
    return _delete_saved_schedules(
        store=app_store or _store(),
        schedule_ids=schedule_ids,
        date=date,
        title=title,
        start_time=start_time,
        time_unspecified=time_unspecified,
        delete_all=delete_all,
    )


@tool(args_schema=SavedScheduleUpdateInput)
def personal_update_saved_schedule(
    schedule_id: str,
    title: str | None = None,
    date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    attendees: list[str] | None = None,
) -> str:
    """앱 DB에 저장된 내 일정 원본을 수정하고 공유 일정 복사본을 같은 값으로 갱신합니다."""

    # ① 수정 필드를 store에 그대로 전달한다. None은 "이 필드는 수정하지 않음"이라는 뜻이고,
    #    None이 아닌 필드만 UPDATE에 반영하는 부분 수정(partial update)은 store가 담당한다.
    result = _store().update_schedule(
        schedule_id=schedule_id,
        title=title,
        date=date,
        start_time=start_time,
        end_time=end_time,
        attendees=attendees,
    )

    # ② ID를 못 찾으면(store가 None 반환) 예외 대신 ok=False로 답한다 —
    #    LLM이 "해당 일정을 찾지 못했다"고 안내하고 목록 조회를 권할 수 있게.
    if result is None:
        return json_payload(
            tool_result(
                "personal_update_saved_schedule",
                ok=False,
                error=f"schedule_id '{schedule_id}'에 해당하는 일정을 찾지 못했습니다.",
                updated_schedule=None,
                shared_sync=None,
            )
        )

    # ③ 수정된 일정 원본과 공유 저장소 동기화 결과를 함께 반환한다.
    return json_payload(
        tool_result(
            "personal_update_saved_schedule",
            updated_schedule=result["schedule"],
            shared_sync=result["shared_sync"],
        )
    )


@tool(args_schema=SavedScheduleDeleteInput)
def personal_delete_saved_schedules(
    schedule_ids: list[str] | None = None,
    date: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
    time_unspecified: bool = False,
    delete_all: bool = False,
) -> str:
    """Nana가 고른 일정 ID나 날짜/제목/시간 필터로 저장 일정을 삭제합니다."""

    # LLM용 입구: args_schema 검증을 통과한 조건을 핵심부에 그대로 전달하고,
    # 결과 dict를 JSON 문자열로 포장만 한다(LLM에게 가는 반환은 항상 문자열).
    # guard(조건 없는 삭제 거부)는 핵심부가 담당하므로 여기서 반복하지 않는다.
    return json_payload(
        _delete_saved_schedules(
            store=_store(),
            schedule_ids=schedule_ids,
            date=date,
            title=title,
            start_time=start_time,
            time_unspecified=time_unspecified,
            delete_all=delete_all,
        )
    )

# [LLM]  personal_delete_saved_schedules (@tool, JSON 반환) ─┐
#                                                            ├→ _delete_saved_schedules
# [코드]  delete_saved_schedules_dict (dict 반환) ────────────┘   (guard + store 호출, 단일 진실)

def week03_tools() -> list[Any]:
    """Week 1 도구, Week 2 구조화 helper, SQLite 저장/조회/삭제 도구를 조립합니다."""

    base_tools = [
        personal_create_schedule if _tool_name(item) == "personal_create_schedule" else item for item in week01_tools()
    ]
    return [
        *base_tools,
        extract_schedule_request,
        save_structured_request,
        list_saved_requests,
        get_saved_request,
        personal_list_saved_schedules,
        personal_update_saved_schedule,
        personal_delete_saved_schedules,
    ]


def week03_system_prompt() -> str:
    """3주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week03_prompt_parts())


def week03_prompt_parts() -> list[str]:
    """1~3주차 system prompt 조각을 누적합니다."""

    # 기준일은 1·2주차와 같은 방식으로 동적 주입한다.
    today = current_app_date_iso()

    return [
        *week02_prompt_parts(),
        # Week 2 구조화 결과 → Week 3 저장 입력 연결 규칙.
        # 래퍼(ok/tool_name/base_date)째 저장하면 통신용 키가 DB 원본 로그(raw_json)에
        # 섞이므로, 내용물(structured_request 필드)만 전달하게 지시한다.
        (
            "Week 2의 구조화 결과는 이제 최종 답변이 아니라 저장 입력이다. "
            "extract_schedule_request 결과 JSON의 structured_request 안에 있는 필드 값들만 "
            "save_structured_request에 그대로 전달하고, ok/tool_name/base_date 같은 "
            "래퍼 키나 원문 문장 전체를 저장 인자로 넘기지 않는다."
        ),
        SQLITE_MEMORY_PROMPT,
        WEEK03_TOOL_CALL_PROMPT,
        # 현재 날짜 + Week 3 tool 선택 기준 + 이번 주차 범위.
        (
            f"오늘은 {today}이다. "
            "일정/할 일/알림의 저장·조회·수정·삭제는 SQLite 기록장 tool을 우선 사용한다. "
            "tool 결과를 받은 뒤에는 저장/조회 내용을 짧고 정확하게 한국어로 답한다. "
            "이번 주에는 RAG 검색이나 외부 멤버 일정 조율을 하지 않는다."
        ),
    ]


def build_week03_agent() -> object:
    """Week 1-3 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK03_AGENT
    if _WEEK03_AGENT is None:
        # Week 2와 달리 response_format이 없다 — 이번 주 최종 답변은 구조화 객체가 아니라
        # "저장했어요/이런 일정이 있어요" 같은 자연어 문장이고, 구조화는 tool 계층이 담당한다.
        _WEEK03_AGENT = create_agent(
            model=chat_model(),
            tools=week03_tools(),
            system_prompt=week03_system_prompt(),
        )
    return _WEEK03_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week03_agent()
