# 작업 목표

Week 1 tool이 만든 JSON payload나 사용자의 한국어 자연어 요청을, LLM structured output(`response_format`)으로
뽑아낼 수 있는 두 개의 Pydantic 스키마를 완성한다.

- `StructuredRequest`: 자연어 요청 한 건을 종류/제목/날짜/시간/멤버/우선순위/근거/원문 필드로 구조화한 결과.
- `StructuredRequestBatch`: `StructuredRequest` 여러 개(요청이 하나뿐이어도 항상 list)와, 상대 날짜 해석
  기준일(`base_date`)을 함께 담는 최종 `structured_response` 스키마.

# 수정 범위

- 수정 대상 파일은 `./week02_structure_natural_language_requests.py` 하나뿐이다. 그 외의 코드/파일
  (`fixed/` 등)은 건드리지 않는다.
- 이번 문서가 다루는 구현 대상은 `StructuredRequest`, `StructuredRequestBatch` 두 클래스(필드 선언 +
  클래스 docstring)뿐이다.
- `week02_tools`, `week02_system_prompt`, `week02_prompt_parts`, `build_week02_agent`,
  `extract_structured_request`, `_coerce_structured_request`, `extract_schedule_request` 등 나머지
  `# TODO`는 이번 작업 범위가 아니다. 이번 문서의 지시로 건드리지 않는다.

# 하지 말아야 할 것

- `RequestKind`, `current_app_date_iso` 등 파일에 이미 정의/import된 타입과 헬퍼를 재정의하지 않고 그대로
  사용한다.
- 이 문서에 없는 필드나 검증 로직(예: `requests`의 최소 길이 제한 등)을 임의로 추가하지 않는다. 스펙에
  없는 제약은 나중에 실제로 필요해질 때 추가한다.
  - (개정) `date`와 `base_date` 간 교차 검증은 과거에는 이 목록에 있었으나, 리뷰 결과 `date`가
    `start_time`/`end_time`과 독립적인 필드가 아니라는 점이 확인되어 아래 "StructuredRequestBatch
    model_validator" 항목의 정식 스펙으로 승격되었다.
- `student_parts/week01_claude.md`는 Week 1용 문서다. 형식이 이번 문서와 다르며, 스타일이 필요할 때만
  참고자료로 확인하고 내용을 그대로 따를 필요는 없다.

# StructuredRequest ↔ StructuredRequestBatch 관계

- `StructuredRequestBatch`는 `StructuredRequest`를 필드로 가진다. 여러 개의 구조화된 pydantic 출력을 더
  높은 정확도로 얻기 위한 구조다.
- 요청이 하나뿐이어도 항상 `StructuredRequestBatch` 형태(즉 `requests` 안에 `StructuredRequest` 하나가 든
  list)로 반환되어야 한다. `StructuredRequest`를 단독으로 최상위 `response_format`으로 쓰지 않는다.

# 공통 규칙 (두 클래스 모두 해당)

- 클래스 docstring은 `response_format`으로 LLM에 노출되는 JSON schema의 description으로 그대로 쓰인다.
  현재 두 클래스의 docstring은 각각 한 문장짜리로 빈약하다. 아래 "클래스 docstring 보강" 항목의 내용이
  드러나도록 다시 쓴다.
- 각 필드의 `description`에는 반드시 한국어로 된 간결한 필드 설명을 넣는다.
- 출력 형태나 fewshot 예시가 실제로 스펙에 주어진 필드에 한해서만 `Field(examples=[...])`를 함께 단다.
  예시가 없는 필드(`title`, `reason`, `original_text`, `members` 등)는 억지로 예시를 만들지 않는다.
  - `date`, `start_time`, `end_time`은 아래 pattern으로 출력 형식이 명시되어 있으므로, 그 형식에 맞는
    예시 하나씩을 `examples=[...]`로 넣는다.
  - `kind`, `priority`는 이미 `Literal`로 후보값이 제한되어 값 자체가 스키마에 드러나므로, `examples`를
    추가로 붙이지 않아도 된다.
