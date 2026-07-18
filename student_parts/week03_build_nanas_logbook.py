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

SQLITE_MEMORY_PROMPT = (
    "Week 3부터 너는 Week 1의 임시 메모리 대신 SQLite 기록장(AppSQLiteStore)을 진짜 기억으로 쓴다. "
    "대화가 끝나거나 앱이 재시작되거나 새 대화가 시작돼도 이전에 저장한 일정/할 일/알림은 채팅 히스토리가 아니라 "
    "SQLite DB에 그대로 남아 있다. 그러므로 '내 일정 보여줘', '저번에 저장한 거 뭐였지' 같은 질문에는 "
    "채팅 맥락을 추측하지 말고 반드시 list_saved_requests/get_saved_request/personal_list_saved_schedules 같은 "
    "조회 tool을 호출해 DB에서 직접 확인한 뒤 답한다. "
    "이것은 반드시 지켜야 하는 규칙이다: 일정이 '있다/없다', '저장됐다/삭제됐다'처럼 SQLite 상태를 언급하는 "
    "모든 답변은, 바로 이번 턴에 조회 tool을 새로 호출한 결과에 근거해야 한다. 방금 전 턴에서 저장하거나 "
    "삭제했다는 이유로 '방금 지웠으니 이제 없겠지'처럼 tool을 다시 호출하지 않고 결과를 추측해 답하지 않는다."
)

