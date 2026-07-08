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

- 파일 상단의 `[2주차 1회차 수강생 구현 가이드]` 주석은 출제 의도 확인용으로, **컨닝하지 않는다**. 그
  주석 내용을 그대로 베끼거나 답을 그 주석에서 가져오는 방식으로 구현하지 않는다.
- `RequestKind`, `current_app_date_iso` 등 파일에 이미 정의/import된 타입과 헬퍼를 재정의하지 않고 그대로
  사용한다.
- 이 문서에 없는 필드나 검증 로직(예: `requests`의 최소 길이 제한, `date`와 `base_date` 간 교차 검증 등)을
  임의로 추가하지 않는다. 스펙에 없는 제약은 나중에 실제로 필요해질 때 추가한다.
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
  `unknown`)만 허용한다. `Field(default="unknown", description=...)`로 선언한다.
- `title`/`date`/`start_time`/`end_time`: `str | None`, 기본값 `None`. `date`/`start_time`/`end_time`에는
  위 공통 규칙의 `pattern`을 건다.
- `members`: `list[str]`, `default_factory=list`. 모르면 빈 list로 둔다.
- `priority`: `Literal["high", "medium", "low"] | None`, 기본값 `None`.
- `reason`: `str | None`, 기본값 `None`. `kind`/필드 값을 그렇게 판단한 근거.
- `original_text`: `str`, 기본값 `""`. 사용자가 입력한 원문 보존용.
- 모르는 값을 억지로 만들지 않는다. 확실하지 않으면 `None` 또는 빈 list가 안전하다는 점을 각 필드
  description에도 드러낸다.

## model_validator

- `mode="after"`인 model validator를 하나 추가해 `start_time`/`end_time`이 둘 다 있을 때만 시간 순서를
  검증한다. (조합이 모순인 경우만 잡아내고, 개별 필드가 `None`이면 통과시킨다.)

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

## 클래스 docstring 보강

- 현재 `"""여러 자연어 의도를 StructuredRequest 목록으로 나누는 2차 과제 스키마입니다."""`도 
  `StructuredRequest`와의 관계, "요청이 하나여도 list" 규칙이 드러나지 않아 빈약하다. 아래 내용이
  드러나도록 다시 쓴다.
  - 이 스키마가 Week 2 agent의 최종 `structured_response`(response_format)라는 것.
  - `requests`가 `StructuredRequest`의 list이며, 요청이 하나뿐이어도 list 형태를 유지한다는 것.
  - `base_date`가 `requests` 안 상대 날짜 표현 해석의 기준일이라는 것.

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