- 필드는 아래쪽 필드를 채울 때 근거로 참고될 수 있는 필드일수록 클래스 위쪽에 배치한다. (원문/판단근거처럼
  맥락을 주는 필드를 먼저 두어, 뒤쪽 필드가 모호하거나 요청에 없는 값을 추론해 채울 때 참고할 수 있게 한다.)
  구체적 순서는 아래 각 클래스 항목에서 필드 이름으로 명시한다.
- 후보값이 명백히 정해진 필드는 `Literal`로 종류를 제한한다. 값이 없을 수도 있으면서 후보값이 정해진
  경우는 `Literal[...] | None` 형태로 선언한다. (`kind`, `priority`)
- 값 범위 제약은 `Field(pattern=...)`로 정규식을 걸어 "타입은 맞지만 값이 터무니없는" 경우를 막는다.
  - `date`: `Field(pattern=r"^\d{4}-\d{2}-\d{2}$")`
  - `start_time`, `end_time`: `Field(pattern=r"^\d{2}:\d{2}$")`
  - 값이 `None`이면 pattern 검증은 적용되지 않는다. (Optional 필드에 값이 있을 때만 형식을 검사한다.)
- 필드 간 교차 검증은 `model_validator`로 처리한다. `start_time`과 `end_time`이 **둘 다 값이 있을 때만**
  `end_time`이 `start_time`보다 빠르지 않은지 검증한다. 둘 중 하나라도 `None`이면 비교 없이 그대로
  통과시킨다. (`"HH:MM"` 형식 문자열은 그대로 사전식 비교해도 시간 순서와 일치한다.)
  `start_time == end_time`은 허용한다 — 특정 시각 하나만 의미 있는 요청(알람/리마인더성)을 표현할 수
  있어야 하므로, "더 빠르면 안 된다"만 막고 "같아도 된다"는 열어둔다.
- `kind == "unknown"`인데 `reason`이 비어 있으면 `model_validator`로 에러를 낸다. `unknown`은 "분류에
  실패해 default가 그대로 남은 경우"와 "정말 4개 분류 중 어디에도 안 속한다고 판단한 경우"를 구분하지
  못하면 의미가 없으므로, 후자임을 확인할 수 있도록 이유를 필수로 남긴다.

# StructuredRequest 스키마 명세

## 필드 선언 순서

```
original_text → reason → kind → title → date → start_time → end_time → members → priority
```

원문을 가장 먼저 두어 이후 모든 판단의 근거로 삼고, 판단 근거(`reason`) → 요청 종류(`kind`)를 먼저 정한
뒤 나머지 세부 항목을 채우는 순서다.

## 필드별 타입/기본값

| 필드            | 타입                                    | 기본값                        |
|-----------------|-----------------------------------------|--------------------------------|
| `original_text` | `str`                                   | `""`                            |
| `reason`        | `str \| None`                           | `None`                          |
| `kind`          | `RequestKind`                           | `Field(default="unknown", ...)` |
| `title`         | `str \| None`                           | `None`                          |
| `date`          | `str \| None` (+ pattern)               | `None`                          |
| `start_time`    | `str \| None` (+ pattern)               | `None`                          |
| `end_time`      | `str \| None` (+ pattern)               | `None`                          |
| `members`       | `list[str]`                             | `Field(default_factory=list)`   |
| `priority`      | `Literal["high", "medium", "low"] \| None` | `None`                       |

## 필드별 세부 요구사항

- `kind`: 이미 정의된 `RequestKind` Literal(`personal_schedule`, `group_schedule`, `todo`, `reminder`,
  `unknown`)만 허용한다. `Field(default="unknown", description=...)`로 선언한다. `unknown`은 4개 분류
  중 어디에도 명확히 속하지 않을 때만 선택하고, 이때는 `reason`에 이유를 반드시 남긴다(아래
  model_validator 참고).
