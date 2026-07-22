---
name: verify-week4
description: Week 4 구현(student_parts/week04_retrieve_nanas_memory.py)을 검증한다. 1회차(add_personal_reference/search_personal_references/search_saved_requests tool과 add_personal_reference_dict/search_personal_reference_hits/search_saved_request_rows helper, hits/rows top-level 계약)와 2회차(search_conversation_messages/search_nana_memory tool, search_conversation_messages_dict/rows helper, conversation RAG lazy sync·현재 대화 제외, hits/rows/context/rag_backend/sync 계약, week04_tools/build_week04_agent 배선, week04_prompt_parts 3출처 지침)를 모두 다룬다. py_compile, 모듈 import, 스키마 필드·기본값·bounds·description, 반환 JSON 계약, temp SQLite 왕복(search_saved_requests)을 키 없이 확인하고, 키가 있으면 ChromaDB RAG 실경로(개인 참고자료 add/search, conversation 검색)까지 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 4 검증 (Verification)

`PROXY_TOKEN` 없이 실행 가능한 **정적 검증(1~7단계)**을 먼저 수행한다. 키가 있으면 **RAG 실경로 검증(8단계)**까지 한다.
각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. **코드는 수정하지 않는다.**

> **Phase A 뼈대 (구현 전 작성).** 이 skill은 대상 파일의 `[4주차 1회차/2회차 수강생 구현 가이드]`와
> 읽기 전용 `fixed/reference_store.py`·`fixed/conversation_rag_store.py`·`fixed/app_store.py` 계약에서
> 유도했다. builder 구현 후 verifier가 실행해 1~8단계를 확정한다. 실행 중 실패가 나면 두 부류로 구분한다:
> **가이드가 못박은 계약 위반 → FAIL(구현 결함)**, **valid 구현인데 assertion이 과하게 좁아 실패 → skill 완화 후보**(코드는 고치지 않는다).
> 특히 hit의 `role`·저장 결과 wrapping 키처럼 가이드가 이름을 안 못박은 부분은 단언하지 않는다.

명령은 모두 `uv run python -X utf8`로 시작한다. `-X utf8`은 Windows 콘솔 코드페이지와 무관하게
한글 출력을 보존한다. `PYTHONIOENCODING=...` 접두어를 붙이면 `allowed-tools: Bash(uv *)` 패턴에서
벗어나 불필요한 권한 프롬프트가 뜨므로 쓰지 않는다.

## 왜 RAG 왕복은 키가 필요한가

`search_personal_references`·`add_personal_reference`·`search_conversation_messages`는 ChromaDB에
문서/쿼리를 넣을 때 **OpenAI embedding proxy**(PROXY_TOKEN)를 호출한다. 따라서 이 세 tool의 실왕복은
8단계(키 필요)에서만 확인한다. **키 없이 결정적으로 확인 가능한 것**은 `search_saved_requests`(SQLite LIKE,
임베딩 불필요)와 모든 스키마/배선/반환 계약이다.

## 격리 하네스 (store를 건드리는 단계 공통)

Week 4 tool은 모듈 전역 `REFERENCE_STORE`/`SQLITE_STORE`/`CONVERSATION_RAG_STORE`를 직접 참조한다.
검증이 앱 DB(`data/kanana_app.sqlite3`)나 실 ChromaDB(`CONFIG.chroma_dir`)를 오염시키지 않도록,
**이 전역들을 임시 경로 인스턴스로 monkeypatch**한다.

```python
# (참고용 하네스 — 각 단계에 인라인됨)
import tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week04_retrieve_nanas_memory as m
_tmp = Path(tempfile.mkdtemp())
m.SQLITE_STORE = store_mod.AppSQLiteStore(_tmp / 'app.sqlite3')   # SQLite tool이 이 임시 DB를 쓴다
# 8단계(키 필요)에서는 REFERENCE_STORE/CONVERSATION_RAG_STORE도 임시 chroma_dir로 재바인딩한다:
#   m.REFERENCE_STORE = PersonalReferenceStore(_tmp / 'chroma')
#   m.CONVERSATION_RAG_STORE = ConversationRAGStore(_tmp / 'chroma')
```

tool 본문이 이 전역들을 인자로 helper에 넘기므로, 전역을 교체하면 모든 tool 경로에 적용된다.

---

## 1. 구문 검사 (py_compile)
```bash
uv run python -m py_compile student_parts/week04_retrieve_nanas_memory.py
```

