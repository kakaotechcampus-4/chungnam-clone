# Week3 프로젝트 지도 — 헷갈릴 때마다 이 파일부터 열어보기

## 1. 어떤 파일을 봐야 하나 (딱 이 3개 + 1개)

| 파일 | 내가 고치는 곳인가 | 뭐가 들어있나 |
|---|---|---|
| `student_parts/week01_wake_up_nana.py` | 아니요 (이미 끝남) | Week1: 임시메모리 일정 CRUD 3개 tool (`personal_create_schedule`, `personal_list_schedules`, `personal_delete_schedule`) |
| `student_parts/week02_structure_natural_language_requests.py` | 아니요 (이미 끝남) | Week2: 자연어 → `StructuredRequest` 구조화. `extract_schedule_request`(bridge tool)도 여기 있음 |
| `student_parts/week03_build_nanas_logbook.py` | **네, 오늘 여기만** | Week3: SQLite 저장/조회/수정/삭제 tool들 + 이 셋을 다 묶는 system prompt + agent |
| `fixed/app_store.py` | 아니요, 읽기만 | 진짜 SQL(`INSERT`/`SELECT`/`UPDATE`/`DELETE`) 실행하는 곳. `_store()`가 이 클래스를 가져다 씀 |

`fixed/` 폴더 전체는 강사가 이미 만들어준 코드라서 안 고쳐도 돼요. 필요하면 "읽기만" 하면 돼요.

## 2. 사용자가 앱에 문장 하나 치면 실제로 무슨 일이 일어나나 (순서)

```
1. 사용자가 "채팅" 탭에 문장 입력 (예: "내일 10시 회의 저장해줘")
2. app.py가 build_week_agent() 호출 → 내부적으로 build_week03_agent() 실행
3. week03_system_prompt()가 만든 규칙(1~3주차 프롬프트 누적)을 AI가 읽음
4. AI가 문장을 보고 "지금 어떤 tool을 불러야 하나" 판단
   (판단 기준 = week03_tools() 목록 + 각 tool의 설명 + system prompt 규칙)
5. 저장 요청이면:
   extract_schedule_request(query="내일 10시 회의 저장해줘")
     → {"kind": "personal_schedule", "title": "회의", "date": "...", ...} 반환
   그 값을 그대로
   save_structured_request(kind=..., title=..., date=...)
     → _store().save_structured_request(payload) → SQLite에 진짜 INSERT
6. 조회 요청이면: personal_list_saved_schedules(...) 호출 → SQLite에서 SELECT
7. 수정 요청이면: personal_list_saved_schedules(먼저 확인) → personal_update_saved_schedule(...)
8. 삭제 요청이면: personal_list_saved_schedules(먼저 확인) → personal_delete_saved_schedules(...)
9. 최종 답변은 "채팅" 탭에, 방금 3~8번 과정 전체는 "상세" 탭의 trace JSON에 나타남
```

## 3. TODO 하나를 채울 때 실제로 하는 것 (순서, 새 가정 필요 없음)

1. `week03_build_nanas_logbook.py`에서 TODO가 있는 함수를 찾음
2. 그 함수 바로 위/파일 상단의 "[N주차 수강생 구현 가이드]" 주석을 읽음 — 뭘 해야 하는지 이미 다 적혀있음
3. 비슷한 일을 하는, **이미 채워진 다른 함수**를 하나 골라서 패턴을 베낌 (예: `list_saved_requests`를 보면 `get_saved_request` 패턴이 보임)
4. `_store()`가 제공하는 메서드 중 뭘 불러야 하는지 확인 (`fixed/app_store.py`에서 메서드 이름 검색)
5. `tool_result(...)` + `json_payload(...)`로 반환값 포장 (거의 모든 tool이 이 패턴)

## 4. 지금 남은 TODO 체크리스트

- [ ] `unwrap_legacy_payload`
- [ ] `_save_input_from`
- [ ] `save_structured_request_payload`
- [ ] `structured_request_from_week01_schedule`
- [ ] `delete_saved_schedules_dict`
- [ ] `_delete_saved_schedules`의 `return` 위치 버그 (else 블록 밖으로 빼기)

(이미 끝난 것: `save_structured_request`, `list_saved_requests`, `get_saved_request`, `personal_list_saved_schedules`, `personal_create_schedule`, `personal_update_saved_schedule`, `personal_delete_saved_schedules`, 프롬프트 2개, `build_week03_agent`)
