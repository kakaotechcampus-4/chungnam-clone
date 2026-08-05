## 과제 목표

- Week 6은 "모든 기능을 한 agent가 직접 처리"하지 않고 supervisor가 Nana/Kana 하위 agent로 위임하게 만듭니다.
- Nana는 개인 일정/저장/RAG를 맡고, Kana는 외부 대화/멤버 일정/그룹 시간 결정을 맡습니다.

---

## 과제 위치

- 작업 브랜치 : `parkjeonghyeon/week6` → 본인 통합 브랜치 `parkjeonghyeon/final` 로 PR
- 주요 파일 : `student_parts\week06_kanamate_decides_schedule.py`

---

## 과제 범위

이번 PR 에서 어디까지 했는지 체크해요. (해당하는 곳에 모두)

- [x] 메인 과제 완료
- [x] 추가 과제 완료

---

## 구현한 기능

- [x] week06_prompt_parts / nana_prompt_parts / kana_prompt_parts / supervisor_system_prompt 프롬프트 부분 작성하기
- [x] nana_agent 구현하기
- [x] kana_agent 구현하기

---

## 도전 기능

- [x] FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION / DECIDE_FINAL_SLOT_DESCRIPTION 작성하기
- [x] find_common_available_slots_dict / find_common_available_slots / decide_final_slot 구현하기

---

### week06_prompt_parts / nana_prompt_parts / kana_prompt_parts / supervisor_system_prompt 프롬프트 부분 작성하기

- AI 활용 내용 :

```
일단 수강생구현가이드 주석의 메인과제 구현대상 1 프롬프트 부분을
어떻게 구현할지 계획을 세워보자
```

위의 프롬프트로 join_system_prompt 헤더에 "더 높은 주차 또는 더 뒤에 있는 지시를 우선한다"가 적혀 있어서 앞 주차 지시를 지우는 대신 뒤에서 덮어써야 한다는 것을 확인하였다. 누적된 Week 1~5 조각이 personal_create_schedule 이나 collect_member_schedules 같은 업무 tool 을 지시하는데 supervisor 에게 붙는 tool 은 nana_agent / kana_agent 두 개뿐이라는 것, week05_prompt_parts 가 최종 확정을 다음 주차로 미뤄 둔 상태라는 것, kana_prompt_parts 만 누적이 없다는 것을 확인한 뒤 네 함수의 역할을 나누는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : week06_prompt_parts 에는 호출 가능한 tool 이 nana_agent / kana_agent 두 개뿐이라는 것, "누구의 정보가 필요한가" 기준의 담당 분리, 두 담당에 걸치면 Kana 로 확정한 뒤 Nana 로 저장하는 순서와 query 에 멤버·날짜·회의 길이를 다 적을 것, 최종 확정이 이번 주차라는 갱신을 조각별로 넣었다. nana_prompt_parts 에는 하위 agent 전환과 그룹 조율은 Kana 담당이라는 경계를 넣고, kana_prompt_parts 는 누적이 없으므로 역할과 current_app_date_iso() 오늘 날짜와 tool 선택 기준과 저장은 Nana 담당이라는 경계를 처음부터 적었다. supervisor_system_prompt 에는 위임 없이 답하지 말 것과 위임 결과의 answer / final_slot_payload 만 근거로 쓸 것을 붙였다.
- 수정 이유 : 누적 구조라서 덮어쓰기를 명시하지 않으면 supervisor 가 자기에게 없는 업무 tool 을 부르려 하고, Week 5의 "확정은 다음 주차" 문장을 남겨 두면 후보만 나열하고 끝나기 때문이다. Kana 는 누적이 없어 오늘 날짜조차 없는 상태로 시작해 상대 날짜를 그대로 tool 인자에 넣으므로 current_app_date_iso() 를 직접 넣었다. 실제로 개인 일정 요청에는 nana_agent 만, 외부 멤버 조회에는 kana_agent 만 호출되는 것을 확인했다. 다만 조회 0건일 때 답변이 "일정이 없습니다"로 단정해서, 조회 조건을 지우고 단정하지 말라는 문장을 한 줄 더 넣고 재실행해 조회 범위가 함께 나오는 것을 확인했다.

### nana_agent 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

