from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from fixed.llm import chat_model
from student_parts.week04.schemas import MemoryRoute


MEMORY_ROUTER_PROMPT = """
아래 규칙을 위에서부터 순서대로 적용하고, 먼저 맞는 항목 하나를 고른다.

1. reference
   '참고자료', '자료', '문서', '메모', '링크'처럼
   사용자가 저장해 둔 자료를 가리키는 말이 질문에 있으면 reference.

2. structured
   '저장된', '저장한', '등록한' 같은 말과 함께
   '할 일', 'todo', '알림', 'reminder', '일정'을 조회할 때만 structured.

3. conversation
   위 두 경우가 아니면 전부 conversation.

판단 기준은 질문의 소재가 아니라 요청 형태다.
회의, 배포, 조사, 공개, 등산, 운동, 식사처럼 일정처럼 들리는 소재가 나와도
그것만으로 structured를 고르지 않는다.
'내가 ~라고 했던', '전에 말한', '~한 이유가 뭐였지'처럼
과거에 나눈 말을 되짚는 질문은 언제나 conversation이다.

search_query에는 선택한 저장소를 검색할 짧은 핵심어를 넣는다.
반드시 source와 search_query만 반환한다.
""".strip()


_MEMORY_ROUTER: Any | None = None


def memory_router() -> Any:
    """기억 검색 출처를 선택하는 모델을 반환합니다."""

    global _MEMORY_ROUTER

    if _MEMORY_ROUTER is None:
        _MEMORY_ROUTER = chat_model(
            temperature=0,
        ).with_structured_output(
            MemoryRoute,
            method="function_calling",
        )

    return _MEMORY_ROUTER


def route_memory_query(question: str) -> MemoryRoute:
    """사용자 질문의 검색 출처와 검색어를 결정합니다."""

    result = memory_router().invoke(
        [
            SystemMessage(content=MEMORY_ROUTER_PROMPT),
            HumanMessage(content=question),
        ]
    )

    if isinstance(result, MemoryRoute):
        return result

    return MemoryRoute.model_validate(result)