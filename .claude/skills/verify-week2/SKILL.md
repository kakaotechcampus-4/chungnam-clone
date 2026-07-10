---
name: verify-week2
description: Week 2 구현(student_parts/week02_structure_natural_language_requests.py)을 검증한다. 메인과제(StructuredRequest/StructuredRequestBatch 스키마, week02_tools)와 추가 과제(_coerce_structured_request/extract_structured_request/extract_schedule_request bridge)를 모두 다룬다. py_compile, 모듈 import, 스키마 필드·기본값·description, bridge 3분기·반환 JSON 계약을 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 2 검증 (Verification)

`PROXY_TOKEN` 없이 실행 가능한 **정적 검증(1~5단계)**을 먼저 수행한다. 키가 있으면 **실경로 검증(6단계)**까지 한다.
각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. **코드는 수정하지 않는다.**

명령은 모두 `uv run python -X utf8`로 시작한다. `-X utf8`은 Windows 콘솔 코드페이지와 무관하게
한글 출력을 보존한다. `PYTHONIOENCODING=...` 접두어를 붙이면 `allowed-tools: Bash(uv *)` 패턴에서
벗어나 불필요한 권한 프롬프트가 뜨므로 쓰지 않는다.

셸이 `\u` 리터럴을 이스케이프 시퀀스로 해석해 `SyntaxError`를 내므로, 이스케이프 검사에는
`chr(92) + 'u'`를 쓴다.

---

## 1. 구문 검사 (py_compile)
```bash
uv run python -m py_compile student_parts/week02_structure_natural_language_requests.py
```

## 2. import + 스키마 인스턴스화 스모크 테스트 (메인과제)
```bash
uv run python -X utf8 -c "import student_parts.week02_structure_natural_language_requests as m; r=m.StructuredRequest(kind='personal_schedule'); b=m.StructuredRequestBatch(requests=[r]); print('kind=', r.kind, '| title=', r.title, '| members=', r.members, '| original_text=', repr(r.original_text)); print('batch.base_date=', b.base_date, '| len(requests)=', len(b.requests)); print('week02_tools len=', len(m.week02_tools())); assert m.StructuredRequestBatch().requests == []; print('OK')"
```
확인 포인트: `kind` 필수 동작, `title=None`, `members=[]`, `original_text=''`, `base_date`가 오늘 날짜, `week02_tools()` 길이 3.

## 3. 필드 스펙 대조 (메인과제)
```bash
uv run python -X utf8 -c "import student_parts.week02_structure_natural_language_requests as m; f=m.StructuredRequest.model_fields; print({k:(str(v.annotation), v.is_required(), bool(v.description)) for k,v in f.items()}); print({k:(str(v.annotation), v.is_required(), bool(v.description)) for k,v in m.StructuredRequestBatch.model_fields.items()})"
```
확인 포인트: `kind`만 `is_required=True`, 나머지 필드는 기본값 보유, 모든 필드에 description 존재.

## 4. bridge — `_coerce_structured_request` 3분기 (추가 과제)
```bash
uv run python -X utf8 -c "
import student_parts.week02_structure_natural_language_requests as m
sr = m.StructuredRequest(kind='todo')
assert m._coerce_structured_request(sr) is sr, 'identity FAIL'
assert m._coerce_structured_request({'kind': 'todo'}).kind == 'todo', 'dict FAIL'
for bad in ('문자열', 123, None, ['x']):
    try:
        m._coerce_structured_request(bad)
    except RuntimeError as exc:
        print(f'  {type(bad).__name__:5} -> RuntimeError: {exc}')
    else:
        raise AssertionError(f'{bad!r}: RuntimeError가 발생하지 않음 (조용한 통과)')
print('COERCE_OK')
"
```
확인 포인트: 이미 `StructuredRequest`면 **동일 객체(identity)** 반환, dict는 검증 통과,
그 외 타입은 전부 `RuntimeError`. 조용히 `None`을 반환하면 FAIL이다.

## 5. bridge — 반환 JSON 계약과 격리 (추가 과제)

`extract_structured_request`를 monkeypatch해 **LLM 호출 없이** tool 반환 계약을 검사한다.
`structured_request`의 키가 `fixed/app_store.py`의 `save_structured_request(payload)`가
소비하는 필드를 모두 포함해야 Week 3 저장 tool이 payload를 그대로 받을 수 있다.

