## 과제 목표

- StructuredRequest 스키마 작성하기
- StructuredRequestBatch 스키마 작성하기
- Week 2 agent 관련 함수 작성하기

---

## 과제 위치

- 작업 브랜치 : `parkjeonghyeon/week2` → 본인 통합 브랜치 `parkjeonghyeon/final` 로 PR
- 주요 파일 : `student_parts/week02_structure_natural_language_requests.py`

---

## 과제 범위

이번 PR 에서 어디까지 했는지 체크해요. (해당하는 곳에 모두)

- [x] 메인 과제 완료
- [x] 심화 과제까지 완료

---

## 구현한 기능

- [x] `StructuredRequest`스키마 작성하기
- [x] `StructuredRequestBatch` 스키마 작성하기
- [x] `week02_tools()`, `week02_prompt_parts()`, `week02_system_prompt()` 함수 구현하기
- [x] `build_week02_agent()` 함수 구현하기

---

## 도전 기능

- [x] `_coerce_structured_request()` 함수 작성
- [x] `extract_structured_request()` 함수 작성
- [x] `extract_schedule_request()` 함수 작성

---

### StructuredRequest 스키마 작성하기

- AI 활용 내용 :

```
2주차 과제 가이드를 참고하여 Pydantic BaseModel을 사용해 `StructuredRequest` 스키마를 작성해두었어.
현재 Field 옵션에 LLM이 이해하기 쉬운 한국어 description을 매끄럽게 수정해줘.
```

위의 프롬프트를 활용하였다.

- 직접 수정한 부분 : title, date, start_time 등의 필수적이지 않은 필드에 default=None을 명시하고, members에 default_factory=list, original_text에 default=""를 지정하여 기본값을 엄격하게 설정했다.
- 수정 이유 : LLM이 자연어 요청에서 특정 정보(시간, 참석자 등)를 추출하지 못했을 때 강제로 임의의 값을 만들어내지 않게 하고, 애플리케이션 실행 중 빈 값으로 인한 오류(ValidationError)를 방지하여 안전하게 처리하기 위함입니다.

### StructuredRequestBatch 스키마 작성하기

- AI 활용 내용 : 따로 AI를 활용하지 않았다.
- 직접 수정한 부분 : 안내된 주석에 따라 잘 수정했다. 특히 TODO에 적힌대로 base_date 필드의 기본값을 설정할 때 default_factory=current_app_date_iso를 적용했다.
- 수정 이유 : 시스템이 동작할 때마다 고정된 텍스트 날짜가 들어가는 것을 막고, 스키마 객체가 생성되는 시점의 현재 시간을 기준으로 항상 최신화된 일정이 잡히도록 보장하기 위해서이다!

### week02_tools(), week02_prompt_parts(), week02_system_prompt() 함수 구현하기

- AI 활용 내용 :

1주차에 만든 week01_tools와 week01_prompt_parts를 가져와서 2주차용 함수인 `week02_tools`, `week02_prompt_parts`, `week02_system_prompt`를 작성해 줘.
2주차 프롬프트에는 사용자의 자연어나 1주차 tool의 결과 JSON payload를 읽어서 `StructuredRequestBatch` 형식으로 변환해야 한다는 명확한 지시를 추가해 줘.

위의 프롬프트를 활용하여 조금 빠르게 구현하였다.

- 직접 수정한 부분 : 프롬프트 내용 중 "Week 2에서는 SQLite 저장, RAG, 외부 멤버 일정 조율을 하지 않습니다."라는 문구를 명시적으로 추가하고, current_app_date_iso() 값을 f-string으로 삽입했다. 또 지난 리뷰를 반영하여 docstring도 활용하였다.
- 수정 이유 : LLM이 임의로 이후 주차에 해당하는 스케줄 DB 저장이나 멤버간 조율 등의 불필요한 행동을 하지 않고, 오직 요청 내용을 구조화하는 그 역할만을 명확하게 알리기 위해서다.