- `title`/`date`/`start_time`/`end_time`: `str | None`, 기본값 `None`. `date`/`start_time`/`end_time`에는
  위 공통 규칙의 `pattern`을 건다. `date`가 `None`이면, 원문에 날짜 언급이 아예 없었는지 아니면 날짜를
  언급했지만 특정할 수 없었는지를 `reason`에 남기도록 description에 명시한다 — 이 둘을 구분하지 않으면
  "언급 없음(시간만 있으면 base_date로 보정해도 안전)"과 "언급했으나 파싱 실패(보정하면 틀린 값이 될 수
  있음)"를 나중에 구분할 수 없다.
- `members`: `list[str]`, `default_factory=list`. 모르면 빈 list로 둔다.
- `priority`: `Literal["high", "medium", "low"] | None`, 기본값 `None`.
- `reason`: `str | None`, 기본값 `None`. `kind`/`date` 등 여러 필드 값을 그렇게 판단한 근거를 함께
  담는 범용 필드다. `kind`가 `unknown`이면 이 필드는 사실상 필수(비어 있으면 model_validator가 에러를
  낸다). `date`가 `None`인 경우에도 위 두 원인 중 어느 쪽인지 여기에 남긴다(다만 이 경우는 강제
  검증하지 않는다 — `date`가 없는 게 흔한 정상 케이스라 매번 `reason`을 요구하면 과하다).
- `original_text`: `str`, 기본값 `""`. 사용자가 입력한 원문 보존용.
- 모르는 값을 억지로 만들지 않는다. 확실하지 않으면 `None` 또는 빈 list가 안전하다는 점을 각 필드
  description에도 드러낸다.

## model_validator

- `mode="after"`인 model validator를 하나 추가해 `start_time`/`end_time`이 둘 다 있을 때만 시간 순서를
  검증한다. (조합이 모순인 경우만 잡아내고, 개별 필드가 `None`이면 통과시킨다.) `start_time == end_time`은
  허용한다 — 알람/리마인더처럼 특정 시각 하나만 의미 있는 요청을 표현할 수 있어야 하기 때문이다.
- `mode="after"`인 model validator를 하나 더 추가해, `kind == "unknown"`인데 `reason`이 비어 있으면
  에러를 낸다. `unknown`이 "분류 실패로 default가 그대로 남은 경우"와 "정말 애매해서 unknown으로 판단한
  경우"를 구분하지 못하면 디버깅에 쓸모가 없으므로, 후자임을 이유로 확인할 수 있게 한다.

## 클래스 docstring 보강

- 현재 `"""LLM structured output으로 추출되는 2주차 요청 스키마입니다."""`는 이 클래스가 무엇을 추출해야
  하는지, `StructuredRequestBatch`와 어떤 관계인지 드러나지 않아 빈약하다. 아래 내용이 드러나도록 다시
  쓴다.
  - 자연어 한 문장(또는 Week 1 tool 결과 JSON)에서 뽑아내는 개별 요청 하나를 표현한다는 것.
  - `kind`로 요청 종류를 먼저 판단하고, 그 판단 근거를 `reason`에 남긴다는 것.
  - 확실하지 않은 값은 `None`/빈 list로 남겨도 된다는 것(억지 추론 금지).
- `[2주차 1회차 수강생 구현 가이드]` 주석을 베끼지 않고 위 내용만 반영해 직접 쓴다.

# StructuredRequestBatch 스키마 명세

## 필드 선언 순서

```
base_date → requests
```

상대 날짜 해석 기준일을 먼저 정의해, 이후 `requests` 안 각 `StructuredRequest.date` 해석의 근거로 삼는다.

## 필드별 타입/기본값

| 필드       | 타입                          | 기본값                                          |
|------------|-------------------------------|--------------------------------------------------|
| `base_date`| `str`                          | `Field(default_factory=current_app_date_iso, ...)`|
| `requests` | `list[StructuredRequest]`      | `Field(default_factory=list, ...)`                |

## 필드별 세부 요구사항

- `base_date`: `current_app_date_iso`를 그대로 재사용해 오늘 날짜로 채운다. description에는 이 값이
  `requests` 안 각 `StructuredRequest.date` 필드의 상대 날짜(예: "내일", "다음 주 화요일") 해석 기준일로
  쓰인다는 점을 명시한다.