## 2. import + 스키마 인스턴스화 스모크 테스트
```bash
uv run python -X utf8 -c "import student_parts.week04_retrieve_nanas_memory as m; a=m.AddPersonalReferenceInput(title='t', content='c'); print('add tags default=', a.tags); s=m.SearchPersonalReferencesInput(query='q'); print('ref top_k=', s.top_k); r=m.SearchSavedRequestsInput(query='q'); print('req top_k=', r.top_k); cm=m.SearchConversationMessagesInput(query='q'); print('conv top_k=', cm.top_k, '| conv_id=', cm.conversation_id); nm=m.SearchNanaMemoryInput(query='q'); print('mem limit=', nm.limit); print('tools len=', len(m.week04_tools())); print('OK')"
```
확인 포인트: `AddPersonalReferenceInput(title,content)`가 `tags=None`으로 생성, `SearchPersonalReferencesInput.top_k` 기본 2, `SearchSavedRequestsInput.top_k` 기본 3, `SearchConversationMessagesInput.top_k` 기본 5·`conversation_id=None`, `SearchNanaMemoryInput.limit` 기본 5, `week04_tools()` 길이 = week03_tools()+4.

## 3. 필드 스펙 대조 (bounds 포함)
```bash
uv run python -X utf8 -c "import student_parts.week04_retrieve_nanas_memory as m; [print(n, {k:(str(v.annotation), v.is_required()) for k,v in c.model_fields.items()}) for n,c in [('AddPersonalReferenceInput',m.AddPersonalReferenceInput),('SearchPersonalReferencesInput',m.SearchPersonalReferencesInput),('SearchSavedRequestsInput',m.SearchSavedRequestsInput),('SearchConversationMessagesInput',m.SearchConversationMessagesInput)]]; f=m.SearchPersonalReferencesInput.model_fields['top_k']; g=m.SearchSavedRequestsInput.model_fields['top_k']; print('ref bounds', [ (x.__class__.__name__) for x in f.metadata]); print('req bounds', [ (x.__class__.__name__) for x in g.metadata])"
```
확인 포인트: 스키마 필드/타입이 파일 상단 정의(top_k `ge=1,le=20`/`le=50`, conversation_id `str|None`)와 일치.

## 4. tool 목록·배선 대조
```bash
uv run python -X utf8 -c "
import inspect
import student_parts.week04_retrieve_nanas_memory as m
names = [t.name for t in m.week04_tools()]
print('week04_tools =', names)
w3 = [t.name for t in __import__('student_parts.week03_build_nanas_logbook', fromlist=['week03_tools']).week03_tools()]
expected_tail = ['add_personal_reference','search_personal_references','search_saved_requests','search_conversation_messages']
assert names[:len(w3)] == w3, f'week03 tool 누적이 깨짐: {names[:len(w3)]}'
assert names[len(w3):] == expected_tail, f'week4 tool 누적 순서 불일치: {names[len(w3):]}'
src = inspect.getsource(m.build_week04_agent)
assert 'create_agent' in src and 'week04_tools' in src and 'week04_system_prompt' in src, 'build_week04_agent 배선 누락'
assert '_WEEK04_AGENT' in src, '싱글턴 캐시(_WEEK04_AGENT) 미사용'
parts = m.week04_prompt_parts()
w3p = __import__('student_parts.week03_build_nanas_logbook', fromlist=['week03_prompt_parts']).week03_prompt_parts()
assert parts[:len(w3p)] == w3p, 'week03_prompt_parts 누적이 깨짐'
assert len(parts) > len(w3p), 'Week4 prompt part가 추가되지 않음'
blob = ' '.join(parts[len(w3p):])
# 3출처 구분 지침이 프롬프트에 담겼는지(문구는 자유, 정보는 필수)
print('week4 prompt part chars =', len(blob))
print('WIRING_OK')
"
```
확인 포인트: week03 tool/prompt 누적 유지, week4 tool 4개가 순서대로 추가, `build_week04_agent`가 `create_agent`+`week04_tools`+`week04_system_prompt`+싱글턴으로 배선, `week04_prompt_parts`에 새 조각 존재. (프롬프트 **문구**는 자유이므로 특정 문자열을 단언하지 않는다 — 3출처 지침 포함 여부는 8단계 라우팅/eval로 판정.)

