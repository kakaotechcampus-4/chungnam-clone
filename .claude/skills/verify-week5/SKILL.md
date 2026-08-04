---
name: verify-week5
description: Week 5 구현(student_parts/week05_load_kanas_past_conversations.py)을 검증한다. 메인과제(search_previous_conversations/load_conversation_messages/extract_schedules_from_history/list_shared_schedules/collect_member_schedules wrapper와 _personal_schedules_for_current_scope/_collect_member_schedules helper)와 추가 과제(create_shared_schedule/delete_shared_schedule wrapper)를 모두 다룬다. py_compile, 모듈 import, 스키마 필드·기본값·bounds, MCP 반환 JSON 계약(ok/tool_name/rows/schedule_summary/shared_schedule/deleted_count), temp 외부 SQLite 왕복(검색→로드→추출→공유 등록/조회/삭제), collect_member_schedules의 "나"+외부 멤버 rows 통합·schedule_summary·SQLite/임시 일정 중복 제거·대화 범위 격리, 책임 경계(직접 SQL·중복 정규화 금지), week05_tools/build_week05_agent 배선을 PROXY_TOKEN 없이 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 5 검증 (Verification)

Week 5는 **LLM 임베딩이 필요 없다.** 모든 경로가 외부 SQLite MCP subprocess를 타므로
`PROXY_TOKEN` 없이 **1~10단계 전부 실행 가능**하다. 키가 필요한 것은 agent 조립(11단계, 선택)뿐이다.
각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. **코드는 수정하지 않는다.**

> **Phase A 뼈대 (구현 전 작성).** 이 skill은 대상 파일의 `[5주차 수강생 구현 가이드]`와
> 읽기 전용 `mcp_server/sqlite_mcp_server.py`·`fixed/external_people_store.py`·`fixed/external_mcp.py`·
> `fixed/app_store.py` 계약에서 유도했다. builder 구현 후 verifier가 실행해 확정한다.
> 실행 중 실패가 나면 두 부류로 구분한다: **가이드/MCP 서버가 못박은 계약 위반 → FAIL(구현 결함)**,
> **valid 구현인데 assertion이 과하게 좁아 실패 → skill 완화 후보**(코드는 고치지 않는다).
> 특히 가이드가 이름을 못박지 않은 부분(collect_member_schedules의 rows/schedule_summary 이외 부가 키,
> 요약 문자열의 정확한 문구)은 단언하지 않는다.

명령은 모두 `uv run python -X utf8`로 시작한다. `-X utf8`은 Windows 콘솔 코드페이지와 무관하게
한글 출력을 보존한다. `PYTHONIOENCODING=...` 접두어를 붙이면 `allowed-tools: Bash(uv *)` 패턴에서
벗어나 불필요한 권한 프롬프트가 뜨므로 쓰지 않는다.

## 왜 키 없이 다 되는가

Week 4는 ChromaDB 임베딩 때문에 실왕복에 키가 필요했다. Week 5의 tool은 전부
`call_local_mcp_tool_sync`(fixed/mcp_client.py:117) 또는 `call_external_tool_payload`(fixed/external_mcp.py:19)를
거쳐 `mcp_server/sqlite_mcp_server.py`를 **stdio subprocess로** 부르고, 서버는 순수 SQLite만 읽는다.
`collect_member_schedules`가 합치는 내 일정도 앱 SQLite와 Week 1 인메모리 리스트라 임베딩이 없다.

## 격리 하네스 (외부/앱 DB를 건드리는 단계 공통)

⚠️ **환경변수 격리가 필수다.** MCP subprocess는 `KANANA_EXTERNAL_DB_PATH`(없으면 `CONFIG.external_db_path`)를
읽는다(mcp_server/sqlite_mcp_server.py:25). 이 변수를 temp로 돌리지 않으면 검증이 사용자 실 외부 DB에
공유 일정 row를 쓰고 지운다. `fixed/mcp_client.py`가 **호출 시점에** `os.environ`을 복사하므로
(fixed/mcp_client.py:85-87) 첫 tool 호출 전에 세팅하면 된다.

⚠️ **앱 DB도 같이 돌린다.** `_personal_schedules_for_current_scope()`는 `AppSQLiteStore(CONFIG.app_db_path)`를
호출 시점에 연다. 모듈 전역 `m.CONFIG`를 temp로 교체해야 실 DB(`data/kanana_app.sqlite3`)를 읽지 않는다.

