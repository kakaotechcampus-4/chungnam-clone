---
name: verify-week3
description: Week 3 구현(student_parts/week03_build_nanas_logbook.py)을 검증한다. 메인과제(SaveStructuredRequestInput 스키마, save_structured_request/list_saved_requests/get_saved_request/personal_list_saved_schedules 저장·조회 tool, week03_tools/build_week03_agent 배선)와 추가 과제(_delete_saved_schedules 안전규칙, personal_update/delete_saved_schedules, personal_create_schedule Week1 호환, unwrap_legacy_payload/_save_input_from/structured_request_from_week01_schedule 정규화)를 모두 다룬다. py_compile, 모듈 import, 스키마 필드·기본값·description, 반환 JSON 계약, temp DB 왕복(저장→조회→수정→삭제)을 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 3 검증 (Verification)

`PROXY_TOKEN` 없이 실행 가능한 **정적 검증(1~7단계)**을 먼저 수행한다. 키가 있으면 **실경로 검증(8단계)**까지 한다.
각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. **코드는 수정하지 않는다.**

> **Phase B 확정본 (구현 검증 통과).** 이 skill은 대상 파일의 `[3주차 수강생 구현 가이드]`와 읽기 전용
> `fixed/app_store.py` 계약에서 유도한 뒤(Phase A), builder 구현본에 대해 verifier가 실행해
> **1~8단계 전부 PASS**로 확정했다(실경로 LLM 8단계 포함, 튜닝 후보 0건). 뼈대 assertion이 valid 구현과
> 어긋난 over-fit이 없어 완화 없이 승격됐다 — 저장 결과 wrapping 키(`saved_rows`/`shared_sync`)는
> 애초에 단언하지 않는 설계라 문제되지 않았다.
> 이후 회귀 검증·다음 주차 참조 자산으로 그대로 사용한다. 실행 중 실패가 나면 두 부류로 구분한다:
> **가이드가 못박은 계약 위반 → FAIL(구현 결함)**, **valid 구현인데 assertion이 과하게 좁아 실패 → skill 완화 후보**(코드는 고치지 않는다).

명령은 모두 `uv run python -X utf8`로 시작한다. `-X utf8`은 Windows 콘솔 코드페이지와 무관하게
한글 출력을 보존한다. `PYTHONIOENCODING=...` 접두어를 붙이면 `allowed-tools: Bash(uv *)` 패턴에서
벗어나 불필요한 권한 프롬프트가 뜨므로 쓰지 않는다.

셸이 `\u` 리터럴을 이스케이프 시퀀스로 해석해 `SyntaxError`를 내므로, 이스케이프 검사에는
`chr(92) + 'u'`를 쓴다.

## 격리 하네스 (DB를 건드리는 단계 공통)

Week 3 tool은 `_store()`가 만든 `AppSQLiteStore(CONFIG.app_db_path)`에 실제로 쓴다.
검증이 앱 DB(`data/kanana_app.sqlite3`)나 외부 공유 저장소를 오염시키지 않도록,
**`_store`를 임시 DB로 monkeypatch**하고 **외부 MCP sync 함수를 no-op으로** 바꾼다.
아래 단계들은 각 `-c` 스크립트 안에서 이 하네스를 인라인으로 세운다.

```python
# (참고용 하네스 — 각 단계에 인라인됨)
import tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as m
for _n in ('sync_personal_schedule_to_shared', 'sync_group_schedule_to_shared',
           'delete_personal_schedule_from_shared', 'delete_group_schedule_from_shared'):
    setattr(store_mod, _n, (lambda *a, **k: {'ok': True, 'stub': True}))
_tmp = Path(tempfile.mkdtemp()) / 'app.sqlite3'
m._store = lambda: store_mod.AppSQLiteStore(_tmp)   # 모든 tool이 이 임시 DB를 쓴다
```