## 5. helper 단위 — safe_limit 경계
```bash
uv run python -X utf8 -c "import student_parts.week04_retrieve_nanas_memory as m; print(m.safe_limit(0), m.safe_limit(999,maximum=50), m.safe_limit('3'), m.safe_limit(None, default=5)); assert m.safe_limit(0)==1 and m.safe_limit(999,maximum=50)==50 and m.safe_limit('3')==3 and m.safe_limit(None,default=5)==5; print('SAFE_LIMIT_OK')"
```
확인 포인트: 1 미만은 1로, maximum 초과는 maximum으로, 문자열 숫자는 int, 비정상값은 default.

## 6. SQLite 저장 요청 검색 왕복 (temp DB, 키 불필요)
```bash
uv run python -X utf8 -c "
import json, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week04_retrieve_nanas_memory as m
_tmp = Path(tempfile.mkdtemp())
m.SQLITE_STORE = store_mod.AppSQLiteStore(_tmp / 'app.sqlite3')

# 저장 요청 2건 seed (fixed AppSQLiteStore 직접 사용 — 임베딩 불필요)
m.SQLITE_STORE.save_structured_request({'kind':'personal_schedule','title':'치과 진료','date':'2026-03-20','start_time':'10:00'})
m.SQLITE_STORE.save_structured_request({'kind':'personal_schedule','title':'팀 회의','date':'2026-03-21','start_time':'11:00'})

# search_saved_requests: top-level rows 계약
out = m.search_saved_requests.invoke({'query':'치과','top_k':3})
assert chr(92)+'u' not in out, 'ensure_ascii=False FAIL: 한글 이스케이프됨'
res = json.loads(out)
assert 'rows' in res, f'top-level rows 키 누락: {sorted(res)}'
assert isinstance(res['rows'], list) and len(res['rows']) >= 1, f'치과 검색 결과 없음: {res[\"rows\"]}'
assert any('치과' in (row.get('title') or '') for row in res['rows']), '검색 결과에 대상 title 없음'
print('search rows =', len(res['rows']))

# 결과 없음 → rows=[] (예외 금지)
empty = json.loads(m.search_saved_requests.invoke({'query':'존재하지않는키워드zzz','top_k':3}))
assert empty.get('rows') == [], f'무매칭인데 rows=[] 아님: {empty}'
print('empty rows =', empty['rows'])

# helper 직접: search_saved_request_rows 는 list 반환
rows = m.search_saved_request_rows(m.SQLITE_STORE, query='팀', top_k=3)
assert isinstance(rows, list) and any('팀' in (r.get('title') or '') for r in rows), 'helper 결과 이상'
print('SAVED_REQUESTS_OK')
"
```
확인 포인트: `search_saved_requests` top-level `rows`(빈 결과 `[]`, 예외 금지), 비어 있지 않은 실데이터로 왕복 성립, helper는 list 반환. `search_saved_request_rows`가 `AppSQLiteStore.search_saved_requests(query, limit)`를 호출.

## 7. 정적 대조 — helper가 fixed 계약을 쓰는지 (Grep/Read 보조)
```bash
uv run python -X utf8 -c "
import inspect
import student_parts.week04_retrieve_nanas_memory as m
# add_personal_reference_dict → reference_store.add_personal_reference 호출
assert 'add_personal_reference' in inspect.getsource(m.add_personal_reference_dict)
# search_personal_reference_hits → search_personal_references 호출 + metadata 재구성
sph = inspect.getsource(m.search_personal_reference_hits)
assert 'search_personal_references' in sph and 'metadata' in sph, 'hit metadata 재구성 누락'
# search_conversation_messages_dict → sync_from_sqlite + search + 현재 대화 제외
scm = inspect.getsource(m.search_conversation_messages_dict)
assert 'sync_from_sqlite' in scm and 'search' in scm, 'conversation lazy sync/search 누락'
assert 'current_session_scope' in scm or 'exclude_conversation_id' in scm, '현재 대화 제외 규칙 누락'
print('STATIC_OK')
"
```
확인 포인트: 각 helper가 지정된 fixed 메서드를 실제로 호출하고(placeholder `...` 잔존이 아님), conversation helper가 lazy sync + 현재 대화 제외 규칙을 구현. **미구현(`...`)이면 여기서 잡힌다.**

## 8. RAG 실경로 검증 (`.env`에 `PROXY_TOKEN`이 있을 때만)

키가 없으면 이 단계는 **N/A(사유: PROXY_TOKEN 없음)**로 기록하고 넘어간다.
`uv run python -X utf8 -c "from fixed.config import CONFIG; print(CONFIG.has_openai_key)"`로 먼저 확인한다.