⚠️ **`PERSONAL_SCHEDULES`는 모듈 전역 리스트다.** 케이스 사이에 `w1.PERSONAL_SCHEDULES[:] = []`로 비운다.
비우지 않으면 앞 케이스의 임시 일정이 뒤 케이스 rows에 새어 들어와 "빈 값으로 통과한 검사"가 된다.

```python
# (참고용 하네스 — 각 단계에 인라인됨)
import os, tempfile
from dataclasses import replace
from pathlib import Path
from fixed.config import CONFIG
import student_parts.week01_wake_up_nana as w1
import student_parts.week05_load_kanas_past_conversations as m
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')   # MCP subprocess (첫 호출 전에)
m.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w1.PERSONAL_SCHEDULES[:] = []
```

⚠️ **MCP subprocess가 stderr로 `INFO ... Processing request of type CallToolRequest` 로그를 뿜는다.**
5단계 이후 판정 출력이 이 로그에 묻히므로, 명령 끝에
`| grep -v "Processing request of type\|ListToolsRequest\|CallToolRequest\|server.py:"`
를 덧붙여 읽는다. **Python 코드 자체는 절대 바꾸지 않는다** — 필터는 출력 가독성용일 뿐이다.

temp 외부 DB는 첫 접근 때 `ExternalPeopleSQLiteStore`가 스스로 스키마 생성 + July 실습 fixture를
seed한다(fixed/external_people_store.py:65-84, `JULY_PRACTICE_*`). 그래서 별도 seed 없이
**2026-07-07 ~ 2026-07-17 / 철수·영희·민준·서연·지훈·하린** 데이터를 기대할 수 있다.

---

## 1. 구문 검사 (py_compile)
```bash
uv run python -m py_compile student_parts/week05_load_kanas_past_conversations.py
```

## 2. import + 스키마 인스턴스화 스모크 테스트
```bash
uv run python -X utf8 -c "
import student_parts.week05_load_kanas_past_conversations as m
s = m.SearchPreviousConversationsInput(query='q'); print('search member_names=', s.member_names, '| limit=', s.limit)
l = m.LoadConversationMessagesInput(conversation_id='ext_cs'); print('load cid=', l.conversation_id)
e = m.ExtractSchedulesFromHistoryInput(member_names=['철수'], date_from='2026-07-07', date_to='2026-07-17'); print('extract=', e.member_names, e.date_from, e.date_to)
c = m.CreateSharedScheduleInput(member_name='나', title='t', date='2026-07-08', start_time='10:00'); print('create end_time=', c.end_time, '| notes=', c.notes, '| sid=', c.schedule_id)
d = m.DeleteSharedScheduleInput(); print('delete sid=', d.schedule_id, '| src=', d.source_conversation_id)
ls = m.ListSharedSchedulesInput(); print('list limit=', ls.limit, '| members=', ls.member_names)
cm = m.CollectMemberSchedulesInput(member_names=['철수'], date_from='2026-07-07', date_to='2026-07-17'); print('collect=', cm.member_names)
print('tools len=', len(m.week05_tools()))
print('OK')
"
```
확인 포인트: `SearchPreviousConversationsInput.member_names` 기본 `None`·`limit` 기본 5,
`CreateSharedScheduleInput.end_time` 기본 `"미정"`·나머지 선택 필드 `None`,
`ListSharedSchedulesInput.limit` 기본 50, `week05_tools()` 길이 = `week04_tools()` + 7.
**스키마는 과제 스캐폴드가 준 코드다 — 값이 바뀌어 있으면 그 자체가 FAIL이다(임의 수정 금지).**

## 3. 필드 스펙 + bounds 대조
```bash
uv run python -X utf8 -c "
import student_parts.week05_load_kanas_past_conversations as m
names = ['SearchPreviousConversationsInput','LoadConversationMessagesInput','ExtractSchedulesFromHistoryInput','CreateSharedScheduleInput','DeleteSharedScheduleInput','ListSharedSchedulesInput','CollectMemberSchedulesInput']
for n in names:
    c = getattr(m, n)
    print(n, {k: (str(v.annotation), v.is_required()) for k, v in c.model_fields.items()})

# bounds는 print만 하면 검사가 아니다 — 경계 밖 값이 실제로 거부되는지 단정한다.
for name, model, kwargs, field_name, lo, hi in [
    ('SearchPreviousConversationsInput', m.SearchPreviousConversationsInput, {'query':'q'}, 'limit', 1, 50),
    ('ListSharedSchedulesInput', m.ListSharedSchedulesInput, {}, 'limit', 1, 200),
]:
    for bad in (lo - 1, hi + 1):
        try:
            model(**kwargs, **{field_name: bad})
            raise AssertionError(name + '.' + field_name + ' 가 경계 밖 값 ' + str(bad) + ' 를 통과시킴')
        except AssertionError:
            raise
        except Exception:
            pass
    print(name + '.' + field_name + ' bounds OK [' + str(lo) + ',' + str(hi) + ']')
print('SCHEMA_OK')
"
```
확인 포인트: 필드/타입이 파일 상단 정의와 일치하고 **경계 밖 값이 실제로 거부**된다.