`_store`는 `m` 모듈 전역이라 monkeypatch가 모든 tool 경로에 적용된다. 외부 sync 함수는
`fixed/app_store.py`가 자기 모듈 전역으로 호출하므로 `store_mod`에서 교체하면 저장/수정/삭제의
부작용이 차단된다.

---

## 1. 구문 검사 (py_compile)
```bash
uv run python -m py_compile student_parts/week03_build_nanas_logbook.py
```

## 2. import + 스키마 인스턴스화 스모크 테스트 (메인과제)
```bash
uv run python -X utf8 -c "import student_parts.week03_build_nanas_logbook as m; s=m.SaveStructuredRequestInput(); print('kind=', s.kind, '| source_schedule_id=', s.source_schedule_id, '| title=', s.title, '| members=', s.members, '| original_text=', repr(s.original_text)); s2=m.SaveStructuredRequestInput(kind='personal_schedule', title='코칭', members=['철수']); print('kind2=', s2.kind, '| members2=', s2.members); print('week03_tools len=', len(m.week03_tools())); print('OK')"
```
확인 포인트: `SaveStructuredRequestInput()`이 **필수 필드 없이** 생성됨(`kind` 기본 `'unknown'`, `source_schedule_id=None`), 상속 필드 기본값(`title=None`, `members=[]`, `original_text=''`), `week03_tools()` 길이 10.

## 3. 필드 스펙 대조 (메인과제)
```bash
uv run python -X utf8 -c "import student_parts.week03_build_nanas_logbook as m; f=m.SaveStructuredRequestInput.model_fields; print({k:(str(v.annotation), v.is_required(), bool(v.description)) for k,v in f.items()}); print('kind_default=', f['kind'].default, '| src_default=', f['source_schedule_id'].default)"
```
확인 포인트: 모든 필드 `is_required=False`(전부 기본값 보유), `kind` 기본 `'unknown'`, `source_schedule_id` 기본 `None`, `kind`·`source_schedule_id`에 한국어 description 존재. (상속 필드 description은 Week 2에서 이미 검증됨.)

## 4. tool 목록·배선 대조 (메인과제)
```bash
uv run python -X utf8 -c "
import inspect
import student_parts.week03_build_nanas_logbook as m
names = [t.name for t in m.week03_tools()]
print('week03_tools =', names)
expected = ['personal_create_schedule','personal_list_schedules','personal_delete_schedule','extract_schedule_request','save_structured_request','list_saved_requests','get_saved_request','personal_list_saved_schedules','personal_update_saved_schedule','personal_delete_saved_schedules']
assert names == expected, f'tool 목록 불일치: {names}'
assert m.week03_tools()[0] is m.personal_create_schedule, 'Week1 personal_create_schedule이 Week3 호환 tool로 교체되지 않음'
src = inspect.getsource(m.build_week03_agent)
assert 'create_agent' in src and 'week03_tools' in src and 'week03_system_prompt' in src, 'build_week03_agent 배선 누락'
assert 'response_format' not in src, 'Week3 agent에는 response_format을 쓰지 않는다(Week2와의 차이)'
assert '_WEEK03_AGENT' in src, '싱글턴 캐시(_WEEK03_AGENT) 미사용'
print('WIRING_OK')
"
```
확인 포인트: tool 이름/순서 정확, `personal_create_schedule`이 Week3 호환 tool(교체), `build_week03_agent`가 `create_agent`+`week03_tools`+`week03_system_prompt`로 배선되고 `response_format` 없음, 싱글턴 사용.

