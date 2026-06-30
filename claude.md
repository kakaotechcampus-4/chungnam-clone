# Week 01 구현 계획서

## 0. 파일 위치

- **구현 대상**: `student_parts/week01_wake_up_nana.py`
- **세션 스코프**: `fixed/session_scope.py` → `current_session_scope()`
- **임시 저장소**: 모듈 레벨 `PERSONAL_SCHEDULES: list[dict]`

---

## 1. 구현 계획 요약

### 핵심 흐름도

```
LLM 호출
  └─ @tool 선택
       ├─ personal_create_schedule  → dict 생성 + PERSONAL_SCHEDULES.append()
       ├─ personal_list_schedules   → _current_session_schedules() 필터링 (읽기 전용)
       └─ personal_delete_schedule  → PERSONAL_SCHEDULES[:] = [...] (레퍼런스 유지)
```

### 세션 격리 방안

`current_session_scope()`는 현재 대화의 `conversation_id`를 문자열로 반환한다.
모든 생성/조회/삭제는 `_current_session_schedules()`(= `session_id`가 동일한 항목만 필터)를 경유한다.
이미 파일에 구현된 `_schedule_scope()` / `_current_session_schedules()` 헬퍼를 그대로 사용한다.

---

### 1-1. `personal_create_schedule`

**입력 인자**: `title`, `date`, `start_time`, `end_time`(기본 `"미정"`), `attendees`(기본 `None`)

**핵심 로직**:
```python
schedule = {
    "id":         _new_personal_id(),       # "personal_<10자 hex>"
    "title":      title,
    "date":       date,
    "start_time": start_time,
    "end_time":   end_time,
    "attendees":  attendees or [],          # None → []
    "created_at": _now_iso(),
    "session_id": current_session_scope(),  # 세션 격리 키
}
PERSONAL_SCHEDULES.append(schedule)
return _json({"ok": True, "tool_name": "personal_create_schedule", "created_schedule": schedule})
```

---

### 1-2. `personal_list_schedules`

**입력 인자**: `date_from: str | None`, `date_to: str | None`

**핵심 로직**:
```python
schedules = _current_session_schedules()          # 세션 필터
if date_from: schedules = [s for s in schedules if s["date"] >= date_from]
if date_to:   schedules = [s for s in schedules if s["date"] <= date_to]
return _json({"ok": True, "tool_name": "personal_list_schedules", "schedules": schedules})
```

- `PERSONAL_SCHEDULES` 자체를 수정하지 않음 (지역 변수 `schedules`만 다룸).
- 날짜 비교는 `YYYY-MM-DD` 문자열 사전순 비교로 충분.

---

### 1-3. `personal_delete_schedule`

**입력 인자**: `schedule_id: str`

**핵심 로직**:
```python
session_id = current_session_scope()
before = len(PERSONAL_SCHEDULES)
PERSONAL_SCHEDULES[:] = [
    s for s in PERSONAL_SCHEDULES
    if not (s["id"] == schedule_id and _schedule_scope(s) == session_id)
]
deleted = before - len(PERSONAL_SCHEDULES)
return _json({"ok": True, "tool_name": "personal_delete_schedule", "deleted": deleted})
```

- `PERSONAL_SCHEDULES[:] = ...` 슬라이스 대입으로 **리스트 객체 레퍼런스 유지**.
- 다른 세션의 동일 ID는 `_schedule_scope(s) == session_id` 조건에서 걸러짐.

---

## 2. 반환 규격 체크리스트

| tool | 필수 키 | `_json()` 경유 | 금지 키 |
|------|---------|---------------|--------|
| `personal_create_schedule` | `ok`, `tool_name`, `created_schedule` | ✅ | `structured_request`, `sqlite_save` |
| `personal_list_schedules` | `ok`, `tool_name`, `schedules` | ✅ | — |
| `personal_delete_schedule` | `ok`, `tool_name`, `deleted` | ✅ | — |

- 모든 반환값은 `str` (LangChain tool 안정성).
- 한글 포함 가능 → `ensure_ascii=False` (이미 `_json()`에 적용).
- SQLite / App store 호출 없음.

---

## 3. 통합 테스트 하네스 프롬프트

앱을 `./run.sh --week1`로 실행한 뒤 채팅창에 아래 한 문장을 입력한다.

```
다음 주 화요일 오전 10시부터 11시까지 "팀 스프린트 회의" 일정을 만들고,
만들어진 일정 목록을 보여준 다음, 방금 만든 일정을 삭제하고,
마지막으로 남은 일정이 없는지 다시 한번 확인해줘.
```

**기대 동작 순서**:
1. `personal_create_schedule` 호출 → `created_schedule` 포함 JSON 반환
2. `personal_list_schedules` 호출 → `schedules` 배열에 위 일정 포함
3. `personal_delete_schedule` 호출 → `deleted: 1` 반환
4. `personal_list_schedules` 재호출 → `schedules: []` 반환

**Trace 확인 포인트**:
- 상세 trace에서 위 4개 tool이 순서대로 호출됐는지 확인.
- 각 tool 결과 JSON에 지정 키(`created_schedule` / `schedules` / `deleted`)가 누락 없이 있는지 확인.
- 삭제 후 재조회에서 빈 배열이 반환되는지 확인.

---

## 4. 구현 시 주의사항

| 항목 | 내용 |
|------|------|
| 레퍼런스 유지 | 삭제 시 반드시 `PERSONAL_SCHEDULES[:] = ...` 사용 |
| 세션 격리 | 생성 시 `session_id: current_session_scope()` 필수 |
| 읽기 전용 조회 | `personal_list_schedules`는 `PERSONAL_SCHEDULES` 직접 수정 금지 |
| 헬퍼 활용 | `_json`, `_now_iso`, `_new_personal_id`, `_schedule_scope`, `_current_session_schedules` 재사용 |
| DB 호출 금지 | Week 1은 인메모리 전용, SQLite / App store 호출 없음 |
