## 과제 목표

- 외부 SQLite/MCP 서버에 있는 Kana의 이전 대화와 공유 일정을 LangChain agent가 사용할 수 있게 감쌉니다.
- MCP tool을 호출하고 그 결과를 agent용 JSON으로 전달하는 wrapper tool을 만듭니다.

---

## 과제 위치

- 작업 브랜치 : `parkjeonghyeon/week5` → 본인 통합 브랜치 `parkjeonghyeon/final` 로 PR
- 주요 파일 : `student_parts/week05_load_kanas_past_conversations.py`

---

## 과제 범위

이번 PR 에서 어디까지 했는지 체크해요. (해당하는 곳에 모두)

- [x] 메인 과제 완료
- [x] 추가 과제 완료

---

## 구현한 기능

- [x] search_previous_conversations() 함수 구현하기
- [x] load_conversation_messages() 함수 구현하기
- [x] extract_schedules_from_history() 함수 구현하기
- [x] list_shared_schedules() 함수 구현하기
- [x] collect_member_schedules() 함수 구현하기

---

## 도전 기능

- [x] create_shared_schedule() / delete_shared_schedule() 함수 구현하기

---

### search_previous_conversations() 함수 구현하기

- AI 활용 내용 :

```
일단 수강생구현가이드 주석의 메인과제 구현대상 1 search_previous_conversations 부분을
어떻게 구현할지 계획을 세워보자
```

위의 프롬프트로 이 파일의 call_mcp_tool_sync 가 fixed/mcp_client.py 의 call_local_mcp_tool_sync 별칭이고, 그 안의 \_mcp_result_to_text 가 MCP 결과를 이미 문자열로 정규화해 준다는 것과, mcp_server/sqlite_mcp_server.py 가 {"ok", "tool_name", "rows"} 형태의 완성된 JSON 문자열을 돌려준다는 것을 확인하였다. 특히 store 의 search_previous_conversations 는 member_names 가 None 이면 멤버 필터 없이 전체를 검색하고 빈 list 면 즉시 빈 rows 를 반환해서 두 값의 의미가 서로 다르다는 것을 확인한 뒤, wrapper 는 인자를 그대로 넘기고 결과 문자열을 그대로 반환한다는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : call_mcp_tool_sync("search_previous_conversations", {"query": query, "member_names": member_names, "limit": limit}) 한 번 호출로 끝내고 반환값을 그대로 return 했다. member_names 는 None 인 채로 넘겼고, Week 4에서 쓰던 safe_limit 같은 보정도 넣지 않았으며, json_payload 로 다시 감싸지도 않았다.
- 수정 이유 : Week 4 습관대로 member_names or [] 를 쓰면 "전체 멤버 검색"이 "빈 결과"로 뒤집히기 때문에 None 을 그대로 통과시켜야 한다. limit 은 args_schema 의 Field(ge=1, le=50) 이 이미 범위를 보장하므로 tool 안에서 또 보정하면 중복이고, call_mcp_tool_sync 반환값이 이미 JSON 문자열이라 json_payload 로 감싸면 이중 인코딩되어 LLM이 escape된 문자열을 받게 되기 때문이다.

### load_conversation_messages() 함수 구현하기

- AI 활용 내용 :

```
다음으로 수강생구현가이드 주석의 메인과제 구현대상 2. load_conversation_messages 부분을
어떻게 구현할지 계획을 세워보자
```

위의 프롬프트로 fixed/external_mcp.py 의 call_external_tool_payload 가 call_local_mcp_tool_sync 에 json.loads 를 붙인 얇은 래퍼라서 dict 를 돌려준다는 것과, store 의 load_conversation_messages 가 ORDER BY created_at ASC 로 이미 시간순 정렬된 role/sender/content/created_at row 를 준다는 것을 확인하였다. 1번 tool과 달리 굳이 dict 를 거치는 이유도, \_mcp_result_to_text 가 content 블록을 여러 개 이어붙이면 유효한 JSON 이 아닐 수 있어 json.loads 로 한 번 검증되기 때문이라는 것을 확인한 뒤, payload 를 통째로 json_payload 로 감싸는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : call_external_tool_payload("load_conversation_messages", {"conversation_id": conversation_id}) 로 payload dict 를 받아 json_payload(payload) 로 그대로 감쌌다. rows 를 다시 정렬하거나 role 을 빼고 필드를 추리는 재조립은 하지 않았다.
- 수정 이유 : ok/tool_name/rows 의 top-level 구조가 그대로 살아야 하므로 {"rows": payload} 처럼 한 겹 더 씌우면 안 된다. 또 Week 4의 search_personal_reference_hits 처럼 metadata 로 묶고 싶어지지만 이번 tool은 "결과를 가공하지 않는다"가 계약이라 재조립 자체가 위반이고, sender/content/created_at 순서는 store 의 ORDER BY 가 보장하므로 tool 에서 손대면 오히려 깨지기 때문이다.