```bash
uv run python -X utf8 -c "
import json
import student_parts.week02_structure_natural_language_requests as m
m.extract_structured_request = lambda text: m.StructuredRequest(kind='personal_schedule', title='회의', members=['철수'], original_text=text)
out = m.extract_schedule_request.invoke({'query': '내일 회의'})
assert chr(92) + 'u' not in out, 'ensure_ascii=False FAIL: 한글이 이스케이프됨'
data = json.loads(out)
assert set(data) == {'ok', 'tool_name', 'base_date', 'structured_request'}, f'키 불일치: {sorted(data)}'
assert data['ok'] is True and data['tool_name'] == 'extract_schedule_request'
assert data['base_date'] == m.current_app_date_iso()
consumed = {'kind','title','date','start_time','end_time','members','priority','reason'}
missing = consumed - set(data['structured_request'])
assert not missing, f'save_structured_request 소비 필드 누락: {missing}'
print('keys      =', sorted(data)); print('base_date =', data['base_date'])
print('TOOL_CONTRACT_OK')
"
```

bridge는 Week 2 agent에 노출되는 tool이 **아니다**(가이드 line 47-48). 격리와 소스 규약을 함께 본다.

```bash
uv run python -X utf8 -c "
import inspect
import student_parts.week02_structure_natural_language_requests as m
names = [t.name for t in m.week02_tools()]
assert names == ['personal_create_schedule', 'personal_list_schedules', 'personal_delete_schedule'], names
assert 'extract_schedule_request' not in names, 'bridge tool이 week02_tools()에 노출됨'
src = inspect.getsource(m.extract_structured_request)
assert 'week02_prompt_parts()' in src, 'week02_prompt_parts() 미사용'
assert 'week02_system_prompt' not in src, 'week02_system_prompt()를 쓰면 안 됨'
assert 'create_agent' not in src, 'bridge는 agent loop를 만들지 않는다'
assert 'function_calling' in src, 'method=function_calling 누락'
src2 = inspect.getsource(m.extract_schedule_request.func)
assert 'except' not in src2, 'try/except로 예외를 삼키면 안 됨'
assert 'ensure_ascii=False' in src2, 'ensure_ascii=False 누락'
print('week02_tools =', names); print('BRIDGE_ISOLATION_OK')
"
```

## 6. 실경로 LLM 검증 (`.env`에 `PROXY_TOKEN`이 있을 때만)

키가 없으면 이 단계는 **N/A(사유: PROXY_TOKEN 없음)**로 기록하고 넘어간다.
`uv run python -X utf8 -c "from fixed.config import CONFIG; print(CONFIG.has_openai_key)"`로 먼저 확인한다.

가이드 line 116-117은 "Week 3 실행 후 trace 확인"을 요구하지만 `student_parts/week03_*.py`는
아직 존재하지 않는다. 아래가 Week 3 없이 bridge를 확인하는 대체 시나리오다.

```bash
uv run python -X utf8 -c "
import json
import student_parts.week02_structure_natural_language_requests as m
sr = m.extract_structured_request('조만간 팀 회고 한번 하자')
print('불충분 입력 ->', sr.kind, sr.date, sr.start_time, sr.end_time, sr.members)
assert (sr.date, sr.start_time, sr.end_time, sr.members) == (None, None, None, []), '없는 값을 지어냄'
payload = json.dumps({'ok': True, 'tool_name': 'personal_create_schedule', 'created_schedule': {'title': '치과 진료', 'date': '2026-07-15', 'start_time': '10:00', 'end_time': '미정', 'attendees': ['철수', '영희']}}, ensure_ascii=False)
data = json.loads(m.extract_schedule_request.invoke({'query': payload}))['structured_request']
print('Week1 JSON ->', data['members'], data['end_time'])
assert data['members'] == ['철수', '영희'], 'attendees -> members 이동 실패'
assert data['end_time'] is None, '미정은 HH:MM이 아니므로 None이어야 함'
print('LIVE_OK')
"
```
확인 포인트:
- **값을 지어내지 않는다** — 정보가 불충분하면 `date/start_time/end_time`은 `None`, `members`는 `[]`.
  `00:00` 같은 기본값이 채워지면 FAIL.
- Week 1 tool JSON 입력에서 `attendees`가 `members`로 옮겨지고, `end_time="미정"`은 `None`이 된다.
  **`attendees`는 반드시 비어 있지 않은 값으로 시험한다** — 빈 리스트로는 이동 로직이 검증되지 않는다.

---

## 보고

**이 skill은 절차(무엇을 어떤 명령으로 실행할지)만 규정한다. 출력 형식은 규정하지 않는다.**
호출자의 지시가 항상 우선한다:
- verifier subagent가 preload로 실행할 때 → `verifier.md`의 "반환 형식"을 따른다.
- 사용자가 프롬프트로 특정 형식을 요구하면 → 그 요구를 먼저 만족시킨다.
- `/verify-week2`로 직접 호출되어 다른 지시가 없을 때만 아래 기본값을 쓴다.

기본값:
- 각 단계 명령 + 원문 출력 + PASS/FAIL.
- 실패 항목은 무엇이·왜 어긋났는지 근거(`file:line`)와 함께 명시.
- 1~5단계 전부 통과할 때만 "정적 검증 통과". 6단계를 건너뛰었으면 그 사실을 결론에 남긴다.