## 5. 메인 왕복 — 저장 → 조회 (temp DB, 키 불필요)
```bash
uv run python -X utf8 -c "
import json, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as m
for _n in ('sync_personal_schedule_to_shared','sync_group_schedule_to_shared','delete_personal_schedule_from_shared','delete_group_schedule_from_shared'):
    setattr(store_mod, _n, (lambda *a, **k: {'ok': True, 'stub': True}))
_tmp = Path(tempfile.mkdtemp()) / 'app.sqlite3'
m._store = lambda: store_mod.AppSQLiteStore(_tmp)

# 저장 (personal_schedule)
out = m.save_structured_request.invoke({'kind':'personal_schedule','title':'개인 코칭','date':'2026-07-16','start_time':'10:00','members':['철수']})
assert chr(92)+'u' not in out, 'ensure_ascii=False FAIL: 한글 이스케이프됨'
save = json.loads(out)
assert save.get('ok') is True and save.get('tool_name'), f'save 반환 계약 위반: {sorted(save)}'
print('save keys =', sorted(save))

# 조회 (list)
lst = json.loads(m.list_saved_requests.invoke({'kind':'personal_schedule'}))
assert lst.get('ok') is True and 'rows' in lst, f'list 반환 계약 위반: {sorted(lst)}'
rows = lst['rows']
assert len(rows) >= 1, '저장한 요청이 list에 보이지 않음(왕복 실패)'
rid = rows[0].get('request_id')
print('list rows =', len(rows), '| request_id =', rid)

# 단건 조회 (get) — 존재
g = json.loads(m.get_saved_request.invoke({'request_id': rid}))
assert g.get('ok') is True and 'row' in g and g['row'], 'get 단건 조회 실패'
# 단건 조회 (get) — 미존재는 row=None(예외 금지)
g2 = json.loads(m.get_saved_request.invoke({'request_id': 'req_does_not_exist'}))
assert 'row' in g2 and g2['row'] is None, '미존재 request는 row=None 이어야 함'

# 일정 목록
sch = json.loads(m.personal_list_saved_schedules.invoke({}))
assert sch.get('ok') is True and 'filters' in sch and 'schedules' in sch, f'schedule 조회 계약 위반: {sorted(sch)}'
assert len(sch['schedules']) >= 1, '저장한 개인 일정이 조회되지 않음'
print('schedules =', len(sch['schedules']))
print('MAIN_ROUNDTRIP_OK')
"
```
확인 포인트: 저장 반환에 `ok=True`+`tool_name`, list는 `rows`, get은 `row`(미존재 시 `None`, 예외 금지), `personal_list_saved_schedules`는 `filters`+`schedules`. **비어 있지 않은 실데이터로 왕복**이 성립해야 한다. (저장 결과 wrapping 키 이름은 가이드가 안 못박으므로 단언하지 않는다.)

## 5b. 결정적 중복 저장 차단 (idempotency) (temp DB, 키 불필요)

