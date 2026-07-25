from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from fixed.llm import chat_model
from student_parts.week04.schemas import MemoryRoute


MEMORY_ROUTER_PROMPT = """
structured:
- 일정, 할 일, 알람으로 명시적으로 저장된 기록을 찾을 때만 선택한다.

reference:
- 사용자가 '참고자료', '자료', '문서', '메모', '링크', '저장한 자료'처럼
- 사용자가 저장한 자료를 명시적으로 찾을 때 선택한다.
  
conversation:
- 사용자가 한 일반적인 대화를 의미한다.
- 저장 경로가 명확하지 않으면 conversation을 기본값으로 선택한다.
  
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