fixed/langchain_trace.py 의 extract_agent_events 가 ToolMessage content 를 json.loads 로 미리 파싱해 dict 로 돌려주고 extract_final_text 는 마지막 비어 있지 않은 메시지를 답변으로 쓴다는 것을 확인하였다. 또 create_agent 는 호출마다 tool 을 다시 바인딩하므로 supervisor 가 부를 때마다 하위 agent 를 새로 만들 필요가 없다는 것과, 하위 agent 는 supervisor 의 messages 를 이어받지 않고 query 하나만 새 대화로 받는다는 것을 확인한 뒤 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : \_NANA_SUBAGENT 가 None 일 때만 create_agent(model=chat_model(), tools=week04_tools(), system_prompt=nana_system_prompt()) 로 만들고 이후에는 재사용하게 했다. query 를 user 메시지 하나로 invoke 한 뒤 extract_agent_events 로 events 를, extract_final_text 로 answer 를 뽑고, 이미 이 파일에 있던 \_tool_call_names(events) 로 tool_call 이벤트의 이름만 골라 inner_tool_names 에 담았다. selected_agent / answer / trace / inner_tool_names 를 json.dumps(..., ensure_ascii=False) 로 반환했다.
- 수정 이유 : 전역 캐시를 안 두면 supervisor 호출마다 하위 agent 를 다시 만들어 tool 바인딩이 반복된다. inner_tool_names 를 따로 올린 것은 extract_langchain_trace 가 위임 결과 content 에서 이 키를 읽어 events 밖으로 모으는 구조라서 supervisor trace 가 하위 tool 호출 순서를 바로 볼 수 있게 하기 위해서다. ensure_ascii=False 는 한글이 escape 되어 supervisor 가 읽는 근거가 깨지지 않게 하기 위해서다. 실제로 개인 일정 요청에서 inner_tool_names 에 personal_list_saved_schedules 가 남는 것을 확인했다.

### kana_agent 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

fixed/schedule_decision.py 의 decide_final_slot_payload 가 final_slot 을 top-level 에 두고 미확정일 때도 None 으로 키를 남긴다는 것과, propose_group_schedule 은 결과를 final_decision 아래에 넣는다는 것을 확인하였다. 또 이 파일의 extract_langchain_trace 가 위임 결과 content 에서 final_slot_payload 키를 먼저 찾는다는 것을 확인해, 하위 trace 의 payload 를 한 단계 끌어올려야 supervisor 가 최종 시간을 읽을 수 있다는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : \_KANA_SUBAGENT 를 kana_tools() 와 kana_system_prompt() 로 한 번만 만들어 nana_agent 와 같은 방식으로 실행했다. events 를 순회하며 content 가 dict 이고 final_slot 키를 가진 것은 final_slot_payload 로, final_decision 값을 가진 것은 final_decision_payload 로 담았고, 같은 요청에서 여러 번 결정하면 마지막 호출이 최종이므로 덮어쓰게 했다. 여기에 answer / trace / inner_tool_names 를 더해 JSON 으로 반환했다.
- 수정 이유 : final_slot 을 값이 아니라 키 유무로 판별한 것이 핵심이다. content.get("final_slot") 으로 보면 미확정 payload 의 None 이 걸러져 needs_agent_selection 이 True 인 상태가 supervisor 까지 올라가지 못하고 시간이 확정된 것처럼 답할 수 있다. payload 를 끌어올리지 않으면 supervisor 가 하위 trace 를 다시 뒤져야 하는데 extract_langchain_trace 는 그런 구조가 아니다. 실제로 그룹 일정 요청에서 final_slot_payload 의 final_slot 이 최종 답변과 일치하는 것을 확인했다.

### FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION / DECIDE_FINAL_SLOT_DESCRIPTION 작성하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

fixed/schedule_decision.py 의 normalize_llm_candidate_slots 가 날짜 범위 밖, 업무 시간 밖, duration 미달, busy_rows 와 겹치는 후보를 예외 없이 조용히 버린다는 것과, FindCommonAvailableSlotsInput / DecideFinalSlotInput 에 인자 이름과 형식이 이미 정의되어 있다는 것을 확인하였다. 두 tool 이 후보나 최종 시간을 계산해 주지 않으므로 description 이 agent 가 직접 고르게 만드는 유일한 근거라는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : 두 상수 모두 첫 문장에 이 tool 이 대신 계산해 주지 않는다는 것을 적었다. FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION 에는 collect_member_schedules 로 rows 를 먼저 모아 직접 읽고 후보를 고를 것, candidate_slots 항목이 date / start_time / end_time / duration_minutes / reason 을 포함할 것, busy_rows 를 앞 결과에서 복사할 것, 조건을 벗어난 후보는 조용히 빠지므로 결과가 비면 다시 고를 것, 여기서 끝내지 말고 decide_final_slot 을 이어 호출할 것을 적었다. DECIDE_FINAL_SLOT_DESCRIPTION 에는 selected_index 또는 selected_slot 으로 후보를 지정하고 final_slot 을 'YYYY-MM-DD HH:MM-HH:MM' 형식으로 적을 것, 못 고르면 final_slot 을 null 로 두고 needs_agent_selection 을 true 로 둘 것을 적었다.
- 수정 이유 : description 과 Python 구현이 다른 계약을 말하면 agent 가 빈 candidate_slots 로 호출해 놓고 결과를 기다린다. 조건 위반 후보가 예외 없이 버려지는 것도 적지 않으면 agent 가 빈 결과를 "가능한 시간이 없다"로 오해한다. 미확정 규칙을 명시한 것은 근거가 없을 때 아무 시간이나 채워 넣는 것을 막기 위해서다. 실제로 후보 5개 중 겹침 / 업무 시간 밖 / 범위 밖 / 길이 미달 4개가 걸러지고 1개만 남는 것을 확인했다.

### find_common_available_slots_dict / find_common_available_slots / decide_final_slot 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

