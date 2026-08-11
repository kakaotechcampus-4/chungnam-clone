# 작업 목표

`student_parts/week06_kanamate_decides_schedule.py`의 메인과제 세로 슬라이스를 완성한다. Week 1-5는
하나의 agent가 개인 일정/RAG/외부 멤버 일정까지 전부 처리했지만, Week 6은 그 구조를 **supervisor +
Nana/Kana 하위 agent**로 나눈다.

- **supervisor**: 직접 업무를 처리하지 않고, 요청을 읽어 `nana_agent`(개인 일정/저장/RAG)와
  `kana_agent`(외부 멤버 일정/공통 시간 결정) 중 하나로 위임만 한다. supervisor가 볼 수 있는 tool은
  이 두 개뿐이다.
- **Nana 하위 agent**: 개인 일정 생성/조회/수정/삭제, todo/reminder 저장, 개인 참고자료·앱 대화
  RAG를 담당한다(Week 4 tool 그대로 재사용).
- **Kana 하위 agent**: 외부 멤버 일정 조회, 공유 일정 row 조회, 공통 가능 시간 후보 검증과 최종 시간
  결정을 담당한다(Week 2/5 tool + 이 파일의 `find_common_available_slots`/`decide_final_slot`).

메인과제는 **prompt 4개 + 위임 wrapper tool 2개**이고, 추가 과제는 Kana의
`find_common_available_slots`/`decide_final_slot`이다. 추가 과제를 구현하지 않기로 했다면
`kana_tools()`와 Kana prompt에서 두 tool을 모두 빼고 위임 흐름만 검증한다(아래 "추가 과제" 절 참고).

# 수정 범위

수정 대상 파일은 `student_parts/week06_kanamate_decides_schedule.py` 하나이며, 이번 문서가 다루는
구현 대상은 아래뿐이다.

**메인과제**
- `week06_prompt_parts()`
- `nana_prompt_parts()`
- `kana_prompt_parts()`
- `supervisor_system_prompt()`의 `# TODO` 부분
- `nana_agent(query)`
- `kana_agent(query)`

**추가 과제** (구현하지 않으면 `kana_tools()`와 Kana prompt에서 관련 tool을 뺀다)
- `FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION`
- `DECIDE_FINAL_SLOT_DESCRIPTION`
- `find_common_available_slots_dict(...)`
- `find_common_available_slots(...)` 본문
- `decide_final_slot(...)` 본문

**이미 구현 완료 상태이므로 건드리지 않는 것**
- `week06_system_prompt()`, `nana_system_prompt()`, `kana_system_prompt()` (prompt 조각을
  `join_system_prompt(...)`로 합치는 부분은 이미 완성돼 있고, 채워야 할 조각(`*_prompt_parts()`)만
  구현 대상이다)
- `_tool_call_names(events)`, `extract_langchain_trace(result)`, `tool_name(tool_object)`
- `FindCommonAvailableSlotsInput`, `DecideFinalSlotInput`, `ProposeGroupScheduleInput`,
  `AgentQueryInput`
- `kana_tools()`, `supervisor_tools()`, `agent_tool_names(agent_name)`
- `propose_group_schedule(...)` — 이전 실습 흐름과의 호환용 helper. 구현은 이미 끝나 있고
  `kana_tools()`에도 들어가지 않는다. 지금 핵심 경로는 `decide_final_slot`이다.
- `build_langchain_supervisor_agent()`, `build_week_agent()`

**Week 1-5 재사용, 다시 작성하지 않음**
- `student_parts/week01_wake_up_nana.py`의 `join_system_prompt`
- `student_parts/week02_structure_natural_language_requests.py`의 `extract_schedule_request`
- `student_parts/week04_retrieve_nanas_memory.py`의 `week04_prompt_parts()`, `week04_tools()`
- `student_parts/week05_load_kanas_past_conversations.py`의 `week05_prompt_parts()`,
  `search_previous_conversations`, `load_conversation_messages`, `extract_schedules_from_history`,
  `list_shared_schedules`, `collect_member_schedules`
- `fixed/schedule_decision.py`의 `find_common_available_slots_payload()`,
  `decide_final_slot_payload()`, `normalize_date_bound()`, `CommonSlotCandidate`
- `fixed/langchain_trace.py`의 `extract_agent_events()`, `extract_final_text()`
- `fixed/external_people_store.py`의 `normalize_external_member_names`

# 1. prompt 함수 4개 (역할 분담을 여기서 직접 정의한다)

위임이 엉뚱한 agent로 가면 **tool이 아니라 이 판단 기준(prompt)을 먼저 고친다.** 하위 에이전트는
supervisor prompt를 공유하지 않으므로 각자 필요한 지시를 스스로 갖고 있어야 한다.

## week06_prompt_parts() -> list[str]

- `week05_prompt_parts()`를 누적한 뒤, supervisor 전용 조각을 추가한다.
- supervisor는 직접 업무를 처리하지 않고 `nana_agent` 또는 `kana_agent`로만 위임한다는 원칙을
  명시한다.
