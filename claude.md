# Nana 프로젝트 — Week 01

## 실행 명령어
```
./run.sh --week1
```

## 주요 파일
- 구현 파일: `student_parts/week01_wake_up_nana.py`
- 세션 헬퍼: `fixed/session_scope.py` → `current_session_scope()`
- 임시 저장소: 모듈 레벨 `PERSONAL_SCHEDULES: list[dict]`

## 아키텍처
Week 1 개인 일정은 SQLite가 아닌 인메모리 리스트(`PERSONAL_SCHEDULES`)에만 저장된다.
세션 격리는 `current_session_scope()`로 처리하며, 모든 CRUD는 같은 `session_id`인 항목만 대상으로 한다.
LLM은 `@tool` 3개(`personal_create_schedule`, `personal_list_schedules`, `personal_delete_schedule`)를 통해 일정을 관리한다.

## 반환 키 규격 (Hard constraint)
| tool | 필수 키 |
|------|---------|
| `personal_create_schedule` | `ok`, `tool_name`, `created_schedule` |
| `personal_list_schedules`  | `ok`, `tool_name`, `schedules` |
| `personal_delete_schedule` | `ok`, `tool_name`, `deleted` |

- 모든 반환값은 `_json()`을 경유한 `str`
- `structured_request`, `sqlite_save` 키 사용 금지

## 절대 금지 / 반드시 지킬 것 (Known gotchas)
- DB(SQLite / App store) 호출 금지 — Week 1은 인메모리 전용
- 삭제 시 `PERSONAL_SCHEDULES[:] = ...` 슬라이스 대입 사용 (레퍼런스 유지)
- 일정 생성 시 `session_id: current_session_scope()` 필수
- `personal_list_schedules`는 `PERSONAL_SCHEDULES` 직접 수정 금지
- 헬퍼(`_json`, `_now_iso`, `_new_personal_id`, `_schedule_scope`, `_current_session_schedules`) 재사용
