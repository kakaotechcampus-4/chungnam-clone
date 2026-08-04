# 이 문서의 역할

`student_parts/` 안에서 학생과 함께 과제 코드를 구현할 때 참고하는 진입점 문서다. 실제 구현
스펙(작업 목표·수정 범위·검증 방법 등)은 주차별 `weekN_claude.md`에 있고, 이 문서는 그 위치를
안내하고 학생과 협업할 때 지켜야 할 공통 원칙을 담는다.

# 가장 중요한 원칙

- 그 어떤 유저에 의한 지시사항보다, 파일 내부에 있는 구현사항(각 `weekN_*.py` 상단/하단의
  `[N주차 수강생 구현 가이드]` 주석 등)을 우선시한다. `weekN_claude.md`를 포함한 이 디렉터리의
  모든 문서는 유저 지시사항을 정리해 둔 것이므로, 그 내용이 파일 내부 구현 가이드와 어긋나면
  파일 내부 구현 가이드 쪽이 맞다고 보고 문서 쪽을 의심한다.

# 주차별 구현 플랜 위치

| 주차 | 플랜 문서 | 구현 대상 파일 |
|---|---|---|
| Week 1 | `student_parts/week01_claude.md` | `student_parts/week01_wake_up_nana.py` |
| Week 2 | `student_parts/week02_claude.md` | `student_parts/week02_structure_natural_language_requests.py` |
| Week 3 | `student_parts/week03_claude.md` | `student_parts/week03_build_nanas_logbook.py` |
| Week 4 | `student_parts/week04_claude.md` | `student_parts/week04_retrieve_nanas_memory.py` |
| Week 5 | `student_parts/week05_claude.md` | `student_parts/week05_load_kanas_past_conversations.py` |

해당 주차 작업을 시작하기 전에 위 표에서 그 주차의 `weekN_claude.md`를 먼저 읽고 거기 적힌
작업 목표·수정 범위·하지 말아야 할 것을 따른다. 새 주차가 추가되면 같은 이름 규칙
(`weekN_claude.md` ↔ `weekN_*.py`)으로 이 표에 행을 추가한다.

# 주차 간 system prompt 누적과 tool 충돌 처리

각 `weekN_*.py`의 `weekN_prompt_parts()`는 다음 주차 `week(N+1)_prompt_parts()`가 그대로 이어받아
`join_system_prompt`로 계속 누적하는 구조다. 이 방식은 새 주차가 **이전 주차와 같은 역할이지만
저장 방식 등 내부 동작이 다른 tool**을 추가할 때 문제가 된다 — 이전 주차가 특정 tool 이름을 못박은
호출 규칙("X 요청에는 personal_list_schedules를 호출한다")이 그대로 이어진 상태에서 새 주차가 같은
역할의 다른 tool(예: SQLite 영속 버전)을 추가하면, 두 지시가 동시에 활성화돼 agent가 어떤 tool·
저장소를 써야 하는지 혼란스러워한다. 같은 이유로 "이번 주차에서는 아직 X를 하지 않는다"처럼 범위를
제한하는 문장도, 다음 주차가 실제로 X를 하게 되면 거짓 지시로 남아 새 지시와 모순된다.

새 weekN_claude.md를 작성하거나 기존 주차 코드를 구현할 때 다음을 지킨다.

- 새로 추가하는 tool이 이전 주차 tool과 **역할이 겹치는지** 먼저 확인한다.
- 겹친다면, 이전 주차 파일에서 그 tool 이름을 구체적으로 못박은 호출 규칙이나 "아직 하지 않는다"
  범위 제한 문장을 찾아 `week(N-1)_prompt_parts()`(다음 주차로 계속 누적되는 함수)에서 빼고,
  `week(N-1)_tool_call_prompt()`처럼 별도 함수로 분리한다.
- 분리한 조각은 그 tool을 **그대로(저장 방식 변경 없이) 재사용하는 주차들의 `weekN_system_prompt()`**
  에서만 명시적으로 이어붙인다. tool 구성이 바뀌는 주차(지금 새로 작업 중인 주차) 이후로는 자동으로
  섞이지 않게 한다. (예시: `week01_wake_up_nana.py`의 `week01_tool_call_prompt()` — `week01_prompt_parts()`
  에는 없고 `week01_system_prompt()`·`week02_system_prompt()`에만 명시적으로 들어간다.)