- 어떤 요청이 Nana 담당이고 어떤 요청이 Kana 담당인지 판단 기준을 적는다.
  - Nana: 개인 일정 생성/조회/수정/삭제, todo/reminder 저장, 개인 참고자료·앱 대화 RAG.
  - Kana: 외부 멤버 일정 조회, 공유 일정 row 조회, 공통 가능 시간 후보 검증과 최종 시간 결정(그룹
    조율).

## nana_prompt_parts() -> list[str]

- `week04_prompt_parts()`를 누적한 뒤, Nana 전용 조각을 추가한다.
- 개인 일정/저장/RAG를 담당한다는 점을 명시한다.
- 그룹 조율 요청이 들어오면 담당이 아니라고 짧게 알리게 한다.

## kana_prompt_parts() -> list[str]

- 다른 주차 prompt를 누적하지 않고 처음부터 Kana 역할을 작성한다.
- 외부 멤버 일정/공통 가능 시간/그룹 조율을 담당한다는 점을 명시한다.
- 확정된 일정 저장은 Nana 담당이라고 답하게 한다(Kana는 저장하지 않는다).
- 추가 과제(`find_common_available_slots`, `decide_final_slot`)를 구현했다면, 공통 가능 시간을 찾은
  뒤 `decide_final_slot`까지 이어서 호출하도록 지시를 추가한다. 아직 구현하지 않았다면 두 tool 언급을
  Kana prompt에서 모두 빼고 위임 흐름만 검증한다.

## supervisor_system_prompt() -> str (`# TODO` 부분)

- `week06_prompt_parts()` 누적 조각 뒤에, supervisor 실행 역할에 필요한 최종 지시를 추가한다.
- 반드시 `nana_agent` 또는 `kana_agent` 중 하나를 호출한 뒤 그 결과만 근거로 답하도록 명시한다
  (supervisor가 스스로 답을 지어내지 않게 하는 것이 핵심).

# 2. nana_agent(query) -> str (`@tool`)

- 모듈 전역 `_NANA_SUBAGENT`가 `None`일 때만
  `create_agent(model=chat_model(), tools=week04_tools(), system_prompt=nana_system_prompt())`로
  Nana 하위 agent를 만들고, 이후 호출에서는 만들어 둔 것을 재사용한다.
- `query`를 user 메시지로 하위 agent에 `invoke`한다.
- 결과에서 `extract_agent_events(result)`로 trace를, `extract_final_text(result)`로 answer를 뽑는다.
- `inner_tool_names`는 trace 중 `tool_call` 이벤트의 `tool_name`만 모은 목록이다(`_tool_call_names`
  헬퍼를 그대로 재사용해도 된다).
- 개인 일정 조회/생성/수정/삭제 판단은 이 함수가 아니라 하위 agent가 prompt와 tool description을
  근거로 스스로 수행한다 — 이 함수는 판단하지 않고 위임/결과 정리만 한다.
- `selected_agent`(`"nana_agent"`), `answer`, `trace`, `inner_tool_names`를 담은 JSON 문자열을
  반환한다.

# 3. kana_agent(query) -> str (`@tool`)

- 모듈 전역 `_KANA_SUBAGENT`가 `None`일 때만 `kana_tools()`와 `kana_system_prompt()`로 Kana 하위
  agent를 만들고, 이후 호출에서는 재사용한다.
- `query`를 user 메시지로 하위 agent에 `invoke`하고, `nana_agent`와 동일하게 trace/answer/
  `inner_tool_names`를 뽑는다.
- 하위 trace event를 훑어 `final_slot`이 들어 있는 dict를 찾아 `final_slot_payload`로, `final_decision`
  값이 있으면 `final_decision_payload`로 끌어올린다.
  - `decide_final_slot`(추가 과제)의 반환 payload는 top-level에 `final_slot`/`reason`/`candidates`를
    포함하므로 `final_slot_payload`는 정상적으로 채워진다.
  - `final_decision` 키는 `propose_group_schedule`이 반환하는 형태이며 `propose_group_schedule`은
    `kana_tools()`에 들어가지 않으므로, 이 경로에서는 `final_decision_payload`가 보통 `None`으로
    남는다. 호환을 위한 훅이므로 굳이 채우려고 `decide_final_slot`에 `final_decision` 키를 추가하지
    않는다.
- `answer`, `trace`, `inner_tool_names`, `final_slot_payload`, `final_decision_payload`를 JSON으로
  반환한다.

# 추가 과제 (find_common_available_slots / decide_final_slot)

구현하지 않기로 했다면 `kana_tools()` 목록과 Kana prompt에서 두 tool을 모두 빼고, 아래는 건너뛴다.

## FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION / DECIDE_FINAL_SLOT_DESCRIPTION

- 두 tool 모두 Python이 후보/최종 시간을 자동으로 계산하지 않는다는 점을 agent에게 분명히 알린다.
  agent가 `busy_rows`를 읽고 `candidate_slots`/`final_slot`을 직접 골라 argument로 넘기게 만드는 것이
  핵심이다. Python 구현과 description이 다른 계약을 말하면 agent가 잘못된 argument를 넘기므로, 실제
  argument 형태(`FindCommonAvailableSlotsInput`/`DecideFinalSlotInput`)와 일치하는 내용으로 쓴다.