- `requests`: 요청이 하나뿐이어도 `StructuredRequest` 하나가 든 list로 채운다. description에 이 규칙을
  명시한다.

## model_validator

- `mode="after"`인 model validator를 추가해, `requests` 안 각 `StructuredRequest`에 대해 `date`가
  `None`이고 `start_time`/`end_time` 중 하나라도 값이 있으면 `date`를 `base_date`로 채운다. `date`는
  `start_time`/`end_time`과 독립적인 필드가 아니다 — 시간이 정해졌다는 것은 이미 "언제"가 암묵적으로
  정해졌다는 뜻이므로, `date`만 비어 있는 상태로 두지 않는다. 이 교차 검증은 `base_date`(Batch 레벨)가
  있어야 판단 가능하므로 `StructuredRequest`가 아니라 `StructuredRequestBatch`에 둔다.
  - 알려진 한계: `date`가 `None`인 이유가 "날짜 언급 자체가 없음"과 "언급했으나 파싱 실패"로 갈릴 수
    있는데, 이 validator는 둘을 구분하지 않고 항상 `base_date`로 채운다. `reason`(위 `StructuredRequest`
    필드 요구사항 참고)에 어느 쪽인지 근거가 남으므로, 필요하면 사람이 `reason`을 보고 잘못 보정된
    케이스를 사후에 구분할 수 있다 — 코드가 자동으로 분기하지는 않는다(자유 텍스트 검증의 한계).

## 클래스 docstring 보강

- 현재 `"""여러 자연어 의도를 StructuredRequest 목록으로 나누는 2차 과제 스키마입니다."""`도 
  `StructuredRequest`와의 관계, "요청이 하나여도 list" 규칙이 드러나지 않아 빈약하다. 아래 내용이
  드러나도록 다시 쓴다.
  - 이 스키마가 Week 2 agent의 최종 `structured_response`(response_format)라는 것.
  - `requests`가 `StructuredRequest`의 list이며, 요청이 하나뿐이어도 list 형태를 유지한다는 것.
  - `base_date`가 `requests` 안 상대 날짜 표현 해석의 기준일이라는 것.
  - `requests` 안 항목의 `date`가 비어 있는데 `start_time`/`end_time` 중 하나라도 있으면 `base_date`로
    보정한다는 것.

# 참고자료

- `student_parts/week01_claude.md`: Week 1(`personal_create_schedule` 등 3개 tool 구현)용 문서. 형식이
  다르므로 그대로 따르지 않고, 문서 스타일이 필요할 때만 참고한다.
- `week01_prompt_parts()`, `week01_tools()`: 이번 두 클래스 구현과는 직접 관련 없다. (다음 문서에서
  `week02_tools`/`week02_prompt_parts` 등을 다룰 때 참고한다.)

# 검증 방법

두 클래스만으로는 아직 `./run.sh --week2`가 완전히 동작하지 않는다(`week02_tools` 등 나머지 `# TODO`가
남아 있으므로). 이번 단계에서는 아래로 스키마 자체를 검증한다.

```python
from student_parts.week02_structure_natural_language_requests import (
    StructuredRequest,
    StructuredRequestBatch,
)

batch = StructuredRequestBatch(
    requests=[
        StructuredRequest(
            original_text="다음 주 화요일 오후 3시에 철수랑 회의 잡아줘",
            kind="group_schedule",
            start_time="15:00",
            end_time="14:00",  # end_time < start_time -> model_validator에서 에러가 나야 함
        )
    ]
)
```

- `StructuredRequestBatch.model_json_schema()`를 찍어 각 필드 description/examples/pattern이 의도대로
  노출되는지 확인한다.
- `date`/`start_time`/`end_time`에 형식에 맞지 않는 값(`"2026-13-99"`, `"9:5"` 등)을 넣었을 때
  `ValidationError`가 나는지 확인한다.
