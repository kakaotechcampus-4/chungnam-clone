## 과제 목표

- Week 2에서 만든 StructuredRequest를 Pydantic 입력 스키마로 검증한 뒤 SQLite에 저장하기
- 저장된 요청/일정을 다시 조회/수정/삭제하기

---

## 과제 위치

- 작업 브랜치 : `parkjeonghyeon/week3` → 본인 통합 브랜치 `parkjeonghyeon/final` 로 PR
- 주요 파일 : `student_parts/week03_build_nanas_logbook.py`

---

## 과제 범위

이번 PR 에서 어디까지 했는지 체크해요. (해당하는 곳에 모두)

- [x] 메인 과제 완료
- [x] 심화 과제까지 완료

---

## 구현한 기능

- [x] save_structured_request() 함수 구현하기
- [x] list_saved_requests(), get_saved_request() 함수 구현하기
- [x] personal_list_saved_schedules() 함수 구현하기
- [x] build_week03_agent(), week03_prompt_parts() 함수 및 Week 3 프롬프트 구현하기

---

## 도전 기능

- [x] structured_request_from_week01_schedule(), personal_create_schedule() 함수 구현하기
- [x] \_delete_saved_schedules(), delete_saved_schedules_dict(), personal_delete_saved_schedules() 함수 구현하기 (B. 삭제)
- [x] personal_update_saved_schedule() 함수 구현하기 (C. 수정)
- [x] unwrap_legacy_payload(), \_save_input_from(), save_structured_request_payload() 함수 구현하기 (D. 레거시 정규화)

---

### save_structured_request() 함수 구현하기

- AI 활용 내용 : 따로 AI를 활용하지 않았다.
- 직접 수정한 부분 : args_schema로 검증된 함수의 인자들을 그대로 payload dict로 모아서 저장했다. 이전 주차처럼members or []로 members가 항상 리스트가 되게 했고, 이어서 dict comprehension {k: v for k, v in payload.items() if v is not None}으로 None 값을 제외한 뒤 \_store().save_structured_request(payload)로 저장했으며, 그 결과를 tool_result로 감싸서 JSON 문자열로 반환했다.
- 수정 이유 : 노트북처럼 본문에서 Pydantic 클래스를 다시 만들면 args_schema가 이미 한 검증을 중복하게 되므로, 굳이 그렇게 하지 않고 이미 검증된 인자를 바로 딕셔너리로 정리했다. None 값을 빼는 것은 store가 payload.get으로 읽어 빈 값을 굳이 넣을 필요가 없고, DB에 불필요한 null 값이 남지 않게 하기 위해서다.

### list_saved_requests(), get_saved_request() 함수 구현하기

- AI 활용 내용 :

```
list_saved_requests는 SQLite 데이터를 kind, date_from, date_to로 필터링해서 가져오니까,
이거를 쿼리문을 직접 써야 하는지 알려 줘. 단건 조회인 get_saved_request도 한번 같이 봐 줘.
```

위의 프롬프트로 실제 SQL은 이미 AppSQLiteStore 안에 들어 있고, tool은 필터를 store 메서드에 그대로 넘기는 얇은 입구 역할만 하면 된다는 것을 확인한 뒤, 설계 방향을 간단히 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : list_saved_requests는 받은 kind, date_from, date_to를 별도 가공 없이 \_store().list_saved_requests에 그대로 넘겨서 결과를 rows로 감쌌다. get_saved_request는 request_id로 \_store().get_saved_request를 호출하고, 결과가 없을 때에도 row=None을 그대로 유지해 반환했다.
- 수정 이유 : SQL 쿼리문 역할은 store 함수가 담당하므로 tool에서 쿼리를 직접 쓸 필요가 없고, 필터가 None이면 store 쪽에서 알아서 조건에서 빠지기 때문이다. 또 조회 결과가 없어도 예외를 던지지 않고 rows=[] 또는 row=None을 유지해야 이후 흐름이 안전하게 이어지기 때문이다.

### personal_list_saved_schedules() 함수 구현하기

- AI 활용 내용 :

```
저장된 일정 목록을 조회하는 personal_list_saved_schedules를 구현하고 싶어. rows로 조회하는 부분까지는 했는데 그 뒤를 어떻게 해야 할지 알려 줘.
```

위의 프롬프트로 store의 메서드 이름이 list_saved_schedules가 아니라 list_schedules라는 점(오류)과, 반환에 filters와 schedules를 함께 담아야 한다는 점을 확인한 뒤 직접 구현하였다.