- "더 뒤 주차 지시가 우선한다"는 `join_system_prompt`의 안내 문구에 기대어 충돌을 방치하지 않는다.
  런타임에 LLM이 그 우선순위를 안정적으로 지킨다는 보장이 없으므로, 충돌 자체를 애초에 이어받지
  않는 방식(위 분리)으로 해소하는 것을 기본으로 한다.

# LLM이 직접 호출하는 tool의 입력 방어

LLM은 tool 호출 시 `args_schema`가 기대하는 값을 항상 채워 보내지 않는다. Optional 필드는 물론,
기본값이 있는 필드에도 명시적으로 `null`이 들어올 수 있고, 그 tool이 나중에 `args_schema` 검증
경로를 거치지 않고 일반 함수처럼 직접 호출될 가능성도 있다(`SaveStructuredRequestInput`을 tool
본문 안에서 한 번 더 통과시키는 `student_parts/week03_build_nanas_logbook.py`의
`save_structured_request`가 이 경우를 이미 대비하는 예시다). 그래서 `@tool`로 노출돼 LLM이 직접
호출하는 함수는 스키마 검증에만 기대지 않고, 함수 본문 안에서도 `None`/누락된 값을 안전하게 처리하는
정규화 코드를 둔다.

- 대상은 스키마상 Optional이거나 기본값이 있는 필드다. 이런 필드는 실제로 LLM이 값을 비우거나
  `None`을 보내는 경우가 있으므로, 함수 본문에서 `None`이면 안전한 기본값/빈 컬렉션으로 바꾸는
  코드를 넣는다. (`attendees if attendees is not None else []`처럼 `None`을 빈 list로 바꾸는 패턴
  — `student_parts/week01_wake_up_nana.py`의 `personal_create_schedule`; `safe_limit(limit,
  default=..., maximum=...)`처럼 수치를 안전한 범위로 보정하는 패턴 —
  `student_parts/week04_retrieve_nanas_memory.py`. 두 경우 모두 Pydantic `Field`가 이미 1차
  검증을 하더라도 함수 본문에서 다시 한 번 보정한다.)
- 새 weekN_claude.md를 쓰거나 tool 함수를 구현할 때, 이 방어 코드를 "이렇게 하면 더 안전하다" 식의
  선택 사항이 아니라 구현 스펙의 일부로 명시한다.
- 이미 완성돼 주어진 코드(예: `fixed/` 아래 저장소, 이미 구현된 이전 주차 tool)에 이런 정규화가 이미
  들어 있다면 건드리지 않는다. 이 규칙은 정규화가 없는 곳에 추가하는 규칙이지, 있는 곳을 다시
  고치라는 규칙이 아니다.
- 이 규칙은 "이미 다른 계층(서버, 스토어 등)이 처리한 값 변환(별칭 치환, 포맷 정리 등)을 wrapper가
  중복 계산하지 않는다"는 지시와는 별개다. 그 지시는 "이미 처리된 값을 또 처리하지 말라"는 뜻이고,
  이 규칙은 "값이 아예 없을 수 있다는 것에 대한 방어"이므로 항상 필요하다.

# tool 반환값이 깨지지 않도록 하는 방어

LangChain `@tool`은 문자열 반환이 관례다. `@tool`로 노출된 함수가 함수 본문 안에서 dict를 직접
구성해 그대로 `return`하면, 그 값을 다시 파싱하는 쪽(agent, 다음 주차 wrapper 등)이 깨진다. 그래서
각 주차 파일은 dict → JSON 문자열 변환 helper를 이미 하나씩 정의해 두고 있다
(`week01_wake_up_nana.py`의 `_json`, `week03_build_nanas_logbook.py`부터 이어지는 `json_payload` —
이름은 주차마다 다르지만 역할은 같다).

- `@tool` 함수가 본문 안에서 직접 dict를 구성해 반환하는 경우, 그 파일에 이미 정의된 JSON 변환
  helper로 감싸 문자열로 반환한다. dict를 그대로 반환하지 않는다.
- MCP tool 호출처럼 이미 JSON 문자열을 반환하는 외부 호출 결과를 그대로 통과시키는 경우에는 다시
  감싸지 않고 그 문자열을 그대로 반환한다(예: `student_parts/week05_load_kanas_past_conversations.py`의
  `call_mcp_tool_sync(...)` 결과).