## 4. tool 목록·배선 대조
```bash
uv run python -X utf8 -c "
import student_parts.week05_load_kanas_past_conversations as m
import student_parts.week04_retrieve_nanas_memory as w4
w4_names = [t.name for t in w4.week04_tools()]
w5_names = [t.name for t in m.week05_tools()]
print('week04 tools =', len(w4_names))
print('week05 tools =', w5_names)
expected = ['search_previous_conversations','load_conversation_messages','extract_schedules_from_history','create_shared_schedule','delete_shared_schedule','list_shared_schedules','collect_member_schedules']
assert w5_names[:len(w4_names)] == w4_names, 'week04 tool 누적 순서가 보존되지 않음'
added = w5_names[len(w4_names):]
assert added == expected, 'Week5 추가 tool 목록/순서 불일치: ' + str(added)
parts = m.week05_prompt_parts()
w4_parts = w4.week04_prompt_parts()
assert parts[:len(w4_parts)] == w4_parts, 'week04_prompt_parts 누적이 깨짐'
print('week05 신규 prompt 조각 수 =', len(parts) - len(w4_parts))
prompt = m.week05_system_prompt()
print('system_prompt len =', len(prompt))
print('WIRING_OK')
"
```
확인 포인트: Week 4 tool/prompt 조각이 **앞쪽에 그대로 누적**되고 Week 5 tool 7개가 뒤에 붙는다.
`week05_system_prompt()`는 `join_system_prompt(week05_prompt_parts())` 결과여야 한다.
추가 과제를 구현하지 않기로 했다면 `create_shared_schedule`/`delete_shared_schedule`이
목록에서 빠져 있어야 하고(가이드 line 48·68), 그때는 `expected`에서 두 이름을 뺀 뒤 다시 판정한다.

## 5. MCP wrapper 왕복 — 검색 / 로드 / 추출 (temp 외부 DB)
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
import student_parts.week05_load_kanas_past_conversations as m

r = json.loads(m.search_previous_conversations.invoke({'query': 'QA 리뷰', 'member_names': ['철수'], 'limit': 5}))
print('search keys =', sorted(r))
assert r['ok'] is True and r['tool_name'] == 'search_previous_conversations', r
assert isinstance(r['rows'], list) and r['rows'], 'seed된 철수 대화를 못 찾음(=검색 경로 미연결)'
assert 'conversation_id' in r['rows'][0] and 'content' in r['rows'][0], r['rows'][0]
cid = r['rows'][0]['conversation_id']
print('search rows =', len(r['rows']), '| cid =', cid)

# member_names=[] 는 '멤버가 명시되지 않은 요청' → 빈 rows (mcp_server/sqlite_mcp_server.py:36-37)
empty = json.loads(m.search_previous_conversations.invoke({'query': 'QA 리뷰', 'member_names': [], 'limit': 5}))
assert empty['rows'] == [], 'member_names=[] 인데 rows가 비지 않음(wrapper가 인자를 변형했을 가능성)'
print('empty member_names rows =', empty['rows'])

lo = json.loads(m.load_conversation_messages.invoke({'conversation_id': cid}))
print('load keys =', sorted(lo))
assert lo['ok'] is True and lo['tool_name'] == 'load_conversation_messages', lo
assert isinstance(lo['rows'], list) and lo['rows'], 'rows가 비어 있음'
first = lo['rows'][0]
for key in ('sender', 'content', 'created_at'):
    assert key in first, 'load rows에 ' + key + ' 가 없음 (가공 금지 규칙 위반): ' + str(first)
created = [row['created_at'] for row in lo['rows']]
assert created == sorted(created), 'created_at 순서가 보존되지 않음(wrapper가 재정렬함)'
print('load rows =', len(lo['rows']), '| first sender =', first['sender'])

