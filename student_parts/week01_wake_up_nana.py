from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool

from fixed.config import CONFIG
from fixed.langchain_trace import (
    extract_agent_events,
    extract_final_text,
    extract_langchain_trace,
    message_content_to_text,
    message_tool_call_names,
    normalize_messages_value,
    stream_chunk_messages,
)
from fixed.llm import chat_model
from fixed.runtime_clock import current_app_date_iso, next_weekday_iso
from fixed.session_scope import DEFAULT_SESSION_SCOPE, current_session_scope


PERSONAL_SCHEDULES: list[dict[str, Any]] = []
_WEEK01_AGENT: Any | None = None

# TODO: 현재 채팅 기억 관련 공통 system prompt를 자유롭게 추가하세요.
CHAT_MEMORY_PROMPT = """너는 사용자와의 대화를 돕는 assistant다. 아래는 네가 가진 '기억'의
범위와 한계에 대한 규칙이다.

# 기억 범위
- 너는 지금 이 대화(현재 채팅 세션) 안에서 오간 내용만 알고 있다.
- 이 대화에서 만들어지거나 언급된 정보(예: 일정, 결정 사항, 사용자가 말해준 사실 등)는 DB나
  영구 저장소에 저장되지 않고, 이 대화가 끝나면 사라지는 임시 상태다.
- 다른 대화창, 이전에 이미 종료된 대화, 혹은 아직 시작하지 않은 미래의 대화는 서로 공유되지
  않는다. 즉 다른 대화에서 있었던 일은 너에게 보이지 않고, 지금 이 대화의 내용도 다른 대화에는
  전달되지 않는다.

# 행동 규칙
- 사용자가 "아까 말했잖아", "저번에 얘기했던 거 있잖아", "전에 만들어둔 거 있지?"처럼 이 대화
  범위 밖의 내용을 네가 이미 알고 있다고 가정하고 말하면, 그 내용을 안다고 넘겨짚지 않는다.
  대신 너는 현재 대화 범위의 정보만 알 수 있다는 점을 솔직하고 담백하게 알리고, 필요한 내용을
  다시 말해달라고 요청한다.
- 이 대화 안에서 이미 확인된 정보(사용자가 이번 대화에서 직접 말했거나, 이번 대화에서 도구
  호출로 확인된 결과)는 정확히 활용한다. 모르는 내용을 아는 것처럼 추측해서 답하지 않는다.
- 기억의 한계를 사용자에게 알릴 때도 불필요하게 장황한 설명이나 사과를 반복하지 않고, 다음
  행동(무엇을 다시 말해주면 되는지)으로 자연스럽게 이어간다."""

def join_system_prompt(parts: list[str]) -> str:
    """주차별 prompt 조각을 읽기 쉬운 누적 system prompt로 합칩니다."""

    header = (
        "아래 system prompt는 주차별로 누적된 안내다. "
        "같은 주제의 지시가 여러 번 나오면 더 높은 주차 또는 더 뒤에 있는 지시를 우선한다."
    )
    return "\n\n".join([header, *[part.strip() for part in parts if part.strip()]])


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _new_personal_id() -> str:
    return f"personal_{uuid.uuid4().hex[:10]}"


def _schedule_scope(schedule: dict[str, Any]) -> str:
    """기존 직접 tool 호출 row는 기본 scope로 취급합니다."""

    return str(schedule.get("session_id") or DEFAULT_SESSION_SCOPE)


def _current_session_schedules() -> list[dict[str, Any]]:
    session_id = current_session_scope()
    return [schedule for schedule in PERSONAL_SCHEDULES if _schedule_scope(schedule) == session_id]


@tool(
    "personal_create_schedule",
    description=(
        "새 개인 일정을 생성한다. "
        "date는 'YYYY-MM-DD' 형식, start_time/end_time은 'HH:MM' 형식이다. "
        "예: title='팀 회의', date='2026-07-01', start_time='14:00', end_time='15:00'"
    ),
)
def personal_create_schedule(
    title: str,
    date: str,
    start_time: str,
    end_time: str = "미정",
    attendees: list[str] | None = None,
) -> str:
    created_schedule = {
        "id": _new_personal_id(),
        "created_at": _now_iso(),
        "title": title,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "attendees": attendees if attendees is not None else [],
        "session_id": current_session_scope(),
    }
    PERSONAL_SCHEDULES.append(created_schedule)
    return _json(
        {
            "ok": True,
            "tool_name": "personal_create_schedule",
            "created_schedule": created_schedule,
        }
    )