WEEK03_TOOL_CALL_PROMPT = (
    "(1) 이것은 반드시 지켜야 하는 규칙이다: 사용자가 자연어 대화로 새 일정/할 일/알림을 만들어달라고 "
    "할 때(예: '잡아줘'/'등록해줘'/'만들어줘'/'예약해줘') SQLite 저장 경로는 extract_schedule_request "
    "다음 save_structured_request, 이 두 단계 딱 하나만 쓴다. extract_schedule_request로 구조화한 "
    "kind/title/date/start_time/end_time/members/priority/reason/original_text 필드를 요약하거나 "
    "다시 쓰지 말고 그대로 save_structured_request 인자로 전달한다. personal_create_schedule은 "
    "제목/날짜/시작시간이 이미 명시 인자로 주어진 Week 1 방식 직접 호출에만 쓰는 예외적 호환용 tool이라 "
    "자연어 대화에서는 고르지 않는다. 새 일정 하나에는 SQLite 저장이 정확히 한 번만 일어나야 하므로, "
    "이 두 경로를 같은 요청에 함께 호출하지 않는다(둘 다 호출하면 완전히 같은 일정이 두 건 저장되는 "
    "실제 데이터 결함이 생긴다). '혹시 몰라 다른 방법으로도 저장해두자'처럼 스스로 판단해 다른 경로를 "
    "추가로 호출하지 않는다. "
    "(2) 저장된 데이터를 다시 봐야 할 때는 list_saved_requests/get_saved_request로 원본 구조화 기록을, "
    "personal_list_saved_schedules로 일정 목록을 조회한다. "
    "(3) 이것은 반드시 지켜야 하는 규칙이다: 일정을 수정하거나 삭제하기 전에는 먼저 "
    "personal_list_saved_schedules로 후보를 확인해 정확한 schedule_id를 얻은 뒤 "
    "personal_update_saved_schedule 또는 personal_delete_saved_schedules를 호출한다. 이 후보 확인을 "
    "포함해 list_saved_requests/personal_list_saved_schedules를 호출할 때, 사용자가 날짜나 기간을 "
    "언급하지 않았다면 date_from/date_to를 반드시 비워(None) 전체 기간에서 찾는다 — 오늘 날짜로 "
    "임의로 좁혀서 조회하면 실제로 있는 일정도 후보에 안 잡혀 '없다'고 잘못 답하게 된다. 사용자가 "
    "날짜나 기간을 직접 언급했을 때만 그 값으로 date_from/date_to를 채운다. "
    "(4) schedule_ids나 날짜/제목/시간 같은 조건을 하나도 지정하지 않은 채로 일정을 전부 지우도록 요청하지 않는다. "
    "사용자가 명확히 전체 삭제를 원할 때만 delete_all=True로 호출한다. "
    "(5) personal_list_schedules와 personal_delete_schedule(둘 다 이름에 saved가 없다)은 Week 1의 임시 "
    "세션 메모리 전용 tool이며 SQLite 기록장을 전혀 건드리지 않는다. save_structured_request나 "
    "personal_create_schedule로 SQLite에 저장한 일정을 조회/수정/삭제할 때 이 두 tool을 절대 쓰지 않는다 — "
    "이름에 saved가 들어간 personal_list_saved_schedules/personal_update_saved_schedule/"
    "personal_delete_saved_schedules만 사용한다. 이름이 비슷해 헷갈리기 쉬우니 tool을 고르기 전에 "
    "반드시 이름에 saved가 있는지 다시 확인한다. "
    "(6) 이것은 반드시 지켜야 하는 규칙이다: personal_list_saved_schedules로 찾은 수정/삭제 후보가 2건 "
    "이상이면, 그 후보들이 제목/날짜/시간까지 서로 완전히 같아 보이더라도 서로 다른 schedule_id를 가진 "
    "별개의 저장 항목이므로 절대 같은 일정으로 취급하지 않는다. 사용자가 schedule_id를 지정했거나, "
    "'전부'/'모두'/'다' 같이 전체를 명확히 지목했거나, 후보가 정확히 1건으로 좁혀졌을 때만 "
    "personal_update_saved_schedule/personal_delete_saved_schedules를 호출한다. 이 세 조건 중 어느 것도 "
    "충족하지 않으면 절대 호출하지 말고, 그 대신 후보 각각의 날짜/시간/schedule_id를 사용자에게 보여주며 "
    "어떤 것을 원하는지 되묻는 답변만 한다. '중복이니 둘 다 반영하면 되겠지'처럼 스스로 판단해 여러 건에 "
    "한꺼번에 적용하지 않는다. "
    "(7) 이것은 반드시 지켜야 하는 규칙이다: extract_schedule_request는 '동아리 회식'/'스터디 모임'처럼 "
    "참석자를 명시하지 않아도 여러 사람이 모이는 자리라고 판단되면 kind를 personal_schedule이 아니라 "
    "group_schedule로 분류할 수 있다. personal_list_saved_schedules는 kind를 지정하지 않으면 기본값 "
    "personal_schedule만 조회하므로, group_schedule로 저장된 일정은 이 기본 조회에 절대 잡히지 않는다. "
    "그래서 사용자가 특정 제목을 언급하며 조회/수정/삭제를 요청했는데 기본값(personal_schedule) 조회 "
    "결과에 그 제목이 없으면, 곧바로 '저장되어 있지 않다'고 답하지 말고 "
    "personal_list_saved_schedules(kind='group_schedule')로 한 번 더 확인한다. 두 kind 모두에서 못 찾았을 "
    "때만 '저장되어 있지 않다'고 답한다."
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
        """예전 trace의 payload wrapper뿐 아니라 StructuredRequest 인스턴스/JSON 문자열까지 정규화합니다."""

        # 이미 완성된 StructuredRequest(그 서브클래스인 SaveStructuredRequestInput 포함) 인스턴스면
        # dict로 풀어서 그대로 통과시킨다 — 아래 언랩/필드 검증 로직과 같은 경로를 타게 한다.
        if isinstance(value, StructuredRequest):
            value = value.model_dump()
        elif isinstance(value, str):
            # JSON 문자열이면 먼저 파싱을 시도한다. 실패하거나 dict가 아니면 원본을 그대로 반환해
            # 이후 필드 검증이 명확한 타입 오류를 내게 한다(조용히 삼키지 않는다) — 자연어 문자열을
            # 구조화하는 일은 이 tool이 아니라 _save_input_from의 몫이다.
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
            value = parsed

        # dict가 아닌 입력(None/list/int 등)은 여기서 풀 수 없다. 억지로 dict로 바꾸려 하지 말고
        # 그대로 통과시켜, 이후 필드 스키마 검증이 명확한 타입 오류로 실패하게 한다.
        if not isinstance(value, dict):
            return value
        # 예전 trace/테스트는 {"payload": {...}} 또는 {"structured_request": {...}} 형태로
        # 실제 저장 필드를 한 겹 감싸서 넘기는 경우가 있다. 감싸는 키가 dict일 때만 한 겹 풀어 준다.
        for wrapper_key in ("payload", "structured_request"):
            inner = value.get(wrapper_key)
            if isinstance(inner, dict):
                return inner
        # 일반적인 agent 경로(LLM이 필드를 직접 넘김)는 이미 평평한 dict이므로 그대로 둔다.
        return value


def _save_input_from(value: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str) -> SaveStructuredRequestInput:
    """저장 입력을 SaveStructuredRequestInput 하나로 모읍니다."""

    # SaveStructuredRequestInput은 StructuredRequest의 서브클래스이므로, 더 구체적인 타입인
    # SaveStructuredRequestInput을 StructuredRequest보다 먼저 검사해야 한다.
    if isinstance(value, SaveStructuredRequestInput):
        return value
    if isinstance(value, StructuredRequest):
        return SaveStructuredRequestInput.model_validate(value.model_dump())
    if isinstance(value, dict):
        return SaveStructuredRequestInput.model_validate(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return SaveStructuredRequestInput.model_validate(parsed)
        # JSON 문자열이 아니거나 dict로 파싱되지 않으면(list/스칼라 포함) 자연어로 취급해
        # Week 2 bridge로 먼저 구조화한 뒤 저장 스키마로 검증한다.
        structured = extract_structured_request(value)
        return SaveStructuredRequestInput.model_validate(structured.model_dump())
    raise RuntimeError(f"예상치 못한 저장 입력 타입입니다: {type(value)!r}")


def save_structured_request_payload(
    request: SaveStructuredRequestInput | StructuredRequest | dict[str, Any] | str,
    *,
    store: AppSQLiteStore | None = None,
) -> dict[str, Any]:
    """검증된 structured request를 앱 DB에 저장합니다."""

    validated = _save_input_from(request)
    save_dict = validated.model_dump(exclude_none=True)
    # model_dump(exclude_none=True)만으로도 members는 절대 None이 될 수 없지만(list[str],
    # default_factory=list), save_structured_request tool과 동일한 방어 관례를 지키기 위해
    # 한 번 더 명시적으로 정규화한다.
    save_dict["members"] = save_dict.get("members") or []
    active_store = store or _store()
    result = active_store.save_structured_request(save_dict)
    return tool_result("save_structured_request", ok=True, **result)


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

    filters = {
        "schedule_ids": schedule_ids,
        "date": date,
        "title": title,
        "start_time": start_time,
        "time_unspecified": time_unspecified,
        "delete_all": delete_all,
    }
    # 안전 가드: schedule_ids가 빈 리스트([])인 경우도 "조건 없음"으로 취급한다.
    # delete_all=True가 명시적으로 들어온 경우에만 조건 없는 전체 삭제를 허용하고,
    # 그 외에는 조건이 하나도 없으면 실제 삭제를 수행하지 않고 즉시 거부한다.
    has_condition = bool(schedule_ids) or bool(date) or bool(title) or bool(start_time) or time_unspecified
    if not delete_all and not has_condition:
        return tool_result(
            "personal_delete_saved_schedules",
            ok=False,
            error=(
                "삭제 조건이 없습니다. schedule_ids나 날짜/제목/시간 필터를 지정하거나, "
                "정말 전체 삭제를 원하면 delete_all=True로 호출하세요."
            ),
            deleted_count=0,
            filters=filters,
            deleted=[],
        )

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

    return tool_result(
        "personal_delete_saved_schedules",
        ok=True,
        deleted_count=len(deleted),
        filters=filters,
        deleted=deleted,
    )


def structured_request_from_week01_schedule(schedule: dict[str, Any]) -> SaveStructuredRequestInput:
    """Week 1 임시 일정 dict를 Week 3 저장 입력으로 변환합니다."""

    def _normalize_time(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text or text in {"미정", "없음"}:
            return None
        return text

    title = schedule.get("title") or "제목 없음"
    date = schedule.get("date")
    start_time = _normalize_time(schedule.get("start_time"))
    # original_text는 원문 텍스트 보존 용도라, 내부 구현(함수명)을 언급하는 대신
    # 실제 일정 내용을 그대로 서술하는 자연스러운 문장으로 채운다.
    when = " ".join(part for part in (date, start_time) if part)
    original_text = f"{when}에 {title}" if when else title

    return SaveStructuredRequestInput(
        kind="personal_schedule",
        title=schedule.get("title"),
        date=date,
        start_time=start_time,
        end_time=_normalize_time(schedule.get("end_time")),
        members=list(schedule.get("attendees") or []),
        original_text=original_text,
        source_schedule_id=schedule.get("id"),
    )


@tool("personal_create_schedule")
def personal_create_schedule(
    title: str,
    date: str,
    start_time: str,
    end_time: str = "미정",
    attendees: list[str] | None = None,
) -> str:
    """Nana의 개인 일정을 생성하고 Week 3+ 앱 SQLite DB에도 저장합니다."""

    created_result = json.loads(
        week01_personal_create_schedule.invoke(
            {
                "title": title,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "attendees": attendees or [],
            }
        )
    )
    save_input = structured_request_from_week01_schedule(created_result["created_schedule"])
    # source_schedule_id에 Week 1 임시 일정 id를 실어 보내므로, 같은 일정을 다시 저장해도
    # AppSQLiteStore가 already_exists로 조기 반환해 구조화 요청/일정 row가 중복 생성되지 않는다.
    sqlite_save = _store().save_structured_request(save_input.model_dump(exclude_none=True))
    # 다른 모든 tool과 동일하게 이 파일의 공통 반환 헬퍼(tool_result)를 명시적으로 거쳐
    # ok/tool_name 규격을 일관되게 유지한다(Week1 tool의 반환 dict를 그대로 스프레드하지 않는다).
    return json_payload(
        tool_result(
            "personal_create_schedule",
            ok=True,
            created_schedule=created_result["created_schedule"],
            structured_request=save_input.model_dump(),
            sqlite_save=sqlite_save,
        )
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

    # args_schema(SaveStructuredRequestInput)가 이미 검증을 끝냈으므로 Pydantic class를 다시 만들지 않는다.
    # kind/original_text는 항상 유지하고, members는 None이 들어와도 schedules.attendees_json /
    # structured_requests.members_json의 NOT NULL 제약을 절대 깨지 않도록 빈 리스트로 정규화해 둔다.
    save_dict: dict[str, Any] = {
        "kind": kind,
        "original_text": original_text,
        "members": members if members is not None else [],
    }
    optional_fields = {
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "priority": priority,
        "reason": reason,
        "source_schedule_id": source_schedule_id,
    }
    # None 값은 저장 dict에서 제외한다 — ok/tool_name/base_date 같은 임시 wrapper뿐 아니라,
    # 확실하지 않은 필드를 raw_json에 null로 남기지 않기 위함이다.
    save_dict.update({key: value for key, value in optional_fields.items() if value is not None})

    result = _store().save_structured_request(save_dict)
    return json_payload(tool_result("save_structured_request", ok=True, **result))


@tool(args_schema=SavedRequestListInput)
def list_saved_requests(
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """SQLite에 저장된 구조화 요청 목록을 조회합니다."""

    rows = _store().list_saved_requests(kind=kind, date_from=date_from, date_to=date_to)
    return json_payload(tool_result("list_saved_requests", ok=True, rows=rows))


@tool(args_schema=SavedRequestGetInput)
def get_saved_request(request_id: str) -> str:
    """request_id로 구조화 요청 행 하나를 조회합니다."""

    row = _store().get_saved_request(request_id)
    return json_payload(tool_result("get_saved_request", ok=True, row=row))


@tool(args_schema=SavedScheduleListInput)
def personal_list_saved_schedules(
    limit: int = 50,
    kind: RequestKind | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """앱 DB에 저장된 일정 목록을 날짜/종류 필터로 반환합니다. Nana가 조회/수정/삭제 후보를 볼 때 사용합니다."""

    effective_kind = kind or "personal_schedule"
    schedules = _store().list_schedules(limit=limit, kind=effective_kind, date_from=date_from, date_to=date_to)
    filters = {"kind": effective_kind, "date_from": date_from, "date_to": date_to, "limit": limit}
    return json_payload(
        tool_result("personal_list_saved_schedules", ok=True, filters=filters, schedules=schedules)
    )


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

    # None은 store 계약상 "수정하지 않음"을 뜻하므로 그대로 전달한다(여기서 값을 걸러낼 필요 없음).
    result = _store().update_schedule(
        schedule_id,
        title=title,
        date=date,
        start_time=start_time,
        end_time=end_time,
        attendees=attendees,
    )
    if result is None:
        return json_payload(
            tool_result(
                "personal_update_saved_schedule",
                ok=False,
                error=f"schedule_id={schedule_id!r}에 해당하는 저장 일정을 찾을 수 없습니다.",
                schedule_id=schedule_id,
            )
        )
    return json_payload(
        tool_result(
            "personal_update_saved_schedule",
            ok=True,
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

    result = _delete_saved_schedules(
        store=_store(),
        schedule_ids=schedule_ids,
        date=date,
        title=title,
        start_time=start_time,
        time_unspecified=time_unspecified,
        delete_all=delete_all,
    )
    return json_payload(result)


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

    return [
        *week02_prompt_parts(),
        (
            "너는 이제 Week 2에서 구조화된 StructuredRequest를 화면에 보여주는 데서 그치지 않고, "
            "SQLite 기록장에 실제로 저장/조회/수정/삭제까지 담당하는 Week 3 agent다. Week 2 구조화 "
            "결과를 최종 답변으로 그대로 반환하지 말고, 아래 SQLite 저장/tool 호출 규칙을 따라 SQLite에 "
            "저장하는 단계까지 반드시 이어간다."
        ),
        SQLITE_MEMORY_PROMPT,
        WEEK03_TOOL_CALL_PROMPT,
        (
            f"현재 앱 기준일은 {current_app_date_iso()} 이다. 상대 날짜('내일' 등)를 실제 날짜로 계산할 "
            "때만 참고하고, 위 tool 호출 규칙대로 조회 범위를 오늘로 임의로 좁히는 용도로는 쓰지 않는다. "
            "이번 주차(Week 3)의 범위는 개인 일정/할 일/알림을 SQLite에 저장하고 다시 꺼내 쓰는 것까지이며, "
            "RAG 검색이나 여러 사람 일정 조율 같은 이후 주차 기능은 다루지 않는다."
        ),
    ]


def build_week03_agent() -> object:
    """Week 1-3 누적 tool 목록을 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK03_AGENT
    if _WEEK03_AGENT is None:
        _WEEK03_AGENT = create_agent(
            model=chat_model(),
            tools=week03_tools(),
            system_prompt=week03_system_prompt(),
        )
    return _WEEK03_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week03_agent()
