from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from fixed.llm import chat_model
from student_parts.week04.schemas import MemoryRoute


MEMORY_ROUTER_PROMPT = """
사용자의 질문에 답하기 위해 검색할 저장소를 하나만 선택한다.
structured는 save_request를 통해 생성된 구조화 요청 저장소다.
conversation은 conversations와 messages에 저장된 대화 원문 저장소다.
reference는 add_personal_reference를 통해 생성된 개인 참고자료 저장소다.
질문의 표현이 아니라 답이 실제로 저장되어 있을 저장소를 기준으로 판단한다.
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