내용 기반 dedup 키(`_ensure_content_dedup_key` → `source_schedule_id`)가 세 저장 경로에서
같은 내용을 한 번만 저장하는지 결정적으로 확인한다. LLM 없이 temp DB로만 돈다.
```bash
uv run python -X utf8 -c "
import json, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as m
for _n in ('sync_personal_schedule_to_shared','sync_group_schedule_to_shared','delete_personal_schedule_from_shared','delete_group_schedule_from_shared'):
    setattr(store_mod, _n, (lambda *a, **k: {'ok': True, 'stub': True}))

def fresh():
    _tmp = Path(tempfile.mkdtemp()) / 'app.sqlite3'
    m._store = lambda: store_mod.AppSQLiteStore(_tmp)

def count():
    return len(json.loads(m.personal_list_saved_schedules.invoke({}))['schedules'])

# A: 같은 tool 2회 -> 두 번째는 already_exists, 일정 1건
fresh()
m.save_structured_request.invoke({'kind':'personal_schedule','title':'코칭','date':'2026-03-16','start_time':'10:00'})
a2 = json.loads(m.save_structured_request.invoke({'kind':'personal_schedule','title':'코칭','date':'2026-03-16','start_time':'10:00'}))
assert a2.get('already_exists') is True, f'A: 두 번째 저장이 already_exists 아님: {sorted(a2)}'
assert count() == 1, f'A: 같은 tool 2회인데 일정이 {count()}건'
print('A ok -> already_exists', a2.get('already_exists'), '| schedules', count())

# B: Week1 호환 경로 2회 -> 일정 1건
fresh()
m.personal_create_schedule.invoke({'title':'코칭','date':'2026-03-16','start_time':'10:00'})
m.personal_create_schedule.invoke({'title':'코칭','date':'2026-03-16','start_time':'10:00'})
assert count() == 1, f'B: Week1 호환 2회인데 일정이 {count()}건'
print('B ok -> schedules', count())

# C: 교차 경로(save_structured_request -> personal_create_schedule, 같은 내용) -> 일정 1건
fresh()
m.save_structured_request.invoke({'kind':'personal_schedule','title':'코칭','date':'2026-03-16','start_time':'10:00'})
m.personal_create_schedule.invoke({'title':'코칭','date':'2026-03-16','start_time':'10:00'})
assert count() == 1, f'C: 교차 경로 같은 내용인데 일정이 {count()}건'
print('C ok -> schedules', count())

# D: 음성 대조 - 제목만 다르면 별개 -> 일정 2건
fresh()
m.save_structured_request.invoke({'kind':'personal_schedule','title':'코칭','date':'2026-03-16','start_time':'10:00'})
m.save_structured_request.invoke({'kind':'personal_schedule','title':'회의','date':'2026-03-16','start_time':'10:00'})
assert count() == 2, f'D: 제목이 다른데 과도하게 dedup됨(일정 {count()}건)'
print('D ok -> schedules', count())
print('IDEMPOTENCY_OK')
"
```
확인 포인트: A/B/C는 같은 내용을 어느 경로로 저장하든 일정이 **정확히 1건**(A는 두 번째 반환 `already_exists=True`), D는 제목만 달라도 **2건**으로 남아 dedup이 과하지 않다.

## 6. 추가 — 삭제 안전규칙 + delete_all/필터 분기 (temp DB)
```bash
uv run python -X utf8 -c "
import json, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as m
for _n in ('sync_personal_schedule_to_shared','sync_group_schedule_to_shared','delete_personal_schedule_from_shared','delete_group_schedule_from_shared'):
    setattr(store_mod, _n, (lambda *a, **k: {'ok': True, 'stub': True}))
_tmp = Path(tempfile.mkdtemp()) / 'app.sqlite3'
m._store = lambda: store_mod.AppSQLiteStore(_tmp)

# 안전규칙: 조건이 하나도 없으면 삭제하지 않는다
d0 = json.loads(m.personal_delete_saved_schedules.invoke({}))
assert d0.get('ok') is False, '조건 없는 삭제가 거부되지 않음(안전규칙 위반)'
assert d0.get('deleted_count', 0) == 0, '조건 없는 삭제가 실제로 지움'
print('no-condition delete ->', d0.get('ok'), d0.get('deleted_count'))

# 데이터 2건 저장 후 필터 삭제
m.save_structured_request.invoke({'kind':'personal_schedule','title':'A','date':'2026-07-16','start_time':'10:00'})
m.save_structured_request.invoke({'kind':'personal_schedule','title':'B','date':'2026-07-17','start_time':'11:00'})
df = json.loads(m.personal_delete_saved_schedules.invoke({'date':'2026-07-16'}))
assert df.get('ok') is True, '필터 삭제 실패'
assert set(('deleted_count','filters','deleted')) <= set(df), f'삭제 반환 키 누락: {sorted(df)}'
assert df['deleted_count'] >= 1, '필터에 맞는 일정이 삭제되지 않음'
print('filter delete ->', df['deleted_count'], '| keys =', sorted(df))

# delete_all
da = json.loads(m.personal_delete_saved_schedules.invoke({'delete_all': True}))
assert da.get('ok') is True and 'deleted_count' in da, 'delete_all 반환 계약 위반'
left = json.loads(m.personal_list_saved_schedules.invoke({}))['schedules']
assert left == [], f'delete_all 후에도 일정이 남음: {len(left)}'
print('delete_all ->', da['deleted_count'], '| remaining =', len(left))
print('DELETE_GUARD_OK')
"
```
확인 포인트: **조건 없는 삭제는 `ok=False`+`deleted_count=0`**(조용히 전체 삭제하면 FAIL), 필터/`delete_all` 분기 모두 `deleted_count/filters/deleted` 유지, `delete_all` 후 목록이 빔. `delete_saved_schedules_dict(...)`도 동일 로직을 dict로 돌려주는지 필요 시 함께 확인.

