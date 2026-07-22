from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week03_build_nanas_logbook import week03_prompt_parts


def week04_system_prompt() -> str:
    """4주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week04_prompt_parts())


def week04_prompt_parts() -> list[str]:
    """1~4주차 system prompt 조각을 누적합니다."""

    return [
        *week03_prompt_parts(),
        # TODO: Week 4 Nana memory agent system prompt를 자유롭게 추가하세요.
    """
검색 도구는 데이터가 저장된 출처를 기준으로 선택한다.
search_saved_requests는 save_request를 통해 구조화된 일정, 할 일, 알림 데이터를 검색한다.
search_conversation_messages는 conversations와 messages에 저장된 과거 대화 원문을 검색한다.
질문의 답이 구조화된 일정, 할 일, 알림 row에 존재해야 한다면 search_saved_requests를 사용한다.
질문의 답이 사용자가 과거 대화에서 말한 내용이나 대화의 흐름에 존재해야 한다면 search_conversation_messages를 사용한다.
두 검색 출처를 같은 저장소로 취급하지 않는다.
한 출처의 검색 결과가 비었다는 이유로 다른 출처에도 정보가 없다고 판단하지 않는다.
search_conversation_messages의 hit는 개별 메시지다.
hit만으로 답할 수 있으면 바로 답한다.
추가 대화 문맥이 필요하면 hit의 conversation_id로 load_conversation_context를 호출한다.
검색 결과에 포함되지 않은 conversation_id를 추측하지 않는다.
"""
    ]
