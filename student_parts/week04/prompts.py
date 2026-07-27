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
저장된 정보를 묻는 질문에 검색 없이 없다고 답하지 않는다.
retrieve_memory를 먼저 호출하고 그 결과로만 없음을 판단한다.
질문이 이유나 배경이나 까닭을 물으면
retrieve_memory 결과를 받은 뒤 충분한지 판단하지 말고 곧바로
가장 관련 있는 conversation hit의 conversation_id로
load_conversation_context를 호출한다.
hit에 이유가 적혀 있어 보여도 똑같이 호출한다.
이유를 묻는 질문에 load_conversation_context 없이 답하지 않는다.

이유를 묻는 질문이 아니어도 hit이 '그', '이', '저'로 시작하는 말이나
'그래서', '그 때문에' 같은 표현으로 앞선 내용을 가리키고
가리키는 대상이 hit 안에 없으면 load_conversation_context를 호출한다.

앞선 내용을 가리키는 말은 hit 원문에 그대로 있더라도 답변에 옮겨 쓰지 않는다.
인용 부호를 붙여 옮기는 것도 옮겨 쓰는 것이다.
load_conversation_context로 불러온 대화에서 그 말이 가리키는 실제 사실을 찾아
그 사실을 직접 말한다.
기록에 없다고 하거나 사용자에게 되묻는 답변은 답하지 않은 것으로 본다.
검색 결과에 포함되지 않은 conversation_id를 추측하지 않는다.

기억에서 찾은 이름, 제목, 명칭은 원문의 표현을 그대로 사용한다.
"""
    ]