## 7. 추가 — 수정 + Week1 호환 생성 + 레거시 정규화 (temp DB)
```bash
uv run python -X utf8 -c "
import json, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as m
for _n in ('sync_personal_schedule_to_shared','sync_group_schedule_to_shared','delete_personal_schedule_from_shared','delete_group_schedule_from_shared'):
    setattr(store_mod, _n, (lambda *a, **k: {'ok': True, 'stub': True}))
_tmp = Path(tempfile.mkdtemp()) / 'app.sqlite3'
m._store = lambda: store_mod.AppSQLiteStore(_tmp)

# structured_request_from_week01_schedule: attendees->members, id->source_schedule_id
w1 = {'id':'personal_abc','title':'회의','date':'2026-07-16','start_time':'10:00','end_time':'미정','attendees':['철수','영희']}
conv = m.structured_request_from_week01_schedule(w1)
assert isinstance(conv, m.SaveStructuredRequestInput), '변환 결과 타입 오류'
assert conv.members == ['철수','영희'], f'attendees->members 실패: {conv.members}'
assert conv.source_schedule_id == 'personal_abc', f'id->source_schedule_id 실패: {conv.source_schedule_id}'
print('w01->save conv:', conv.kind, conv.members, conv.source_schedule_id)

# unwrap_legacy_payload: wrapper dict 정규화
u1 = m.SaveStructuredRequestInput.model_validate({'structured_request': {'kind':'todo','title':'과제'}})
assert u1.kind == 'todo' and u1.title == '과제', f'structured_request wrapper 언랩 실패: {u1}'
u2 = m.SaveStructuredRequestInput.model_validate({'kind':'reminder','title':'약'})
assert u2.kind == 'reminder', '평범한 dict 통과 실패'
print('unwrap ok:', u1.kind, u2.kind)

# _save_input_from: dict / StructuredRequest / JSON
from student_parts.week02_structure_natural_language_requests import StructuredRequest
a = m._save_input_from({'kind':'personal_schedule','title':'x'}); assert isinstance(a, m.SaveStructuredRequestInput)
b = m._save_input_from(StructuredRequest(kind='todo', title='y')); assert isinstance(b, m.SaveStructuredRequestInput) and b.kind=='todo'
c = m._save_input_from(json.dumps({'kind':'reminder','title':'z'})); assert isinstance(c, m.SaveStructuredRequestInput) and c.kind=='reminder'
print('save_input_from ok:', a.kind, b.kind, c.kind)

# Week1 호환 personal_create_schedule: 임시 생성 + SQLite 이중 저장
created = json.loads(m.personal_create_schedule.invoke({'title':'개인 코칭','date':'2026-07-16','start_time':'10:00','attendees':['철수']}))
assert 'structured_request' in created and 'sqlite_save' in created, f'이중 기록 키 누락: {sorted(created)}'
saved_sch = json.loads(m.personal_list_saved_schedules.invoke({}))['schedules']
assert len(saved_sch) >= 1, 'Week1 호환 생성이 SQLite에 저장되지 않음'
sid = saved_sch[0]['schedule_id']
print('compat create -> schedule_id', sid)

# personal_update_saved_schedule: 존재/미존재
up = json.loads(m.personal_update_saved_schedule.invoke({'schedule_id': sid, 'start_time':'14:00'}))
assert up.get('ok') is True and 'updated_schedule' in up and 'shared_sync' in up, f'수정 반환 계약 위반: {sorted(up)}'
assert up['updated_schedule'].get('start_time') == '14:00', '수정 값이 반영되지 않음'
up_none = None
try:
    up_none = json.loads(m.personal_update_saved_schedule.invoke({'schedule_id':'sch_missing','title':'x'}))
except Exception as e:
    raise AssertionError(f'미존재 수정이 예외를 던짐(ok=False로 답해야 함): {e}')
assert up_none.get('ok') is False, '미존재 schedule 수정이 ok=False가 아님'
print('update ok:', up['updated_schedule'].get('start_time'), '| missing ->', up_none.get('ok'))
print('EXTRA_OK')
"
```
확인 포인트: 변환 매핑 정확, `unwrap_legacy_payload`가 `structured_request`/`payload` wrapper와 평범한 dict를 모두 처리, `_save_input_from` 3분기, Week1 호환 생성이 `structured_request`+`sqlite_save`를 담고 실제 SQLite에 남음, 수정은 값 반영·`updated_schedule`/`shared_sync` 유지·미존재는 `ok=False`(예외 금지).