- 직접 수정한 부분 : 잘못 호출하던 list_saved_schedules를 store에 실제로 있는 list_schedules로 고쳤다. 기본 kind를 kind or "personal_schedule"로 계산해 kind_tmp 변수에 담고, 이 값을 store 호출과 filters dict에 똑같이 사용했다. 반환은 rows 대신, 조회 조건을 담은 filters와 결과 목록인 schedules를 함께 넣어 JSON 문자열로 만들었다.
- 수정 이유 : store에 없는 메서드를 부르면 실행 시 AttributeError로 프로그램이 죽기 때문에 이름을 정확히 맞췄다. kind 기본값을 변수 하나로 계산해 재사용한 것은 store 인자와 filters에 서로 다른 값이 들어가 어긋나는 것을 막기 위해서다. filters를 함께 반환하는 것은 trace에서 "어떤 조건으로 무엇을 조회했는지" 확인할 수 있게 하기 위해서다.

### build_week03_agent(), week03_prompt_parts() 함수 및 Week 3 프롬프트 구현하기

- AI 활용 내용 :

```
2주차에 만든 build_week02_agent와 week02_prompt_parts를 가져와서 3주차용 함수인 build_week03_agent, week03_prompt_parts를 작성해 줘.
단, week02를 고려해서 3주차에 빠져야 하는 내용(structured output용 response_format, "SQLite 저장은 이 agent 역할이 아니다"라는 지시)은 빼고,
현재 3주차의 SQLite 저장/조회 흐름에 맞게 반영해 줘.
```

위의 프롬프트를 활용하여 조금 빠르게 구현하였다.

- 직접 수정한 부분 : build_week03_agent()에서는 week02처럼 create_agent를 쓰되 structured output용 response_format 인자는 빼고 model=chat_model(), tools=week03_tools(), system_prompt=week03_system_prompt()만 연결했다. 비어 있던 SQLITE_MEMORY_PROMPT와 WEEK03_TOOL_CALL_PROMPT 상수에는 각각 "저장 내용은 DB에서 도구로 조회하라"는 규칙과 "extract_schedule_request -> save_structured_request 순서로 저장하라"는 규칙을 직접 작성했고, 특히 "이번 주차에서는 SQLite 저장이 이 agent의 역할"이라는 문구를 명시적으로 추가했다. 또 week03_prompt_parts()의 TODO 자리에는 "구조화로 끝내지 말고 저장까지 수행"하라는 지시와 함께 current_app_date_iso() 값을 f-string으로 삽입한 오늘 날짜, 주차 범위 안내를 넣었다.
- 수정 이유 : week03은 week02처럼 structured output을 반환하는 agent가 아니라 tool 호출로 SQLite에 저장/조회하는 agent이므로 response_format이 불필요하기 때문이다. 또한 week03_prompt_parts()가 week02_prompt_parts()를 상속하는데 2주차 프롬프트에 "SQLite 저장은 이 agent 역할이 아니다"라는 문구가 있어 그대로 두면 LLM이 저장을 하지 않으므로, 이번 주차에서는 저장이 agent의 역할임을 명시해 상속된 지시를 뒤집고 오직 구조화 후 저장·조회하는 역할만을 명확히 알리기 위해서다.

### structured_request_from_week01_schedule(), personal_create_schedule() 함수 구현하기

- AI 활용 내용 :

```
structured_request_from_week01_schedule(), personal_create_schedule() 함수에서,
Week 1과 호환되도록 저장 로직을 어떻게 구현해야 하는지 알려 줘.
week01_wake_up_nana.py 파일도 참고해서, Week 1의 personal_create_schedule 결과를 structured_request_from_week01_schedule로 변환한 뒤 SQLite에도 저장하는 흐름을 설명해 줘.
```

위의 프롬프트로 Week 1 tool의 반환 구조(created_schedule dict)와 필드 매핑(attendees을 members로, id를 source_schedule_id로.)을 파악한 뒤 직접 구현하였다.

- 직접 수정한 부분 : structured_request_from_week01_schedule에서는 kind를 "personal_schedule"로 고정하고 attendees를 members로, id를 source_schedule_id로 매핑했다.
- 수정 이유 : kind를 personal_schedule로 두면 members가 있을 때 StructuredRequest의 validator가 자동으로 group_schedule로 승격해 주고, source_schedule_id에 Week 1 임시 id를 넣어야 store가 중복 저장을 막고 같은 일정으로 연결하기 때문이다.

### \_delete_saved_schedules(), delete_saved_schedules_dict(), personal_delete_saved_schedules() 함수 구현하기

- AI 활용 내용 :

```
수강생 구현 가이드 주석을 잘 참고하여,
_delete_saved_schedules(), delete_saved_schedules_dict(), personal_delete_saved_schedules()
저장 일정 삭제 함수 3개를 구현해 줘. 조건 없이 전체가 삭제되지 않게 안전 규칙을 넣고, delete_all이나 명시 필터에 따라 store의 삭제 메서드를 호출하는 구조로 만들어 줘.
```

위의 프롬프트를 활용하여 helper(\_delete_saved_schedules)부터 tool(personal_delete_saved_schedules)까지 아래에서 위 순서로 빠르게 구현하였다.

- 직접 수정한 부분 : 이 부분은 따로 수정하지 않았다. 기능 테스트 시 잘 통과하였다..!