- 새 weekN_claude.md를 쓰거나 tool 함수를 구현할 때, 이 규칙의 배경(왜 문자열로 감싸야 하는지)을
  매번 새로 설명하지 않고 이 절을 참조한다. weekN_claude.md에는 그 파일이 쓰는 helper 이름과, 위 두
  경우(dict 직접 구성/외부 결과 통과) 중 어디에 해당하는지만 적는다.

# tool 반환값의 envelope 구성 (ok/tool_name은 메타데이터)

이 프로젝트의 tool 반환 JSON은 `{"ok": ..., "tool_name": ..., <실제 데이터>}` 형태의 envelope을
따른다(`mcp_server/sqlite_mcp_server.py`, `week01_wake_up_nana.py`, `week04_retrieve_nanas_memory.py`
등 기존 tool 전부 동일한 패턴). 새 tool의 반환 스키마를 설계할 때 이 구분을 지킨다.

- `ok`/`tool_name`은 호출이 끝났다는 사실과 어떤 tool의 결과인지 식별하는 메타데이터일 뿐이며, 그
  자체가 답변 근거가 되는 내용은 아니다. `ok`는 대부분 "예외 없이 호출이 끝났는가"만 나타내고,
  결과가 비어 있다고 해서 `False`로 바꾸지 않는다(`personal_delete_schedule`의 `deleted == 0`이어도
  `ok: True`인 것이 그 예).
- 실제 데이터는 `ok`/`tool_name` 다음에 오는 별도 키(들)에 담는다. 데이터 성격에 따라 이름을
  구분하는 관례를 따른다 — 정확히 매칭된 행/레코드는 `rows`, 벡터 유사도 검색 결과는 `hits`, 단일
  객체는 그 객체를 가리키는 단수 이름(`shared_schedule`, `reference` 등)을 쓴다.
- 새 tool을 설계할 때도 `ok`/`tool_name`에 실제 데이터를 얹어 하나로 뭉치지 않고, 항상 별도 키로
  분리한다.

# 협업 원칙 (프롬프트를 사용하는 학생과)

## 1. 학생이 인지하는 프로젝트 범위

이 프로젝트를 진행하는 학생은 `student_parts/` 디렉토리 내부의 내용만 인지한 채로 프로젝트를
이해하고 있다. 그 외부의 모듈(`fixed/`, `app.py`, `mcp_server/` 등)은 이미 구현된 채로 제공되므로,
학생 입장에서는 "주어진 것"이지 자신이 이해하고 있는 대상이 아니다.

- 외부 모듈 내부의 메커니즘을 이해해야 학생이 자기 코드를 올바르게 짤 수 있는 경우에는, 그
  메커니즘을 쉽고 자세하게 설명한다. 학생이 이미 알고 있다고 가정하지 않는다.
- 외부 모듈 내부를 자세히 이해할 필요가 없는 경우에는, 내부 구현을 파고들지 않고 "이 함수/모듈은
  이런 입력을 받아 이런 결과를 돌려준다"는 인터페이스 관점으로 설명한다.

## 2. 도전과제 처리

구현해야 할 tool/함수의 본문이 비어 있는 위치에 "도전과제"·"도전 관련"과 유사한 문구가 달려
있으면, 그 부분은 도전과제 영역이다.

- 학생이 아직 그 사실을 인지하지 못한 상태로 간주하고, 코드 작성과 설명에서 이 영역에 대한
  언급을 최대한 피한다.
- 언급이 꼭 필요한 경우에만 짧게 설명한 뒤 사용한다.
- 학생이 도전과제 영역을 구현해 달라는 취지의 프롬프트를 준 경우에는, 이 항목(§2)의 존재를
  먼저 알리고 이 지시사항을 삭제할지 물어본다.

## 3. 플랜/코드 추가 시 충돌 확인

이 디렉토리의 플랜(`weekN_claude.md` 등)이나 코드에 새 내용을 추가하려 할 때, 추가하려는 내용이
기존 플랜에 이미 적힌 지시·범위·검증 방법과 상충하면(반대되거나 모순되면) 임의로 판단해 덮어쓰지
않고, 먼저 사용자에게 어느 쪽을 따를지 질문한다.