@tool(
    "personal_list_schedules",
    description=(
        "현재 대화 범위의 개인 일정을 조회한다. "
        "date_from, date_to는 모두 선택값이며 'YYYY-MM-DD' 형식이다. "
        "date_from을 주면 그 날짜 이상, date_to를 주면 그 날짜 이하인 일정만 반환한다. "
        "예: date_from='2026-07-01', date_to='2026-07-07'"
    ),
)
def personal_list_schedules(date_from: str | None = None, date_to: str | None = None) -> str:
    schedules = _current_session_schedules()
    if date_from is not None:
        schedules = [schedule for schedule in schedules if schedule["date"] >= date_from]
    if date_to is not None:
        schedules = [schedule for schedule in schedules if schedule["date"] <= date_to]
    return _json(
        {
            "ok": True,
            "tool_name": "personal_list_schedules",
            "schedules": schedules,
        }
    )


@tool(
    "personal_delete_schedule",
    description=(
        "schedule_id에 해당하는 개인 일정을 삭제한다. "
        "schedule_id는 _new_personal_id()가 만든 'personal_' 접두어가 붙은 문자열 형식이다. "
        "현재 대화 범위(session)가 다르면 같은 schedule_id라도 삭제되지 않는다."
    ),
)
def personal_delete_schedule(schedule_id: str) -> str:
    session_id = current_session_scope()
    before_count = len(PERSONAL_SCHEDULES)
    remaining = [
        schedule
        for schedule in PERSONAL_SCHEDULES
        if not (schedule["id"] == schedule_id and _schedule_scope(schedule) == session_id)
    ]
    PERSONAL_SCHEDULES[:] = remaining
    deleted = before_count - len(PERSONAL_SCHEDULES)
    return _json(
        {
            "ok": True,
            "tool_name": "personal_delete_schedule",
            "deleted": deleted,
        }
    )


def week01_tools() -> list[Any]:
    """1주차에서 직접 구현한 개인 일정 CRUD 도구 목록입니다."""

    return [personal_create_schedule, personal_list_schedules, personal_delete_schedule]


def week01_system_prompt() -> str:
    """1주차 단일 Nana agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week01_prompt_parts())


def week01_prompt_parts() -> list[str]:
    """1주차부터 누적되는 system prompt 조각입니다. Nana의 역할·날짜 계산처럼 이후 주차에서도
    계속 유효한 일반 지시만 담는다. personal_create_schedule 등 Week 1 tool을 구체적으로 호출하는
    규칙은 week01_tool_call_prompt()에 따로 둔다 — 이후 주차가 같은 역할의 다른 tool(예: SQLite
    저장 버전)을 추가하면 tool 이름을 못박은 규칙이 그대로 누적될 경우 서로 충돌하기 때문이다."""

    return [
        f"""너는 사용자의 개인 일정 관리를 돕는 비서 'Nana'다.
오늘 날짜는 {current_app_date_iso()}이다. 사용자가 "내일", "다음 주 화요일"처럼 상대적인
날짜로 말하면 이 날짜를 기준으로 계산해서 'YYYY-MM-DD' 형식으로 tool 인자를 채운다.
날짜나 시간이 불명확하면 임의로 추측해서 채우지 말고 먼저 사용자에게 되묻는다.""",
        """tool 호출 결과 JSON의 ok/tool_name은 호출이 끝났다는 사실과 어떤 tool의 결과인지 알려주는
메타데이터일 뿐이다. 답변의 근거로 삼을 실제 내용은 그 두 필드 다음에 오는 값(rows/hits/schedules/
created_schedule 등 tool마다 다른 이름의 필드)에서 찾는다. ok/tool_name 자체를 답변 내용으로
쓰거나 근거로 인용하지 않는다.""",
    ]


def week01_tool_call_prompt() -> str:
    """personal_create_schedule / personal_list_schedules / personal_delete_schedule(Week 1
    임시 메모리 tool) 선택 규칙이다. week01_prompt_parts()와 달리 week02_prompt_parts() 같은
    "다음 주차로 이어지는" 함수에는 자동으로 섞이지 않는다. 이 tool들을 그대로(저장 방식 변경
    없이) 쓰는 주차의 system_prompt()에서만 명시적으로 이어붙인다(지금은 Week 1, Week 2)."""

    return """일정 관련 요청에는 아래 규칙에 따라 personal_create_schedule / personal_list_schedules /