### extract_schedules_from_history() 함수 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

위의 프롬프트로 store 의 extract_schedules_from_history 가 normalize_external_member_names 와 normalize_external_schedule_date_bounds 를 이미 내부에서 호출하고 있어 wrapper 가 다시 정규화하면 중복이라는 것과, MCP 서버가 rows 에 external_schedule_summary(rows) 를 붙여 schedule_summary 까지 함께 돌려준다는 것을 확인한 뒤, 1번과 같은 pass-through 로 두는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : member_names/date_from/date_to 세 인자를 그대로 담아 call_mcp_tool_sync("extract_schedules_from_history", args) 를 호출하고 결과 문자열을 그대로 반환했다. 날짜 형식 정리와 schedule_summary 생성은 넣지 않았다.
- 수정 이유 : 테스트에서 date_from 을 "2000-01-01" 로 준 결과와 "2000-01-01T00:00:00" 으로 준 결과의 rows 18건이 완전히 동일하게 나와서, wrapper 에서 날짜를 정리하지 않아도 store 경계에서 이미 처리된다는 것을 확인했기 때문이다. 또 schedule_summary 를 tool 에서 다시 만들면 서버가 이미 만든 것과 중복되므로, 여기서는 결과를 통과시키기만 해야 한다.

### list_shared_schedules() 함수 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

store 의 list_shared_schedules 가 has_explicit_filter 로 "필터가 하나도 없으면 7월 실습용 기본 멤버/날짜로 대체"하는 분기를 가지고 있고, 그 조건에 member_names is not None 이 들어 있어서 빈 list 는 명시 필터로 간주되어 곧바로 빈 배열을 돌려준다는 것을 확인하였다. 또 limit 은 store 가 max(1, min(int(limit or 50), 200)) 으로 이미 클램프한다는 것을 확인한 뒤, 5개 인자를 그대로 넘기는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : member_names/date_from/date_to/source_conversation_id/limit 5개를 변형 없이 담아 call_mcp_tool_sync 로 넘기고 결과 문자열을 그대로 반환했다. limit 보정은 넣지 않았다.
- 수정 이유 : 1번과 같은 None / 빈 list 함정이 여기서는 더 크게 나타나서, member_names 를 빈 list 로 바꾸면 "기본 공유 일정 반환"이 "0건"으로 뒤집힌다. 실제로 테스트에서 필터 없이 부르면 18건, 빈 list 로 부르면 0건이 나와 두 분기가 갈리는 것을 확인했다. limit 은 args_schema 의 le=200 과 store 클램프로 이미 이중 보장돼 있어 tool 안에서 또 손대면 중복이기 때문이다.

### collect_member_schedules() 함수 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