collect_member_schedules 가 rows 에 "나"의 일정까지 합쳐 준다는 것과 PERSONAL_SHARED_MEMBER_NAME 이 "나"라는 것, decide_final_slot_payload 가 final_slot 이 없으면 needs_agent_selection 을 알아서 True 로 둔다는 것을 확인하였다. 또 busy_rows 가 None 인 것과 빈 list 인 것의 의미가 다르다는 것을 확인한 뒤, dict 헬퍼가 정규화와 수집만 맡고 검증은 fixed/schedule_decision.py 에 넘기는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : find_common_available_slots_dict 에서 normalize_external_member_names 로 이름을, normalize_date_bound 로 날짜를 정규화하고, busy_rows 가 None 일 때만 collect_member_schedules.invoke 로 rows 를 채웠다. 검증 대상 명단에는 PERSONAL_SHARED_MEMBER_NAME 을 앞에 붙여 find_common_available_slots_payload 로 넘겼다. 두 tool 함수는 각각 dict 헬퍼 결과와 decide_final_slot_payload 결과를 json.dumps(..., ensure_ascii=False) 로 반환하기만 했고, 후보나 최종 시간을 고르는 로직은 넣지 않았다.
- 수정 이유 : busy_rows 를 is None 으로 검사한 것은 None 이 "아직 안 모았다"이고 빈 list 는 "모았는데 없다"라서, or [] 로 처리하면 확정된 0건을 20초 넘는 MCP 호출로 다시 조회하기 때문이다. members 에 "나"를 넣은 것은 rows 에 내 일정이 들어 있으니 명단에도 남아야 근거가 맞기 때문이다. tool 안에서 최종 시간을 고르지 않은 것은 description 과 계약을 맞추기 위해서고, 실제로 selected_index 없이 호출하면 final_slot 이 None 이고 needs_agent_selection 이 True 로 유지되는 것을 확인했다.

---

## 구현하면서 고민한 점

- 고민한 점 : Week 6은 코드가 아니라 프롬프트가 동작을 좌우하는 주차라서, 잘못 만들어도 에러가 아니라 그럴듯한 답변으로 나타나는 것이 가장 어려웠다. 위임이 엉뚱한 하위 agent로 가도, 미확정인 시간을 확정된 것처럼 말해도, 조회 0건을 "일정이 없다"고 단정해도 실행 자체는 정상으로 끝난다. 그래서 클로드 코드에게 "무엇을 확인해야 이 구현이 검증됐다고 할 수 있는지"부터 물어보고, 확인할 항목을 먼저 정리한 다음에 테스트를 짰다.
- 해결방법 : 검증을 두 단계로 나눠서 진행했다.
  1단계는 LLM을 거치지 않고 계약만 확인하는 것이었다. 하위 agent 자리에 미리 만든 trace를 돌려주는 stub을 넣어, kana_agent가 decide_final_slot 결과를 final_slot_payload로 끌어올리고 그것이 supervisor 쪽 extract_langchain_trace까지 그대로 읽히는지 확인했다. 도전 기능은 busy_rows를 직접 주입해 후보 5건 중 겹침·업무 시간 밖·범위 밖·길이 미달 4건이 걸러지고 1건만 남는 것과, selected_index 없이 부르면 final_slot이 None이고 needs_agent_selection이 True로 유지되는 것을 확인했다.
  2단계는 실제 supervisor를 실행해 trace를 보는 것이었다. 개인 일정 요청에는 nana_agent만 호출되고 하위 trace에 personal_list_saved_schedules가, 외부 멤버 조회에는 kana_agent만 호출되고 collect_member_schedules가 남았다. 날짜를 명시한 그룹 일정 요청에서는 collect_member_schedules 다음 find_common_available_slots 다음 decide_final_slot이 이어지고, final_slot_payload의 시간이 최종 답변과 일치했다.
  이 과정에서 프롬프트 문제를 하나 잡았다. 조회 0건일 때 Kana는 조회 조건을 밝혔는데 supervisor가 요약하면서 "일정이 없습니다"로 단정했다. tool 구현이 아니라 supervisor 프롬프트에 조회 조건을 지우고 단정하지 말라는 문장을 한 줄 넣고 재실행하니 날짜 범위가 함께 나왔다. 실습 데이터가 2026-07-07~17인데 앱 기준 오늘은 2026-08-05라 상대 날짜로 물으면 전부 0건이 나오는 것도 확인해, 테스트 질문에는 항상 날짜를 명시했다.

---

## 과제 회고 (KPT)

- **Keep** (좋았고 계속 유지할 점) : 이번에도 거의 AI-first로 문제해결을 맡기고, 직접 판단하는 시간을 가졌는데 좀 더 빠르게 과제를 할 수 있었던 것 같다.
- **Problem** (아쉬웠거나 막혔던 점) : 멀티 에이전트를 활용하는 부분에 대해서 좀 더 깊은 이해가 필요활 것 같다.
- **Try** (다음에 시도해볼 점) : 멀티 에이전트 추가로 공부해보기