## 8. 실경로 LLM 검증 (`.env`에 `PROXY_TOKEN`이 있을 때만)

키가 없으면 이 단계는 **N/A(사유: PROXY_TOKEN 없음)**로 기록하고 넘어간다.
`uv run python -X utf8 -c "from fixed.config import CONFIG; print(CONFIG.has_openai_key)"`로 먼저 확인한다.

가이드 line 112-117 시나리오: `./run.sh --week3`에서 "내일 10시 개인 코칭 저장해줘" 입력 →
trace에서 `extract_schedule_request` 다음 `save_structured_request` 호출 확인 →
"내 일정 보여줘"가 `personal_list_saved_schedules`로 조회되고, 앱 재시작/새 대화에서도
저장 일정이 유지되는지 확인한다. 이어서 `personal_update_saved_schedule`로 시간 변경,
`personal_delete_saved_schedules`에 `schedule_ids`/필터를 넘겨 삭제된 일정이 목록에서 사라지는지 본다.

> 8단계는 앱 실행이 필요하므로 `allowed-tools: Bash(uv *)` 밖이다. 실행 방법은 호출자 판단에 맡기고,
> 여기서는 trace로 확인할 계약(호출 순서·영속성)만 규정한다.

---

## 보고

**이 skill은 절차(무엇을 어떤 명령으로 실행할지)만 규정한다. 출력 형식은 규정하지 않는다.**
호출자의 지시가 항상 우선한다:
- verifier subagent가 preload로 실행할 때 → `verifier.md`의 "반환 형식"을 따른다.
- 사용자가 프롬프트로 특정 형식을 요구하면 → 그 요구를 먼저 만족시킨다.
- `/verify-week3`로 직접 호출되어 다른 지시가 없을 때만 아래 기본값을 쓴다.

기본값:
- 각 단계 명령 + 원문 출력 + PASS/FAIL.
- 실패 항목은 무엇이·왜 어긋났는지 근거(`file:line`)와 함께 명시.
- 1~7단계 전부 통과할 때만 "정적 검증 통과". 8단계를 건너뛰었으면 그 사실을 결론에 남긴다.

**Phase B 튜닝 지침**: assertion이 valid 구현을 FAIL시키면(특히 저장 결과 wrapping 키처럼 가이드가
이름을 안 못박은 부분) skill을 완화한다. 반대로 가이드가 못박은 계약(반환 키 `ok/tool_name/rows/row/filters/schedules/deleted_count/deleted`,
삭제 안전규칙, 값 왕복, 미존재 `ok=False`/`row=None`, 임의값 금지)을 못 지키면 구현 결함이므로 FAIL로 남긴다.