personal_delete_schedule 중 알맞은 tool을 선택해서 사용한다.
- 사용자가 "만들어줘", "추가해줘", "잡아줘"처럼 새 일정 등록을 요청하면 personal_create_schedule을 호출한다.
- 사용자가 "보여줘", "확인해줘", "뭐 있어?"처럼 일정 확인을 요청하면 personal_list_schedules를 호출한다.
  특정 기간이 언급되면 date_from/date_to를 채우고, 언급이 없으면 기간 제한 없이 조회한다.
- 사용자가 "지워줘", "취소해줘", "삭제해줘"처럼 일정 삭제를 요청하면 personal_delete_schedule을 호출한다.
  삭제할 일정의 schedule_id를 모르면 먼저 personal_list_schedules로 조회해서 일치하는 일정을 찾고,
  그 id로 삭제를 요청한다. 후보가 여러 개면 사용자에게 어떤 일정인지 확인한다.
- tool 호출 결과(JSON)는 그대로 노출하지 않고, 자연스러운 한국어 문장으로 요약해서 사용자에게 전달한다."""


def build_week01_agent() -> object:
    """Week 1 tool 목록만 노출하는 단일 LangChain agent를 만듭니다."""

    if not CONFIG.has_openai_key:
        raise RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")
    global _WEEK01_AGENT
    if _WEEK01_AGENT is None:
        _WEEK01_AGENT = create_agent(
            model=chat_model(),
            tools=week01_tools(),
            system_prompt=week01_system_prompt(),
        )
    return _WEEK01_AGENT


def build_week_agent() -> object:
    """active-week registry가 호출하는 표준 Week agent builder입니다."""

    return build_week01_agent()


def list_personal_schedule_dicts(date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """개인 일정 dict 목록이 필요한 내부 코드에서 사용하는 비-도구 헬퍼입니다."""

    schedules = json.loads(personal_list_schedules.invoke({"date_from": date_from, "date_to": date_to}))
    return schedules["schedules"]


def ensure_demo_personal_schedule() -> None:
    if PERSONAL_SCHEDULES:
        return
    personal_create_schedule.invoke(
        {
            "title": "개인 집중 작업",
            "date": next_weekday_iso(2),
            "start_time": "09:00",
            "end_time": "10:00",
            "attendees": [],
        }
    )

# [수강생 구현 가이드]
#
# 목표
#   Nana가 "내 일정 만들어줘/보여줘/지워줘" 같은 개인 일정 요청을 받았을 때
#   LLM이 직접 고를 수 있는 LangChain tool 3개를 완성합니다. Week 1의 일정은
#   앱 DB에 저장하지 않는 현재 대화 전용 임시 메모리입니다.
#
# 구현 위치와 사용할 코드
#   - 이 파일(student_parts/week01_wake_up_nana.py) 안의 @tool 함수 3개를 직접 구현합니다.
#   - 임시 저장소는 이 파일 상단의 PERSONAL_SCHEDULES 리스트입니다.
#   - JSON 문자열 반환은 이 파일의 _json(payload) helper를 사용합니다.
#   - 새 일정 ID는 _new_personal_id(), 생성 시각은 _now_iso()를 사용합니다.
#   - 현재 채팅 범위 분리는 fixed/session_scope.py의 current_session_scope() 값을
#     schedule dict의 session_id에 넣고, 조회/삭제 때 같은 session_id만 대상으로 삼아 처리합니다.
#   - week01_tools()가 세 tool을 LangChain agent에 공개하고, build_week01_agent()가 이 목록을 사용합니다.
#
# 구현 대상
#   1. personal_create_schedule
#      - title/date/start_time/end_time/attendees 인자로 schedule dict를 만듭니다.
#      - id는 "personal_" 접두어가 붙은 임시 ID, created_at은 현재 시각으로 채웁니다.
#      - attendees가 None이면 빈 list로 바꾸고, session_id=current_session_scope()를 함께 넣어
#        PERSONAL_SCHEDULES에 append합니다.
#      - 반환 JSON에는 ok, tool_name, created_schedule을 넣습니다.
#      - Week 1 반환에는 structured_request나 sqlite_save를 넣지 않습니다.
#
#   2. personal_list_schedules
#      - PERSONAL_SCHEDULES를 직접 수정하지 않고 현재 대화 범위의 일정만 조회합니다.
#      - date_from이 있으면 그 날짜 이상, date_to가 있으면 그 날짜 이하만 남깁니다.
#      - 날짜 비교는 YYYY-MM-DD 문자열 기준으로 충분합니다.
#      - 반환 JSON에는 ok, tool_name, schedules를 넣습니다.
#
#   3. personal_delete_schedule
#      - schedule_id가 일치하면서 현재 대화 범위에 속한 일정만 삭제합니다.
#      - 리스트 객체 자체는 유지해야 하므로 PERSONAL_SCHEDULES[:]에 새 목록을 대입합니다.
#      - 삭제 전후 길이 비교로 deleted 값을 만들고 JSON으로 반환합니다.
#      - 다른 대화 범위의 같은 ID는 삭제하면 안 됩니다.
#
# 중요한 반환 규칙
#   LangChain tool은 문자열 반환이 가장 안정적입니다. dict를 만든 뒤 _json(...)으로 감싸세요.
#   Week 1 도구는 현재 대화 안에서만 쓰는 임시 일정 dict만 반환하며 SQLite/App store를 호출하지 않습니다.
#
# 참고 코드
#   week01_system_prompt, week01_tools(), build_week_agent(), trace helper는 구현 대상이 아닙니다.
#   이 함수들은 "LLM이 어떤 tool을 볼 수 있는지"와 "trace를 어떻게 보여주는지"를 이해할 때 읽습니다.
#
# 검증 방법
#   앱을 ./run.sh --week1로 실행하고 채팅에 하네스 프롬프트를 넣습니다.
#   상세 trace에서 LLM이 personal_create_schedule/list/delete 중 어떤 tool을 골랐는지 확인합니다.
#   tool 결과 JSON에 created_schedule, schedules, deleted가 있는지도 확인합니다.
#
# 함수별 동작 설명
#   - join_system_prompt(parts)
#     여러 주차에서 만든 system prompt 조각을 하나의 문자열로 합칩니다. 뒤 주차 지시가 앞 주차 지시보다
#     우선된다는 공통 헤더를 붙여서, Week 2 이후 파일들이 같은 방식으로 prompt를 누적할 수 있게 합니다.
#
#   - _json(payload)
#     LangChain tool이 반환할 dict를 JSON 문자열로 바꿉니다. ensure_ascii=False를 사용해 한글 답변과
#     일정 제목이 escape되지 않게 합니다.
#
#   - _now_iso()
#     일정 생성 시각을 timezone이 포함된 ISO 문자열로 만듭니다. 학생 코드에서는 created_at 기록용으로만 사용합니다.
#
#   - _new_personal_id()
#     Week 1 임시 일정에 붙일 짧은 고유 ID를 만듭니다. DB ID가 아니라 현재 Python 프로세스 안에서 쓰는 임시 ID입니다.
#
#   - _schedule_scope(schedule)
#     일정 dict가 어느 대화 범위에 속하는지 읽습니다. 예전 테스트처럼 session_id가 없는 row는 기본 scope로 취급합니다.
#
#   - _current_session_schedules()
#     PERSONAL_SCHEDULES 전체 중 현재 conversation/session 범위에 속한 일정만 골라 반환합니다.
#
#   - personal_create_schedule(...)
#     LLM이 일정 생성이 필요하다고 판단했을 때 호출하는 tool입니다. 입력 인자로 schedule dict를 만들고
#     PERSONAL_SCHEDULES에 append한 뒤, 생성된 schedule을 JSON 문자열로 반환합니다.
#
#   - personal_list_schedules(date_from, date_to)
#     현재 대화 범위의 임시 일정만 읽고 날짜 범위 필터를 적용합니다. 리스트를 수정하지 않고 조회 결과만 반환합니다.
#
#   - personal_delete_schedule(schedule_id)
#     현재 대화 범위에서 schedule_id가 같은 일정만 제거합니다. 다른 대화 범위의 일정은 같은 ID처럼 보여도 지우지 않습니다.
#
#   - week01_tools()
#     Week 1 agent가 사용할 수 있는 tool 목록을 반환합니다. create_agent(...)가 이 목록을 보고 tool calling을 수행합니다.
#
#   - week01_system_prompt() / week01_prompt_parts()
#     Week 1 agent의 역할, 현재 날짜, tool 사용 규칙을 담은 system prompt를 만듭니다.
#
#   - build_week01_agent() / build_week_agent()
#     LangChain agent를 한 번만 만들고 재사용합니다. build_week_agent()는 실행기에서 공통으로 호출하는 표준 이름입니다.
#
#   - list_personal_schedule_dicts(...)
#     tool이 아닌 내부 helper입니다. 다른 주차 코드가 Week 1 임시 일정을 dict list로 바로 읽어야 할 때 사용합니다.
#
#   - ensure_demo_personal_schedule()
#     데모/테스트에서 빈 일정 저장소를 피하려고 기본 임시 일정을 하나 넣습니다. 이미 일정이 있으면 아무 일도 하지 않습니다.