ex = json.loads(m.extract_schedules_from_history.invoke({'member_names': ['철수','영희'], 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))
print('extract keys =', sorted(ex))
assert ex['ok'] is True and ex['tool_name'] == 'extract_schedules_from_history', ex
assert ex['rows'], 'July fixture 범위인데 rows가 비어 있음'
for key in ('member_name','title','date','start_time','end_time','notes'):
    assert key in ex['rows'][0], 'extract rows에 ' + key + ' 누락 (가이드 line 85): ' + str(ex['rows'][0])
assert isinstance(ex.get('schedule_summary'), str) and ex['schedule_summary'], 'schedule_summary 누락'
assert {row['member_name'] for row in ex['rows']} <= {'철수','영희'}, '멤버 필터가 반영되지 않음'
print('extract rows =', len(ex['rows']))

# 날짜 범위 밖은 비어야 한다 — '빈 값으로 통과'의 반대축(필터가 실제로 걸리는지)
out = json.loads(m.extract_schedules_from_history.invoke({'member_names': ['철수'], 'date_from': '2026-01-01', 'date_to': '2026-01-31'}))
assert out['rows'] == [], '범위 밖인데 rows가 비지 않음: ' + str(out['rows'])[:120]
print('MCP_ROUNDTRIP_OK')
"
```
확인 포인트: 세 wrapper 모두 MCP 서버가 만든 `ok`/`tool_name`/`rows`(+`schedule_summary`) 계약을
**그대로** 전달한다. rows를 재정렬·재가공하면 FAIL(가이드 line 79·85).

## 6. 공유 일정 저장소 조회 (list_shared_schedules)
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
import student_parts.week05_load_kanas_past_conversations as m

d = json.loads(m.list_shared_schedules.invoke({}))
print('list keys =', sorted(d))
assert d['ok'] is True and d['tool_name'] == 'list_shared_schedules', d
assert d['rows'], '필터 없이 호출했는데 기본 공유 일정이 없음 (가이드 line 90)'
assert isinstance(d.get('schedule_summary'), str) and d['schedule_summary'], 'schedule_summary 누락'
print('default rows =', len(d['rows']), '| members =', sorted({r['member_name'] for r in d['rows']}))

f = json.loads(m.list_shared_schedules.invoke({'member_names': ['철수'], 'date_from': '2026-07-07', 'date_to': '2026-07-17', 'limit': 10}))
assert {r['member_name'] for r in f['rows']} == {'철수'}, '멤버 필터 미반영: ' + str(f['rows'])[:150]
assert len(f['rows']) <= 10, 'limit 미반영'
print('filtered rows =', len(f['rows']))
print('LIST_SHARED_OK')
"
```
확인 포인트: 필터 없으면 기본 실습 row, 필터를 주면 그대로 반영. `rows`와 `schedule_summary`가 유지된다.

## 7. 공유 일정 등록/삭제 왕복 (추가 과제 — 미구현이면 N/A)
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
import student_parts.week05_load_kanas_past_conversations as m

SRC = 'verify:week5:roundtrip'
c = json.loads(m.create_shared_schedule.invoke({'member_name': '나', 'title': '검증용 회의', 'date': '2026-07-09', 'start_time': '09:00', 'end_time': '10:00', 'notes': 'verify', 'source_conversation_id': SRC, 'schedule_id': 'verify_sch_1'}))
print('create keys =', sorted(c))
assert c['ok'] is True and c['tool_name'] == 'create_shared_schedule', c
shared = c['shared_schedule']
assert shared.get('schedule_id') == 'verify_sch_1', 'schedule_id 보존 실패 (가이드 line 105): ' + str(shared)
assert shared.get('source_conversation_id') == SRC, 'source_conversation_id 보존 실패: ' + str(shared)

seen = json.loads(m.list_shared_schedules.invoke({'source_conversation_id': SRC}))
assert len(seen['rows']) == 1 and seen['rows'][0]['title'] == '검증용 회의', '등록 row가 조회되지 않음: ' + str(seen['rows'])
print('after create rows =', seen['rows'])

d = json.loads(m.delete_shared_schedule.invoke({'source_conversation_id': SRC}))
print('delete keys =', sorted(d))
assert d['ok'] is True and d['tool_name'] == 'delete_shared_schedule', d
assert d['deleted_count'] == 1, 'deleted_count 불일치: ' + str(d)

gone = json.loads(m.list_shared_schedules.invoke({'source_conversation_id': SRC}))
assert gone['rows'] == [], '삭제 후에도 row가 남아 있음: ' + str(gone['rows'])
print('SHARED_CRUD_OK')
"
```
확인 포인트: 등록 → 조회에 나타남 → 삭제 → 사라짐. `schedule_id`/`source_conversation_id`가 보존된다
(가이드 line 105 — 이게 깨지면 Week 3 앱 동기화의 수정/삭제가 대상 row를 못 찾는다).
**추가 과제를 구현하지 않기로 했다면 이 단계는 N/A(범위 밖)로 기록한다.**

## 8. collect_member_schedules — 두 출처 통합 (Week 5 핵심)
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from dataclasses import replace
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
from fixed.config import CONFIG
from fixed.session_scope import conversation_session_scope
import fixed.app_store as store_mod
import student_parts.week01_wake_up_nana as w1
import student_parts.week05_load_kanas_past_conversations as m

m.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w1.PERSONAL_SCHEDULES[:] = []

# (a) SQLite에 저장된 내 일정
store = store_mod.AppSQLiteStore(_tmp / 'app.sqlite3')
store.save_structured_request({'kind': 'personal_schedule', 'title': '내 저장 일정', 'date': '2026-07-08', 'start_time': '14:00', 'end_time': '15:00'})

# (b) 현재 대화의 임시 일정
with conversation_session_scope('conv_now'):
    w1.personal_create_schedule.invoke({'title': '내 임시 일정', 'date': '2026-07-09', 'start_time': '16:00', 'end_time': '17:00', 'attendees': []})
    out = json.loads(m.collect_member_schedules.invoke({'member_names': ['철수','영희'], 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))

print('collect keys =', sorted(out))
rows = out['rows']
assert isinstance(rows, list) and rows, 'rows가 비어 있음'
for row in rows:
    for key in ('member_name','title','date','start_time','end_time','notes'):
        assert key in row, 'row에 ' + key + ' 누락 (가이드 line 96): ' + str(row)
assert isinstance(out.get('schedule_summary'), str) and out['schedule_summary'], 'schedule_summary 누락 (가이드 line 97)'
titles = {r['title'] for r in rows}
members = {r['member_name'] for r in rows}
print('members =', sorted(members))
assert '내 저장 일정' in titles, 'SQLite 저장 일정이 합쳐지지 않음'
assert '내 임시 일정' in titles, '현재 대화 임시 일정이 합쳐지지 않음'
assert {'철수','영희'} & members, '외부 멤버 일정이 합쳐지지 않음'
assert any(r['member_name'] == '나' for r in rows), '내 일정 row의 member_name이 \"나\"가 아님'
print('COLLECT_OK rows =', len(rows))
"
```
확인 포인트: 내 SQLite 일정 + 현재 대화 임시 일정 + 외부 멤버 일정이 **같은 6개 키 구조의 한 rows 배열**로
합쳐지고 `schedule_summary`가 함께 온다. 내 일정 row의 `member_name`은 `"나"`
(`fixed/external_people_store.py:21` `PERSONAL_SHARED_MEMBER_NAME`)여야 외부 row와 같은 축으로 읽힌다.

## 8-b. collect_member_schedules — 내 일정 사본 중복 차단 + 조회 조건 보고
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from dataclasses import replace
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
from fixed.config import CONFIG
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as w3
import student_parts.week05_load_kanas_past_conversations as m
m.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w3.CONFIG = m.CONFIG

store = store_mod.AppSQLiteStore(_tmp / 'app.sqlite3')
saved = store.save_structured_request({'kind':'personal_schedule','title':'내 개인 일정','date':'2026-07-08','start_time':'14:00','end_time':'15:00'})
assert (saved.get('shared_sync') or {}).get('ok'), '전제 실패: 공유 저장소 사본이 안 생겼다면 이 검사는 빈 값으로 통과한다 ' + str(saved.get('shared_sync'))

def collect(names):
    return json.loads(m.collect_member_schedules.invoke({'member_names': names, 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))

# (a) '나'가 들어와도 내 일정은 1건 — 앱 원본과 공유 사본이 겹쳐 잡히면 안 된다
out = collect(['나', '철수'])
mine = [r for r in out['rows'] if r['title'] == '내 개인 일정']
synced = [r for r in out['rows'] if r.get('notes') == '앱 개인 일정 자동 동기화']
print('내 일정 rows =', len(mine), '| 동기화 사본 rows =', len(synced))
assert len(mine) == 1 and not synced, '같은 일정이 앱 원본 + 공유 사본으로 중복됨: ' + str(mine + synced)

# (b) 반대 축 — 사본을 거르면서 외부 멤버 rows까지 깎으면 안 된다
only_ext = collect(['철수'])
a = [r for r in out['rows'] if r['member_name'] == '철수']
b = [r for r in only_ext['rows'] if r['member_name'] == '철수']
print('철수 rows =', len(a), 'vs', len(b))
assert a and a == b, '사본 제거가 외부 멤버 rows까지 깎음'

# (c) member_names=['나']만 와도 내 일정은 남는다 (전부 비우는 구현 차단)
solo = collect(['나'])
assert [r for r in solo['rows'] if r['title'] == '내 개인 일정'], 'member_names=[\"나\"]에서 내 일정이 사라짐'
print('solo rows =', len(solo['rows']))

# (d) 조회 조건이 결과에 남는가 — 0건 멤버가 '조회했는데 없음'인지 '조회 대상 아님'인지 구분
f = collect(['나', '철수', '설하']).get('filters') or {}
print('filters =', json.dumps(f, ensure_ascii=False))
assert f.get('requested_member_names') == ['나', '철수', '설하'], f
assert '나' in (f.get('external_member_names') or []), '사본 아닌 \"나\" 공유 row를 놓치지 않도록 조회 대상에 들어야 한다: ' + str(f)
assert '설하' in (f.get('external_member_names') or []), 'fixture에 없는 멤버도 조회했다는 사실이 남아야 한다'
assert f.get('date_from') == '2026-07-07' and f.get('date_to') == '2026-07-17', f
assert f.get('includes_personal_schedules') is True, f

# (e) 사본 아닌 '나' 공유 row는 살아 있어야 한다 — (a)를 '나 제외'로 해결하면 여기서 실패한다
m.create_shared_schedule.invoke({'member_name':'나','title':'수동 등록 회의','date':'2026-07-10','start_time':'13:00','end_time':'14:00'})
manual = [r for r in collect(['나','철수'])['rows'] if r['title'] == '수동 등록 회의']
print('수동 등록 rows =', manual)
assert len(manual) == 1, '앱 DB에 원본이 없는 \"나\" 공유 row가 busy-time에서 빠짐'
print('SELF_DEDUP_AND_FILTERS_OK')
" | grep -v "Processing request of type\|ListToolsRequest\|CallToolRequest\|server.py:"
```
확인 포인트: `member_names`에 `"나"`가 들어와도 내 일정이 **1건**이고 공유 사본(`notes="앱 개인 일정 자동 동기화"`)이 섞이지 않는다.
동시에 **(b)(c)(e)로 과잉 제거가 아님**을 확인한다 — 사본 제거가 외부 멤버·내 일정·사본 아닌 공유 row를 깎으면 실패다.
`filters`는 week03 `personal_list_saved_schedules`의 기존 키 관례를 따른다.

> ⚠️ 첫 줄의 `shared_sync` 단언이 **이 검사의 전제**다. 공유 사본이 애초에 안 생기면 (a)는
> "빈 값으로 통과한 검사"가 된다(`verifier.md` 검증 설계 원칙).

> **(d)(e)는 "나"를 외부 조회 대상에 넣는 설계를 전제로 한다.** 사본 중복은 입력 단계에서 `"나"`를
> 빼는 것으로도 막을 수 있지만, 그러면 `create_shared_schedule`로 직접 등록해 앱 DB에 원본이 없는
> `"나"` row가 어느 경로로도 안 잡혀 busy-time이 사라진다((e)가 그 축). 사본 중복은 응답 단계
> dedupe가 담당한다 — 공유 저장소는 제목의 소괄호를 지우고 빈 시각을 `"미정"`으로 바꾸므로
> 값을 그대로 비교하면 안 되고, `end_time`은 이 파일이 앱 DB 경로에서 바꾸지 않으므로 같은
> `"미정"` 정규화를 거쳐 키에 넣는다(빼면 9(c)의 별개 일정이 병합된다).

## 8-c. collect_member_schedules — 그룹 일정도 내 busy-time
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from dataclasses import replace
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
from fixed.config import CONFIG
import fixed.app_store as store_mod
import student_parts.week03_build_nanas_logbook as w3
import student_parts.week05_load_kanas_past_conversations as m
m.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w3.CONFIG = m.CONFIG
store_mod.AppSQLiteStore(_tmp / 'app.sqlite3').save_structured_request(
    {'kind':'group_schedule','title':'팀 워크샵','date':'2026-07-09','start_time':'15:00','end_time':'16:00','members':['철수']})
rows = json.loads(m.collect_member_schedules.invoke({'member_names':['철수'],'date_from':'2026-07-07','date_to':'2026-07-17'}))['rows']
mine = [r for r in rows if r['title'] == '팀 워크샵' and r['member_name'] == '나']
print('내 busy-time 그룹 일정 =', mine)
assert len(mine) == 1, '내가 잡아둔 그룹 일정이 busy-time에서 빠짐 — kind 필터를 새로 걸면 여기서 실패한다'
print('GROUP_BUSY_OK')
" | grep -v "Processing request of type\|ListToolsRequest\|CallToolRequest\|server.py:"
```
확인 포인트: 앱 `schedules` 테이블은 모든 row가 `owner='me'`이므로(`fixed/app_store.py:92`·`:358`)
`personal_schedule`과 `group_schedule`을 구분하지 않고 **모두 내 busy-time으로 본다**.
`list_schedules`에 `kind='personal_schedule'`을 넣는 순간 내가 잡아둔 회의가 busy-time에서 사라지고,
Week 6이 이미 일정이 있는 시각을 "가능"으로 제안하게 된다.

## 9. 안전규칙 — 중복 제거 + 대화 범위 격리
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from dataclasses import replace
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
from fixed.config import CONFIG
from fixed.session_scope import conversation_session_scope
import student_parts.week01_wake_up_nana as w1
import student_parts.week03_build_nanas_logbook as w3
import student_parts.week05_load_kanas_past_conversations as m

m.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w3.CONFIG = m.CONFIG
w1.PERSONAL_SCHEDULES[:] = []

# (a) 중복 제거 — **실제 앱 경로로** 잰다. Week 5 agent가 노출하는 personal_create_schedule은
#     week01판이 아니라 week03판이다(week03_build_nanas_logbook.py:677이 같은 이름으로 교체).
#     이 tool 한 번 호출이 임시 일정 id='personal_<hex>' 와 SQLite schedule_id='sch_<sha1>'(내용 해시,
#     week03_build_nanas_logbook.py:499 -> :286)를 **동시에** 만든다 → 두 식별자는 구조적으로 겹치지 않는다.
#     가이드 line 128을 글자 그대로 id 비교로만 구현하면 여기서 조용히 2건이 된다.
with conversation_session_scope('conv_now'):
    w3.personal_create_schedule.invoke({'title': '중복 후보', 'date': '2026-07-10', 'start_time': '11:00', 'end_time': '12:00', 'attendees': []})
    out = json.loads(m.collect_member_schedules.invoke({'member_names': ['철수'], 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))
dup = [r for r in out['rows'] if r['title'] == '중복 후보']
print('중복 후보 rows =', len(dup))
assert len(dup) == 1, '같은 일정이 SQLite/임시 양쪽에서 중복 합산됨 (가이드 line 98·128): ' + str(dup)

# (b) 대화 범위 격리: 다른 대화의 임시 일정은 현재 대화 rows에 섞이면 안 된다
w1.PERSONAL_SCHEDULES[:] = []
with conversation_session_scope('conv_other'):
    w1.personal_create_schedule.invoke({'title': '남의 대화 일정', 'date': '2026-07-11', 'start_time': '09:00', 'end_time': '10:00', 'attendees': []})
with conversation_session_scope('conv_now'):
    out2 = json.loads(m.collect_member_schedules.invoke({'member_names': ['철수'], 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))
leaked = [r for r in out2['rows'] if r['title'] == '남의 대화 일정']
print('누수 rows =', leaked)
assert leaked == [], '다른 대화 범위의 임시 일정이 섞임 (가이드 line 94·98)'

# (c) 과잉 dedup 음성 대조 — dedup이 '너무 많이' 지우지 않는지. (a)만 있으면 '전부 1건으로 합치기'도 통과한다.
#     제목·날짜·시작시각이 같고 종료시각만 다른 두 일정은 서로 다른 busy-time이므로 2건으로 남아야 한다.
#     내용 키를 직접 만들지 않고 Week 3 저장 경로와 같은 helper로 키를 얻으면 이 검사가 통과한다.
w1.PERSONAL_SCHEDULES[:] = []
with conversation_session_scope('conv_now'):
    w3.personal_create_schedule.invoke({'title': '겹침 시험', 'date': '2026-07-13', 'start_time': '10:00', 'end_time': '11:00', 'attendees': []})
    w1.personal_create_schedule.invoke({'title': '겹침 시험', 'date': '2026-07-13', 'start_time': '10:00', 'end_time': '12:00', 'attendees': []})
    out3 = json.loads(m.collect_member_schedules.invoke({'member_names': ['철수'], 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))
same = [r for r in out3['rows'] if r['title'] == '겹침 시험']
print('종료시각만 다른 별개 일정 rows =', [(r['start_time'], r['end_time']) for r in same])
assert len(same) == 2, '별개 일정이 과잉 dedup으로 합쳐짐: ' + str(same)
print('SAFETY_OK')
"
```
확인 포인트: (a) SQLite에 이미 저장된 일정과 Week 1 임시 일정이 **한 번만** 계산된다 —
중복되면 Week 6의 공통 가능 시간 계산이 없는 busy-time을 만든다. (b) 다른 대화 임시 일정은 제외된다.
(c) 반대로 **과잉 dedup도 결함**이다 — 별개 일정을 합치면 있어야 할 busy-time이 사라진다.

> **왜 (c)가 필요한가:** dedup 키를 학생 파일에서 새로 만들면(예: `(title, date, start_time)`만) (a)는 통과하지만
> (c)에서 걸린다. `_content_schedule_id`/`_ensure_content_dedup_key`(week03_build_nanas_logbook.py:254·289)가
> **DB의 `schedule_id`를 실제로 만들어낸 함수**이므로, 저장 경로와 같은 helper로 키를 되짚는 구현만이 정확히 일치한다
> (kanana-conventions §5 "이미 존재하는 helper를 다시 구현하지 않는다"와 결과가 일치하는 지점).

## 10. 책임 경계 정적 대조
```bash
uv run python -X utf8 -c "
import ast, inspect, re
import student_parts.week05_load_kanas_past_conversations as m
src = inspect.getsource(m)
for bad, why in [
    (r'\bimport sqlite3\b', '학생 파일에서 직접 SQL 금지 (가이드 line 109)'),
    (r'\bconn\.execute\(', '학생 파일에서 직접 SQL 금지 (가이드 line 109)'),
    (r'except\s+Exception\s*:\s*\n\s*(pass|\.\.\.)', '예외 삼킴 금지'),
]:
    hits = re.findall(bad, src)
    assert not hits, why + ' — 발견: ' + str(hits)
# 정규화는 경계에서 한 번만 — wrapper가 자체 alias/날짜 파싱 helper를 새로 두면 안 된다(가이드 line 74·84·109)
assert 'EXTERNAL_MEMBER_ALIAS' not in src, '멤버 이름 정규화를 wrapper에서 중복 구현함'
# 미구현 placeholder는 **실행되는 Ellipsis 문장**만 잡는다. 문자열 검색(`'...' not in src`)은 쓰지 마라 —
# 스캐폴드 가이드 주석이 'collect_member_schedules(...)'처럼 (...) 표기를 쓰므로 어떤 완벽한 구현도
# 통과할 수 없고, 검사가 신호를 전혀 주지 못한다(Week 5 1차 검증에서 실측된 skill 결함).
placeholders = [
    node.lineno for node in ast.walk(ast.parse(src))
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis
]
assert not placeholders, '미구현 placeholder(...) 문장이 남아 있음: line ' + str(placeholders)
print('BOUNDARY_OK')
"
```
확인 포인트: 학생 파일에 **직접 SQL·중복 정규화·예외 삼킴·미구현 placeholder**가 없다.
`search_previous_conversations`/`extract_schedules_from_history` wrapper는 인자를 그대로 넘긴다
(정규화는 store/MCP 경계에서 한 번 — 가이드 line 74·84).

## 11. (선택, 키 필요) agent 조립
```bash
uv run python -X utf8 -c "
from fixed.config import CONFIG
if not CONFIG.has_openai_key:
    print('SKIP: PROXY_TOKEN 없음 — agent 조립은 키가 필요하다.')
else:
    import student_parts.week05_load_kanas_past_conversations as m
    agent = m.build_week05_agent()
    print('agent =', type(agent).__name__)
    print('AGENT_OK')
"
```
확인 포인트: `build_week05_agent()`가 `week05_tools()`/`week05_system_prompt()`으로 조립되고 예외 없이 만들어진다.
**tool 라우팅(어떤 질문에 어떤 MCP tool을 고르는가)은 이 skill의 범위가 아니다** —
확률적 행동은 `evals/week05_eval.py`가 통과율로 잰다.

---

## 보고 형식

각 단계 → `PASS` / `FAIL` / `N/A(사유)` + 실행 원문 출력. FAIL은
**구현 결함**인지 **assertion이 과하게 좁은 skill 문제**인지 구분해 적는다. 코드는 고치지 않는다.
