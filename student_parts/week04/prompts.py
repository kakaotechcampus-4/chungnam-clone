from student_parts.week01_wake_up_nana import join_system_prompt
from student_parts.week03_build_nanas_logbook import week03_prompt_parts


def week04_system_prompt() -> str:
    """4주차 단일 agent가 따르는 시스템 프롬프트입니다."""

    return join_system_prompt(week04_prompt_parts())


def week04_prompt_parts() -> list[str]:
    """1~4주차 system prompt 조각을 누적합니다."""

    return [
        *week03_prompt_parts(),
    """
과거에 저장된 정보의 의미 검색이 필요하면 retrieve_memory를 호출한다.
검색할 저장소는 retrieve_memory 내부에서 결정한다.
검색 저장소를 직접 선택하거나 다른 검색 도구로 대체하지 않는다.
명시적인 목록 조회와 식별자 조회에는 기존 조회 도구를 사용한다.
retrieve_memory의 conversation hit만으로 답할 수 있으면 바로 답한다.
추가 대화 문맥이 필요하면 hit의 conversation_id로 load_conversation_context를 호출한다.
검색 결과에 포함되지 않은 conversation_id를 추측하지 않는다.
"""
    ]