- `end_time`이 `start_time`보다 빠른 값을 넣었을 때 model_validator가 에러를 내는지 확인한다.
- `start_time == end_time`을 넣었을 때는 에러 없이 통과하는지 확인한다.
- `kind="unknown"`인데 `reason`이 비어 있으면 `ValidationError`가 나는지, `reason`을 채우면 통과하는지
  확인한다.
- `date=None`, `start_time="15:00"`인 `StructuredRequest`를 `StructuredRequestBatch`에 넣었을 때,
  결과 `requests[0].date`가 `base_date`로 채워지는지 확인한다.

---

# [추가 작업] week02_system_prompt / week02_prompt_parts 구현 가이드

# 작업 목표

`student_parts/week02_structure_natural_language_requests.py`의 `week02_prompt_parts()`와
`week02_system_prompt()`를 완성해, Week 2 agent가 자연어 요청이나 Week 1 tool JSON을
`StructuredRequestBatch`로 구조화해 최종 답변으로 반환하도록 만든다.

# 수정 범위

- 수정 대상 함수는 `week02_prompt_parts()`, `week02_system_prompt()` 두 개뿐이다.
- `StructuredRequest`, `StructuredRequestBatch`, `week02_tools()`, `build_week02_agent()`,
  `_coerce_structured_request`, `extract_structured_request`, `extract_schedule_request`는 이번
  작업 범위가 아니다. 건드리지 않는다.
- 이미 구현된 `join_system_prompt`, `week01_prompt_parts()`, `current_app_date_iso()`는 그대로
  재사용하고 재정의하지 않는다.

# 하지 말아야 할 것

- `week01_prompt_parts()`가 반환한 조각을 지우거나 순서를 바꾸지 않는다. `week02_prompt_parts()`는
  그 뒤에 Week 2 전용 조각을 이어 붙이기만 한다.
- 여기 없는 새로운 규칙(Week 3 이상의 저장/조율 관련 지시 등)을 임의로 추가하지 않는다.

# week02_prompt_parts() 구현 명세

`week01_prompt_parts()`가 반환한 list 뒤에, 아래 내용이 드러나는 문자열 조각(들)을 이어 붙인다.

- Week 2 agent의 목적은 Week 1 에이전트의 출력(자연어 답변 또는 tool 호출 결과)을
  `StructuredRequest` 필드(`kind`/`title`/`date`/`start_time`/`end_time`/`members` 등)로 구조화된
  pydantic 출력으로 반환하는 것임을 명시한다.
- 현재 날짜는 `current_app_date_iso()`를 호출한 실행 시점 값이며, 사용자가 "내일", "다음 주
  화요일"처럼 상대적인 날짜로 말하면 이 날짜를 기준으로 계산한다는 것을 명시한다.
- Week 1 tool 호출 결과로 JSON을 받은 경우, 그 내용을 다시 만들기 위해 tool을 재호출하지 않고 받은
  payload를 그대로 읽어 `structured_response`를 채운다는 것을 명시한다.
- Week 2에서는 SQLite 저장, RAG, 외부 멤버 일정 조율을 하지 않는다는 범위 제한을 명시한다.

# week02_system_prompt() 구현 명세

- `join_system_prompt(...)`로 `week02_prompt_parts()`가 반환한 조각들과 Week 2 전용 최종 답변
  규칙을 합쳐 하나의 system prompt 문자열로 반환한다. (`week01_system_prompt()`가
  `join_system_prompt(week01_prompt_parts())`를 쓰는 것과 동일한 패턴이다.)
- 최종 답변(`structured_response`) 규칙에는 아래 내용이 드러나야 한다.
  - 단일 요청을 구조화하는 경우에도 최상위 `structured_response`는 `StructuredRequest` 단독이
    아니라 항상 `StructuredRequestBatch`(요청 하나가 든 `requests` list) 형태여야 한다는 것.
  - 개인 일정 생성 요청은 `personal_create_schedule` tool 결과 JSON의 `created_schedule` 필드를
    읽어 `StructuredRequest`의 각 필드(title/date/start_time/end_time/members 등)를 채운다는 것.
  - 조회(`personal_list_schedules`)나 삭제(`personal_delete_schedule`) 요청에는 tool 호출 결과를
    `StructuredRequestBatch`로 구조화하지 않고, tool 호출 결과를 자연어 문장으로 요약해서 답한다는
    것. 즉 조회/삭제 요청은 `structured_response`를 만드는 대상이 아니다.

