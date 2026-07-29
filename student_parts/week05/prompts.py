from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week04_retrieve_nanas_memory import week04_prompt_parts


def week05_system_prompt() -> str:
    """5주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week05_prompt_parts())


def week05_prompt_parts() -> list[str]:
    return [
        *week04_prompt_parts(),
        """
외부 멤버의 과거 대화를 찾을 때는 search_previous_conversations를 사용한다.
검색 결과의 전체 대화가 필요하면 conversation_id로
load_conversation_messages를 호출한다.

외부 멤버의 특정 기간 일정을 조회할 때는
extract_schedules_from_history를 사용한다.

내 일정과 외부 멤버 일정을 함께 조회할 때는
collect_member_schedules를 사용한다.

공유 일정 저장소에 등록된 일정을 직접 확인할 때는
list_shared_schedules를 사용한다.

개인 저장 기록과 대화 기억은 기존 Week 3, Week 4 도구를 사용한다.
여러 사람의 최종 회의 시간을 결정하는 것은 Week 6의 역할이다.
"""
    ]