여기서는 중복이 생길 수 있는 지점 두 곳을 먼저 확인하였다. 첫째로 fixed/app_store.py 의 save_structured_request 가 schedule_id = source_schedule_id or new_id("sch") 로 Week 1 임시 일정의 id 를 그대로 schedule_id 에 재사용하기 때문에, 같은 일정이 PERSONAL_SCHEDULES 와 SQLite 에 같은 식별자로 동시에 존재한다는 것을 확인했다. 둘째로 개인 일정을 저장하면 sync_personal_schedule_to_shared 가 member_name="나" 로 external_schedules 테이블에 복사본을 만드는데, extract_schedules_from_history 가 조회하는 테이블이 바로 그 테이블이라서 "나"를 외부 조회에 넣으면 내 일정이 두 경로로 들어온다는 것을 확인한 뒤, 헬퍼가 정리하고 tool 이 json_payload 로 감싸는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : \_personal_schedules_for_current_scope 에서 AppSQLiteStore(CONFIG.app_db_path).list_schedules 로 저장 일정을 읽고 그 schedule_id 집합을 만든 뒤, PERSONAL_SCHEDULES 중 현재 대화 범위이면서 그 집합에 없는 임시 일정만 뒤에 붙였다. \_collect_member_schedules 에서는 멤버 이름과 날짜를 helper 로 정규화한 다음 외부 조회 목록에서 "나"를 빼고, 내 일정은 \_structured_request_from_schedule_row 를 거쳐 member_name/title/date/start_time/end_time/notes 6개 필드 row 로 바꾸면서 날짜 범위 밖은 걸렀다. 외부 멤버가 있을 때만 MCP 를 한 번 호출해 rows 를 합치고, (date, start_time, member_name) 순으로 정렬한 뒤 external_schedule_summary 를 붙여 반환했다. notes 에는 "앱 저장 일정" / "현재 대화 임시 일정" 출처를 적었다.
- 수정 이유 : 중복 제거를 안 하면 Week 1에서 만든 일정을 Week 3에서 저장하는 순간 같은 일정이 rows 에 두 번 들어가서 Week 6의 busy_rows 근거가 어긋난다. 실제로 임시 일정을 같은 id 로 저장한 뒤에도 총 건수가 그대로 유지되는 것을 테스트로 확인했다. 또 MCP 호출은 매번 서버 subprocess 를 새로 띄워 한 번에 20초 이상 걸리므로 멤버마다 나눠 부르지 않고 한 번만 호출해야 하고, notes 에 출처를 적어야 LLM이 확정된 일정과 아직 저장 안 된 임시 일정을 구분해서 답할 수 있기 때문이다.

### create_shared_schedule() / delete_shared_schedule() 함수 구현하기

- AI 활용 내용 : 이전과 동일한 형식으로 활용하였다.

store 의 create_shared_schedule 이 ON CONFLICT(schedule_id) DO UPDATE 로 동작해서 같은 schedule_id 면 갱신이 되고 응답의 sync_status 가 created / updated 로 갈린다는 것과, delete_shared_schedules 가 schedule_id 와 source_conversation_id 를 AND 가 아니라 OR 로 묶어 삭제한다는 것을 확인한 뒤, 두 tool 모두 인자를 그대로 넘기는 설계 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : create 는 8개 인자, delete 는 2개 인자를 변형 없이 담아 각각 call_mcp_tool_sync 로 넘기고 결과 문자열을 그대로 반환했다. schedule_id 와 source_conversation_id 는 값이 None 이어도 키를 빼지 않고 그대로 실었다.
- 수정 이유 : 이 두 값이 나중에 같은 row 를 다시 찾아 갱신하거나 삭제할 유일한 근거라서 wrapper 에서 떨어뜨리면 동기화가 끊긴다. member_name/title/date/시간/notes 의 기본값 채우기와 괄호 제거는 store 가 전부 처리하므로 tool 에서 미리 손대면 중복이고, 실제로 같은 schedule_id 로 두 번 호출했을 때 created 다음 updated 가 나오고 조회 결과가 2건이 아니라 1건으로 유지되는 것을 테스트로 확인했기 때문이다.

---

## 구현하면서 고민한 점