```bash
uv run python -X utf8 -c "
import json, tempfile
from pathlib import Path
from fixed.config import CONFIG
if not CONFIG.has_openai_key:
    print('N/A: PROXY_TOKEN 없음'); raise SystemExit(0)
from fixed.reference_store import PersonalReferenceStore
from fixed.conversation_rag_store import ConversationRAGStore
import fixed.app_store as store_mod
import student_parts.week04_retrieve_nanas_memory as m
_tmp = Path(tempfile.mkdtemp())
m.SQLITE_STORE = store_mod.AppSQLiteStore(_tmp / 'app.sqlite3')
m.REFERENCE_STORE = PersonalReferenceStore(_tmp / 'chroma')
m.CONVERSATION_RAG_STORE = ConversationRAGStore(_tmp / 'chroma')

# add_personal_reference → reference_backend + reference
added = json.loads(m.add_personal_reference.invoke({'title':'집중 시간','content':'오전 9-11시에 집중이 잘 된다','tags':['preference']}))
assert 'reference_backend' in added and 'reference' in added, f'add 반환 계약 위반: {sorted(added)}'
print('add keys =', sorted(added))

# search_personal_references → top-level hits + metadata(title/tags)
hits = json.loads(m.search_personal_references.invoke({'query':'집중이 잘 되는 시간','top_k':2}))
assert 'hits' in hits and isinstance(hits['hits'], list), f'top-level hits 계약 위반: {sorted(hits)}'
if hits['hits']:
    h = hits['hits'][0]
    assert set(('id','content','distance','metadata')) <= set(h), f'hit 구조 위반: {sorted(h)}'
    assert 'title' in h['metadata'] and 'tags' in h['metadata'], f'metadata(title/tags) 누락: {sorted(h[\"metadata\"])}'
print('ref hits =', len(hits['hits']))

# search_conversation_messages → hits+rows+context+rag_backend+sync, 현재 대화 제외
out = json.loads(m.search_conversation_messages.invoke({'query':'회의','top_k':3}))
assert set(('hits','rows','context','rag_backend','sync')) <= set(out), f'conversation 반환 계약 위반: {sorted(out)}'
assert out['hits'] == out['rows'], 'hits와 rows가 같은 결과가 아님'
print('conv keys =', sorted(out), '| sync =', out['sync'])
print('RAG_REALPATH_OK')
"
```
확인 포인트: `add_personal_reference`는 `reference_backend`+`reference`, `search_personal_references`는 top-level `hits`(각 hit `id/content/distance/metadata`, metadata에 `title`/`tags`), `search_conversation_messages`는 `hits`/`rows`(동일)/`context`/`rag_backend`/`sync`. **출처별 라우팅**(질문 성격→맞는 tool)은 이 skill이 아니라 `evals/week04_eval.py`에서 통과율로 판정한다.

---

## 보고

**이 skill은 절차(무엇을 어떤 명령으로 실행할지)만 규정한다. 출력 형식은 규정하지 않는다.**
호출자의 지시가 항상 우선한다:
- verifier subagent가 preload로 실행할 때 → `verifier.md`의 "반환 형식"을 따른다.
- 사용자가 프롬프트로 특정 형식을 요구하면 → 그 요구를 먼저 만족시킨다.
- `/verify-week4`로 직접 호출되어 다른 지시가 없을 때만 아래 기본값을 쓴다.

기본값:
- 각 단계 명령 + 원문 출력 + PASS/FAIL.
- 실패 항목은 무엇이·왜 어긋났는지 근거(`file:line`)와 함께 명시.
- 1~7단계 전부 통과할 때만 "정적 검증 통과". 8단계를 건너뛰었으면(키 없음) 그 사실을 결론에 남긴다.

**Phase B 튜닝 지침**: assertion이 valid 구현을 FAIL시키면(특히 hit의 `role`·저장 wrapping 키처럼 가이드가
이름을 안 못박은 부분) skill을 완화한다. 반대로 가이드가 못박은 계약(반환 키 `hits`/`rows`/`context`/
`rag_backend`/`sync`/`reference_backend`/`reference`, hit의 `id/content/distance/metadata(title,tags)`,
무매칭 `rows=[]`, 현재 대화 제외, 임의값 금지)을 못 지키면 구현 결함이므로 FAIL로 남긴다.