# 참고자료

- `week01_system_prompt()`, `week01_prompt_parts()` (`student_parts/week01_wake_up_nana.py`):
  `join_system_prompt` 사용 패턴과 prompt 조각을 누적하는 방식의 예시로 참고한다.
- 이 문서 앞부분의 `StructuredRequest`/`StructuredRequestBatch` 스키마 명세: 여기서 지시하는
  필드/구조 규칙이 이번 prompt 지시 내용의 근거가 된다.

# 검증 방법

- `./run.sh --week2`로 실행한 뒤 "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"처럼 개인 일정 생성
  요청을 넣어, 최종 답변이 `StructuredRequestBatch` 형태의 `structured_response`로 나오는지
  확인한다.
- "내 일정 보여줘" 같은 조회 요청과 "그 일정 지워줘" 같은 삭제 요청을 넣어, 최종 답변이 구조화 JSON이
  아니라 자연어 요약 문장으로 나오는지 확인한다.
- 요청이 하나뿐인 입력에서도 `requests` 안에 `StructuredRequest`가 정확히 하나만 들어 있는지
  확인한다.

---

# [추가 개선] build_week02_agent() response_format 전략 변경

# 배경

- `build_week02_agent()`가 `response_format=StructuredRequestBatch`를 그대로 넘기면, `create_agent`는
  모델 이름(`fixed/config.py`의 `openai_model = "openai/gpt-4.1-mini"`)에 `"gpt-4.1"`이 포함된다는
  이유만으로 자동으로 provider native structured output 전략(`ProviderStrategy`)을 선택한다.
- 이 전략은 모델의 최종 답변 텍스트(`AIMessage.content`) 전체를 그대로 `json.loads()`로 파싱한다
  (`langchain/agents/structured_output.py`의 `ProviderStrategyBinding.parse`). 그런데 이 프로젝트가
  쓰는 프록시(`mlapi.run`) 뒤의 실제 모델은 OpenAI의 strict json_schema 계약을 완벽히 지키지 않고,
  같은 JSON을 텍스트에 통째로 두 번 반복해서 내보내는 경우가 있었다. 그 결과
  `StructuredOutputValidationError: ... Extra data: line 2 column 1 (char ...)`가 발생했다
  (`agent.invoke(...)`로 직접 재현·확인함).
- 이 문제는 `StructuredRequest`/`StructuredRequestBatch` 스키마나 `week02_prompt_parts()` 내용과
  무관하게, 스키마가 아주 단순한 요청에서도 동일하게 재현됐다. 즉 스키마/프롬프트를 고쳐서 해결할 수
  있는 문제가 아니었다.

# 조치

- `build_week02_agent()`의 `response_format`을 `StructuredRequestBatch`(그대로 = native 전략 자동
  선택)에서 `langchain.agents.structured_output.ToolStrategy(StructuredRequestBatch)`로 바꿨다.
- `ToolStrategy`는 최종 구조화 출력을 자유 텍스트가 아니라 tool-call의 `arguments`(이미 파싱된
  `dict`)로 받는다(`OutputToolBinding.parse`는 `json.loads`를 호출하지 않는다). tool-call 인수는
  API 응답 구조상 자유 텍스트처럼 통째로 반복될 여지가 없고, `handle_errors` 재시도 로직도 포함돼
  있어 이 프록시 모델에서 더 안정적으로 동작한다.
- Week 1 tool(`personal_create_schedule` 등) 호출 자체는 이 변경과 무관하게 항상 정상 동작했다(tool
  호출은 일반 function-calling 경로를 타므로 native/tool 구조화 출력 전략 선택과 별개다). 이번 문제는
  Week 1 tool 결과를 다 받은 **다음**, 최종 답변을 `StructuredRequestBatch` 모양으로 포장하는 마지막
  단계에서만 있었다.