### build_week02_agent() 함수 구현하기

- AI 활용 내용 : 구현할때는 따로 AI를 활용하지 않았고, 오류를 고치며 활용했다.
- 직접 수정한 부분 : 오류를 해결하는 과정에서 create_agent의 매개변수로 response_format=ToolStrategy(StructuredRequestBatch)를 할당하도록 코드를 수정했다.
- 수정 이유 : 에이전트의 최종 결괏값이 단순 문자열이나 일반 JSON dict가 아니라, 앞서 엄격하게 정의한 Pydantic 스키마(StructuredRequestBatch) 형태로 보장되도록 하기 위함이다.

### \_coerce_structured_request(), extract_structured_request(), extract_schedule_request() 함수 작성

- AI 활용 내용 :

```
2주차 가이드 주석 72~88번 줄의 추가 과제를 구현해줘.
_coerce_structured_request는 structured output 결과가 이미 StructuredRequest면 그대로 반환하고,
dict면 model_validate로 검증해서 반환하고, 그 외 형태면 RuntimeError를 내도록 해줘.
extract_structured_request의 리턴값은 꼭 _coerce_structured_request로 정규화해서 StructuredRequest 하나로 반환하게 해줘.
extract_structured_request() 결과에는 ok, tool_name, base_date, structured_request 키를 잘 붙여줘.

```

위의 프롬프트를 활용하여 구현하였다.

- 직접 수정한 부분 : AI의 조언을 받아 isinstance로 세 갈래로 나눠, StructuredRequest면 그대로 반환하고 딕셔너리면 StructuredRequest.model_validate(value)로 검증하며, 둘 다 아니면 받은 타입의 정보를 메시지에 담아서 런타임 에러를 발생시키게 했다.
- 수정 이유 : LLM 응답의 결과가 상황에 따라 모델 객체로 오기도 하고 딕셔너리로 오기도 해서 한쪽으로 정규화가 필요하고, 예상하지 못한 형태가 다음 단계로 흘러가면 뒤로 갈수록 원인을 찾기 힘든 오류가 나기 때문이다.

---

## 구현하면서 고민한 점

- 고민한 점 :
  Week 2 Agent 실행 과정에서 구조화된 출력(Structured Output)과 관련된 두 가지 오류에 직면했다.

1. 프롬프트 설정 단계에서 join_system_prompt()에 잘못된 키워드 인자(system_prompt_suffix)가 전달되어 발생하는 TypeError.
2. 에이전트의 도구 호출 시 발생하는 StructuredOutputValidationError. StructuredRequestBatch 스키마 내부의 members 필드가 유효한 리스트 타입(list_type)이 아닌 NoneType으로 반환되면서 Pydantic 데이터 검증을 통과하지 못하는 타입 불일치 문제였다.

- 해결 방법 :
  클로드 코드에게 질문하였고, LangChain의 구조화된 출력 강제 방식을 분석하고 코드를 수정했다.
  langchain.agents.structured_output에서 ToolStrategy를 임포트하고, 에이전트의 응답 형식을 response_format=ToolStrategy(StructuredRequestBatch)로 명시적으로 지정하여 해결했다.
  이를 통해 LLM이 도구 스키마에 정의된 엄격한 타입(List)을 정확히 준수하여 데이터를 생성하도록 강제함으로써 파싱 오류를 말끔히 해결할 수 있었다.

---

## 과제 회고 (KPT)

- **Keep** (좋았고 계속 유지할 점) : 스키마나 함수를 직접 손으로 작성하기 위해 노력했다.
- **Problem** (아쉬웠거나 막혔던 점) : 1차 PR까지 시간이 조금 부족해서 도전 미션을 뒤늦게 구현했다.
- **Try** (다음에 시도해볼 점) : 라이브 강의 더 열심히 듣고 멘토님 피드백 수정 잘 기록하기