- 고민한 점 : Week 5는 직접 SQL을 쓰는 주차가 아니라 MCP tool을 감싸고 결과를 그대로 넘기는 주차라서, 코드를 다 짜고 나서도 이게 제대로 된 건지 눈으로는 판단이 되지 않았다. tool 하나하나가 세 줄짜리 pass-through라 "실행은 되는데 맞게 되는 건가"를 판별할 기준 자체가 없었고, 특히 반환 JSON을 한 겹 더 감쌌는지 아닌지, 내 일정이 중복으로 들어가는지 같은 것은 결과만 봐서는 티가 나지 않았다. 그래서 클로드 코드에게 "무엇을 확인해야 이 구현이 검증됐다고 할 수 있는지"부터 물어보고, 확인할 항목을 먼저 정리한 다음에 테스트를 짰다.
- 해결방법 : 검증을 두 단계로 나눠서 진행했다.
  1단계는 agent를 거치지 않고 tool을 직접 호출하는 스크립트로 계약을 확인하는 것이었다. 반환 문자열의 앞부분이 {"ok": true 로 시작하는지 봐서 이중 인코딩이 없다는 것을 확인했고, member*names를 None으로 주면 18건 빈 리스트로 주면 0건이 나오는 것을 확인해 두 값의 분기가 실제로 갈린다는 것을 검증했다. date_from을 "2000-01-01"과 "2000-01-01T00:00:00"으로 각각 넘겨 rows 18건이 완전히 동일하게 나오는 것을 확인해, wrapper에서 날짜를 정리하지 않아도 store 경계에서 처리된다는 것도 확인했다. 중복 제거는 Week 1 임시 일정을 만든 뒤 같은 id로 SQLite에 저장하고 \_personal_schedules_for_current_scope의 결과 건수가 그대로 유지되는지로 검증했다. 공유 일정은 같은 schedule_id로 create를 두 번 불러 sync_status가 created 다음 updated로 바뀌고 조회 결과가 2건이 아닌 1건으로 유지되는지, delete 후 0건이 되는지까지 라이프사이클로 확인했다. 테스트로 만든 row는 전부 지워서 원상복구했다.
  2단계는 ./run.sh --week5 로 실제 agent에 질문하고 trace를 확인하는 것이었다. "7월 7일부터 17일까지 민준이랑 지훈이 일정 알려줘"에는 collect_member_schedules가 한 번만 호출되고 rows 9건이 나/민준/지훈 모두 같은 6개 필드 구조로 날짜순 정렬되어 나왔다. "민준이 일정만 알려줘"처럼 멤버를 한 명으로 좁히면 extract_schedules_from_history가 선택되어 3건이 나왔다. "민준이가 예전에 일정 얘기한 대화 찾아줘"로 search_previous_conversations가 ext_mj를 찾은 뒤 "그 대화 전체 내용을 보여줘"라고 이어서 물으면 load_conversation_messages가 그 ext_mj를 그대로 받아 호출되는 체인이 한 trace 안에 순서대로 찍혔다. 공유 일정은 필터 없이 물으면 기본 실습 18건이, "나"로 등록된 일정을 날짜와 함께 물으면 앱에서 동기화된 3건이 나왔고, 등록한 일정이 목록에 나타났다가 삭제로 사라지는 것도 확인했다. 마지막으로 "내가 저장해둔 일정 뭐 있지?"에는 외부 조회 tool이 아니라 Week 3 도구가 호출되어 출처 경계가 지켜지는 것도 확인했다.
  이 과정에서 trace만 보고 pass-through 여부를 판별하는 방법도 알게 됐다. MCP 결과를 그대로 넘긴 tool은 응답 top-level에 ok와 tool_name이 그대로 남아 있고, 내가 직접 두 출처를 합친 collect_member_schedules만 rows/schedule_summary/member_names 구조라서 모양이 다르다. json_payload로 한 번 더 감쌌다면 content가 escape된 문자열로 찍혔을 것이므로, 이 차이만 봐도 이중 인코딩이 없다는 것을 알 수 있었다. 같은 맥락으로 collect_member_schedules 결과의 "나" row에 source_conversation_id가 없다는 것이, 내 일정이 MCP 경로로 중복 유입되지 않았다는 직접적인 증거가 됐다.
  검증 도중 두 번 헷갈렸는데 둘 다 구현 문제가 아니었다. 첫째로 앱 기준 오늘이 2026-07-29인데 실습 데이터는 2026-07-07~17이라, 상대 날짜로 물으면 전부 0건이 나왔다. 구현이 틀린 게 아니라 그 범위에 데이터가 없는 것이어서 테스트 질문에는 항상 날짜를 명시했다. 둘째로 공유 저장소에서 "나" 일정을 조회했을 때 0건이 나와 자동 동기화가 실패한 줄 알았는데, DB를 직접 열어보니 shared_sch*... 형태로 3건이 정상 등록되어 있었고 LLM이 넘긴 날짜 필터가 과거 일정을 걸러낸 것이었다. 날짜를 명시해서 다시 물으니 3건이 그대로 나왔다.

---

## 과제 회고 (KPT)

- **Keep** (좋았고 계속 유지할 점) : 이번에도 거의 AI-first로 문제해결을 맡기고, 직접 판단하는 시간을 가졌는데 좀 더 빠르게 과제를 할 수 있었던 것 같다.
- **Problem** (아쉬웠거나 막혔던 점) : 아직 MCP에 대해서 이해하고 응용하는게 조금 어려운 것 같다.
- **Try** (다음에 시도해볼 점) : MCP 추가로 공부해보기