### personal_update_saved_schedule() 함수 구현하기

- AI 활용 내용 :

```
personal_update_saved_schedule() 저장 일정 수정 tool을 구현해 줘. None으로 들어온 필드는 수정하지 않고, ID를 못 찾으면 실패로 응답하고, 성공하면 수정된 일정과 공유 일정 동기화 결과를 함께 반환하게 해 줘.
```

위의 프롬프트를 활용하여 store의 update_schedule을 감싸는 형태로 구현하였다.

- 직접 수정한 부분 : title/date/start_time/end_time/attendees를 그대로 \_store().update_schedule에 넘기고, 결과가 None이면 ok=False와 reason을, 있으면 updated_schedule과 shared_sync를 담아 JSON으로 반환하도록 했다. 구현 뒤 임시 디비로 "시간만 수정 시 나머지 필드 유지"와 "없는 ID -> None" 동작을 확인했다.
- 수정 이유 : store의 update_schedule이 이미 None을 "수정하지 않음"으로 처리하므로 그냥 인자를 그대로 넘기면 되고, 공유 저장소 동기화 결과까지 반환해야 이후 여러 사람 일정 조율에서 '나'의 일정이 일관되게 보이기 때문이다.

### unwrap_legacy_payload(), \_save_input_from(), save_structured_request_payload() 함수 구현하기

- AI 활용 내용 :

```
unwrap_legacy_payload(), _save_input_from(), save_structured_request_payload() 함수 3개의 payload 정규화를 각각 구현해 줘.
dict, json 문자열, 자연어, StructuredRequest 등 어떤 형태로 들어오든간에 SaveStructuredRequestInput 하나로 맞춰 저장할 수 있게 해 줘.
```

위의 프롬프트를 활용하여 unwrap_legacy_payload -> \_save_input_from -> save_structured_request_payload 순서로 구현하였다.

- 직접 수정한 부분 : 해당 부분의 구현을 정확하게 다 이해하지 못하였다ㅜㅜ 따로 수정하지는 않았지만, 추가 기능 테스트는 잘 통과하였다.

---

## 구현하면서 고민한 점

고민한 점 : Week 3 저장·조회 도구를 완성한 뒤 실제로 "저장 -> 조회"가 동작하게 만드는 과정에서 두 가지 문제에 직면했다.

1. 저장 일정 조회 도구(personal_list_saved_schedules)를 구현할 때, 저장소에 존재하지 않는 list_saved_schedules 메서드를 호출하도록 작성하여 실행 시 AttributeError가 발생하는 문제.
   위의 문제는 요청 조회 메서드 이름(list_saved_requests)과 헷갈린 것이었고, 저장소에 실제로 정의된 일정 조회 메서드 이름은 list_schedules였다.
2. 저장/조회 도구를 모두 정상적으로 구현했음에도 불구하고 agent가 실제로는 SQLite에 저장하지 않는 문제.
   이 문제의 원인은 두 가지였는데, build_week03_agent가 create_agent로 agent를 생성하지 않고 계속 None을 반환하던 것과, week03_prompt_parts()가 Week 2 프롬프트를 상속하면서 "SQLite 저장은 이 agent의 역할이 아니다"라는 지시까지 함께 물려받아 LLM이 저장 도구를 호출하지 않도록 지시받고 있던 구조적 충돌이었다.

해결 방법 : 우선적으로 클로드 코드에게 질문하였고, 저장소(fixed/app_store.py)의 실제 메서드 시그니처와 주차별 프롬프트 상속 구조를 분석해 코드를 수정했다.
1번 문제는 존재하지 않던 list_saved_schedules 호출을 저장소에 실제 정의된 list_schedules로 바로잡아 해결했다.
2번은 build_week03_agent에서 create_agent(model, tools, system_prompt)로 agent를 실제 생성하도록 채우되 Week 2와 달리 구조화 출력용 response_format은 제외했고,
week03_prompt_parts()에 "이번 주차에서는 SQLite 저장이 이 agent의 역할"이라는 지시를 추가해 Week 2에서 상속된 "저장하지 말라"는 지시를 명시적으로 뒤집어 해결했다.
마지막으로 SQLite DB를 직접 조회해 일정이 kind=personal_schedule로 structured_requests와 schedules 테이블에 정상 저장·조회되는지 확인함으로써, 저장 흐름이 끝까지 동작하는 것을 검증할 수 있었다.

---

## 과제 회고 (KPT)

- **Keep** (좋았고 계속 유지할 점) : 스키마나 함수를 직접 손으로 작성하기 위해 노력했다.
- **Problem** (아쉬웠거나 막혔던 점) : 추가 과제 부분에서 구현하고 싶다는 욕심에 AI를 너무 많이 사용한 것 같다..
- **Try** (다음에 시도해볼 점) : 멘토님 피드백에 따라서 코드를 더 면밀하게 분석하고 이해하기