- `FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION`: `candidate_slots` 각 항목이 `date`/`start_time`/
  `end_time`/`duration_minutes`/`reason`을 포함해야 하고 `busy_rows`와 겹치면 안 된다는 형식을 명시하며,
  이 결과로 답변을 끝내지 말고 `decide_final_slot`을 이어서 호출하도록 유도한다.
- `DECIDE_FINAL_SLOT_DESCRIPTION`: `final_slot` 형식(`'YYYY-MM-DD HH:MM-HH:MM'`)과
  `needs_agent_selection`/`reason`을 채우는 기준을 명시하고, 아직 고르지 않았다면 `final_slot=null`,
  `needs_agent_selection=true`로 두게 한다.

## find_common_available_slots_dict(...) -> dict[str, Any]

- `normalize_external_member_names(...)`로 멤버 이름을, `normalize_date_bound(...)`로 `date_from`/
  `date_to`를 정규화한다(ISO datetime이 들어오면 날짜 부분만 사용).
- `busy_rows`가 `None`이면 `collect_member_schedules.invoke({...})`를 호출해 내 일정과 외부 멤버
  busy-time을 모은다. 이때 `member_names`에는 "나"를 함께 포함해 내 일정도 근거로 남긴다.
- 실제 후보 검증 payload 생성은 `fixed/schedule_decision.py`의
  `find_common_available_slots_payload(...)`에 위임한다.

## find_common_available_slots(...) -> str (`@tool`)

- `find_common_available_slots_dict(...)` 결과를 JSON 문자열로 반환한다.

## decide_final_slot(...) -> str (`@tool`)

- 직접 최종 시간을 고르지 않는다. 받은 인자(`final_slot`, `selected_index`, `needs_agent_selection`,
  `reason` 등)를 그대로 `decide_final_slot_payload(...)`에 넘긴다.
- 반환 JSON은 course repo 계약에 따라 top-level `final_slot`/`reason`/`candidates`를 반드시 포함한다.
- 후보 판단을 수행한 경우 `members`/`busy_rows`/`candidate_slots`도 함께 남겨 근거를 확인할 수 있게
  한다.
- `selected_index`나 `selected_slot`이 없으면 `final_slot`을 자동으로 고르지 말고
  `needs_agent_selection=True` 상태를 유지한다(이 로직은 이미 `decide_final_slot_payload` 안에 있으므로
  이 tool은 인자를 그대로 전달하기만 하면 된다).

# 검증 방법

- **메인과제**: `./run.sh --week6`을 실행한다.
  - supervisor trace에서 `nana_agent`/`kana_agent` 중 무엇이 선택됐는지 확인한다.
  - 개인 일정 조회를 요청해 Nana 하위 agent trace에 `personal_list_saved_schedules` 호출이 남는지
    확인한다.
  - 위임이 엉뚱한 agent로 가면 tool이 아니라 prompt의 판단 기준부터 고친다.
  - 추가 과제를 아직 구현하지 않았다면 `kana_tools()`에서 `find_common_available_slots`/
    `decide_final_slot`을 빼고 Kana prompt에서도 두 tool 언급을 지운 뒤 위임 흐름만 확인한다.
- **추가 과제**: 그룹 일정 요청에서 하위 trace에 `search_previous_conversations`,
  `extract_schedules_from_history` 또는 `collect_member_schedules`, `find_common_available_slots`,
  `decide_final_slot`이 이어서 호출되고, `final_slot_payload`가 최종 답변과 일치하는지 확인한다.

# 참고자료

- `fixed/schedule_decision.py`: `CommonSlotCandidate`, `normalize_date_bound`,
  `find_common_available_slots_payload`, `decide_final_slot_payload`의 실제 구현. agent가 넘긴
  `candidate_slots`를 검증/정규화하는 로직(`normalize_llm_candidate_slots`)이 여기 있다.
- `fixed/langchain_trace.py`: `extract_agent_events`, `extract_final_text`, `extract_langchain_trace`
  (Week 1-5 공통 버전). 이 파일의 `extract_langchain_trace`(supervisor용, 이미 구현 완료)와 이름은
  같지만 역할이 다르다 — `fixed/langchain_trace.py` 쪽은 단일 agent 결과를 다루고, 이 week06 파일의
  버전은 supervisor 결과에서 `nana_agent`/`kana_agent` 선택 여부까지 뽑아낸다.
  - **오탈자/중복 표기 주의**: 참고 요구사항 원문 중 "그룹 조율 요청은 그룹 조율 요청은 담당이
    아니라고 짧게 알리게 합니다"처럼 문구가 중복 표기된 부분이 있었다. 실제 prompt에는 한 번만 쓴다
    (위 `nana_prompt_parts()` 절 참고).
- `student_parts/week04_retrieve_nanas_memory.py`: `week04_prompt_parts()`, `week04_tools()`.
- `student_parts/week05_load_kanas_past_conversations.py`: `week05_prompt_parts()`, Kana가 재사용하는
  wrapper tool들.
- `student_parts/week02_structure_natural_language_requests.py`: `extract_schedule_request`.
