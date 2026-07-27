---
name: verify-week4
description: Week 4 구현(student_parts/week04_retrieve_nanas_memory.py)을 검증한다. 1회차(add_personal_reference/search_personal_references/search_saved_requests tool과 add_personal_reference_dict/search_personal_reference_hits/search_saved_request_rows helper, hits/rows top-level 계약)와 2회차(search_conversation_messages/search_nana_memory tool, search_conversation_messages_dict/search_conversation_message_rows helper, conversation RAG lazy sync·현재 대화 제외, hits/rows/context/rag_backend/sync 계약, week04_tools/build_week04_agent 배선, week04_prompt_parts 3출처 지침)를 모두 다룬다. py_compile, 모듈 import, 스키마 필드·기본값·bounds·description, 반환 JSON 계약, temp SQLite 왕복(search_saved_requests), fake embedding 주입으로 대화 RAG 전 계약(제외 규칙·lazy sync 증분·top_k), search_nana_memory 필터 반영, tags=None 정규화, tool 안 safe_limit 보정을 키 없이 확인하고, 키가 있으면 ChromaDB RAG 실경로(개인 참고자료 add→search 인과 왕복)까지 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 4 검증 (Verification)

`PROXY_TOKEN` 없이 실행 가능한 **정적 검증(1~10단계)**을 먼저 수행한다. 키가 있으면 **RAG 실경로 검증(11단계)**까지 한다.
각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. **코드는 수정하지 않는다.**

> **Phase A 뼈대 (구현 전 작성).** 이 skill은 대상 파일의 `[4주차 1회차/2회차 수강생 구현 가이드]`와
> 읽기 전용 `fixed/reference_store.py`·`fixed/conversation_rag_store.py`·`fixed/app_store.py` 계약에서
> 유도했다. builder 구현 후 verifier가 실행해 1~11단계를 확정한다. 실행 중 실패가 나면 두 부류로 구분한다:
> **가이드가 못박은 계약 위반 → FAIL(구현 결함)**, **valid 구현인데 assertion이 과하게 좁아 실패 → skill 완화 후보**(코드는 고치지 않는다).
> 특히 hit의 `role`·저장 결과 wrapping 키처럼 가이드가 이름을 안 못박은 부분은 단언하지 않는다.

명령은 모두 `uv run python -X utf8`로 시작한다. `-X utf8`은 Windows 콘솔 코드페이지와 무관하게
한글 출력을 보존한다. `PYTHONIOENCODING=...` 접두어를 붙이면 `allowed-tools: Bash(uv *)` 패턴에서
벗어나 불필요한 권한 프롬프트가 뜨므로 쓰지 않는다.

## 왜 RAG 왕복은 키가 필요한가

`search_personal_references`·`add_personal_reference`는 ChromaDB에 문서/쿼리를 넣을 때
**OpenAI embedding proxy**(PROXY_TOKEN)를 호출한다. `PersonalReferenceStore`는 생성자가
`OpenAIEmbeddingFunction`을 고정으로 만들므로 주입 지점이 없다 → 개인 참고자료 실왕복은
11단계(키 필요)에서만 확인한다.

반면 **대화 RAG는 키 없이 전부 검증할 수 있다.** `ConversationRAGStore.__init__`이
`embedding_function`/`collection_name` 주입을 받으므로(fixed/conversation_rag_store.py:20-37),
결정적 fake embedding을 넣으면 제외 규칙·lazy sync 증분·top_k까지 재현 가능하게 잰다(8단계).
`search_saved_requests`(SQLite LIKE)와 모든 스키마/배선/반환 계약도 키 없이 확인한다.

## 격리 하네스 (store를 건드리는 단계 공통)

Week 4 tool은 모듈 전역 `REFERENCE_STORE`/`SQLITE_STORE`/`CONVERSATION_RAG_STORE`를 직접 참조한다.
검증이 앱 DB(`data/kanana_app.sqlite3`)나 실 ChromaDB(`CONFIG.chroma_dir`)를 오염시키지 않도록,
**이 전역들을 임시 경로 인스턴스로 monkeypatch**한다.

⚠️ **전역 세 개만으로는 부족하다.** `AppSQLiteStore.save_structured_request`는 `kind`가
`personal_schedule`/`group_schedule`이면 외부 공유 저장소에도 복사한다(fixed/app_store.py:406-412).
이 복사는 MCP subprocess를 타고 `KANANA_EXTERNAL_DB_PATH`(없으면 `CONFIG.external_db_path`)를 쓰므로,
**temp 앱 DB에 seed해도 외부 공유 DB는 실 파일이 오염된다.** 환경변수는 `fixed/mcp_client.py`가
호출 시점에 `os.environ`에서 읽으므로 seed 전에 세팅하면 된다.

```python
# (참고용 하네스 — 각 단계에 인라인됨)
import os, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week04_retrieve_nanas_memory as m
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')  # 외부 공유 저장소 (seed 전에)
m.SQLITE_STORE = store_mod.AppSQLiteStore(_tmp / 'app.sqlite3')   # SQLite tool이 이 임시 DB를 쓴다
# 11단계(키 필요)에서는 REFERENCE_STORE/CONVERSATION_RAG_STORE도 임시 chroma_dir로 재바인딩한다:
#   m.REFERENCE_STORE = PersonalReferenceStore(_tmp / 'chroma')
#   m.CONVERSATION_RAG_STORE = ConversationRAGStore(_tmp / 'chroma')
# 8단계는 ConversationRAGStore(_tmp / 'chroma', embedding_function=FakeEmbedding(), ...)로 키 없이 쓴다.
```

tool 본문이 이 전역들을 인자로 helper에 넘기므로, 전역을 교체하면 모든 tool 경로에 적용된다.
(이 skill은 Week 1-3 tool을 직접 부르지 않으므로 week03 모듈의 `CONFIG` 재바인딩까지는 필요 없다.
`evals/week04_eval.py`는 agent 경로라 필요하며 거기서 처리한다.)

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
uv run python -X utf8 -c "
import student_parts.week04_retrieve_nanas_memory as m
for n, c in [('AddPersonalReferenceInput',m.AddPersonalReferenceInput),('SearchPersonalReferencesInput',m.SearchPersonalReferencesInput),('SearchSavedRequestsInput',m.SearchSavedRequestsInput),('SearchConversationMessagesInput',m.SearchConversationMessagesInput),('SearchNanaMemoryInput',m.SearchNanaMemoryInput)]:
    print(n, {k: (str(v.annotation), v.is_required()) for k, v in c.model_fields.items()})

# bounds는 print만 하면 검사가 아니다 — 경계 밖 값이 실제로 거부되는지 단정한다.
for name, model, field_name, lo, hi in [
    ('SearchPersonalReferencesInput', m.SearchPersonalReferencesInput, 'top_k', 1, 20),
    ('SearchSavedRequestsInput', m.SearchSavedRequestsInput, 'top_k', 1, 50),
    ('SearchConversationMessagesInput', m.SearchConversationMessagesInput, 'top_k', 1, 50),
    ('SearchNanaMemoryInput', m.SearchNanaMemoryInput, 'limit', 1, 20),
]:
    for bad in (lo - 1, hi + 1):
        try:
            model(query='q', **{field_name: bad})
            raise AssertionError(name + '.' + field_name + ' 가 경계 밖 값 ' + str(bad) + ' 를 통과시킴')
        except AssertionError:
            raise
        except Exception:
            pass
    print(name + '.' + field_name + ' bounds OK [' + str(lo) + ',' + str(hi) + ']')
print('SCHEMA_OK')
"
```
확인 포인트: 스키마 필드/타입이 파일 상단 정의(top_k `ge=1,le=20`/`le=50`, conversation_id `str|None`)와 일치하고, **경계 밖 값이 실제로 거부**된다.

> 참고: week4 스키마 5종은 과제 스캐폴드가 준 코드 그대로이고 `Field(description=...)`이 없다.
> TODO가 스키마 수정을 요구하지 않았으므로 FAIL로 잡지 않는다. 다만 `conversation_id`에 설명이
> 없으면 LLM이 이 인자를 언제 채워야 하는지 근거가 없다는 점은 리스크로 기록한다(8단계 빈 문자열 검사와 연결).

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
확인 포인트: week03 tool/prompt 누적 유지, week4 tool 4개가 순서대로 추가, `build_week04_agent`가 `create_agent`+`week04_tools`+`week04_system_prompt`+싱글턴으로 배선, `week04_prompt_parts`에 새 조각 존재. (프롬프트 **문구**는 자유이므로 특정 문자열을 단언하지 않는다 — 3출처 지침이 실제로 먹는지는 `evals/week04_eval.py`의 라우팅 통과율로 판정.)

## 5. helper 단위 — safe_limit 경계
```bash
uv run python -X utf8 -c "import student_parts.week04_retrieve_nanas_memory as m; print(m.safe_limit(0), m.safe_limit(999,maximum=50), m.safe_limit('3'), m.safe_limit(None, default=5)); assert m.safe_limit(0)==1 and m.safe_limit(999,maximum=50)==50 and m.safe_limit('3')==3 and m.safe_limit(None,default=5)==5; print('SAFE_LIMIT_OK')"
```
확인 포인트: 1 미만은 1로, maximum 초과는 maximum으로, 문자열 숫자는 int, 비정상값은 default.

## 6. SQLite 저장 요청 검색 왕복 (temp DB, 키 불필요)
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from pathlib import Path
import fixed.app_store as store_mod
import student_parts.week04_retrieve_nanas_memory as m
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')  # personal_schedule seed가 실 공유 DB로 새지 않게
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

## 8. 대화 RAG 전 계약 (fake embedding 주입, 키 불필요)

`ConversationRAGStore`는 `embedding_function` 주입을 받으므로 결정적 fake embedding으로
**제외 규칙·lazy sync 증분·top_k·rows helper**를 전부 키 없이 잰다. 소스 문자열 grep(7단계)이 아니라
**실행으로** 확인하는 단계다.

```bash
uv run python -X utf8 -c "
import hashlib, tempfile
from pathlib import Path
from fixed.app_store import AppSQLiteStore
from fixed.conversation_rag_store import ConversationRAGStore
from fixed.session_scope import conversation_session_scope
import student_parts.week04_retrieve_nanas_memory as m

class FakeEmbedding:
    DIM = 64
    def name(self): return 'fake_embedding'
    def is_legacy(self): return True
    def _vec(self, text):
        vec = [0.0] * self.DIM
        for token in str(text).split():
            h = int(hashlib.sha256(token.encode('utf-8')).hexdigest(), 16)
            vec[h % self.DIM] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]
    def __call__(self, input): return [self._vec(t) for t in input]
    def embed_query(self, input): return self(input)
    def embed_documents(self, input): return self(input)

FAILS = []
def check(label, ok, detail=''):
    print(('  OK   ' if ok else '  FAIL ') + label + (('  :: ' + detail) if (detail and not ok) else ''))
    if not ok: FAILS.append(label)

tmp = Path(tempfile.mkdtemp())
sq = AppSQLiteStore(tmp / 'app.sqlite3')
rag = ConversationRAGStore(tmp / 'chroma', embedding_function=FakeEmbedding(), collection_name='verify_conv')
m.SQLITE_STORE = sq
m.CONVERSATION_RAG_STORE = rag   # rows helper가 이 전역을 쓴다

past = sq.create_conversation('제주도 여행')['conversation_id']
sq.append_message(past, 'user', '제주도 여행 흑돼지 먹었어')
cur = sq.create_conversation('오늘 대화')['conversation_id']
sq.append_message(cur, 'user', '제주도 여행 얘기 방금 꺼냈어')

# (a) 반환 키 계약 + 최초 lazy sync
p = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5, conversation_id=None)
check('(a1) 미구현 아님', p is not None)
if p is None: raise SystemExit('search_conversation_messages_dict 미구현(None) — 이후 검사 불가')
check('(a2) 키=hits/rows/context/rag_backend/sync', set(p) == set(('hits','rows','context','rag_backend','sync')), str(sorted(p)))
check('(a3) hits == rows', p['hits'] == p['rows'])
check('(a4) context 비어 있지 않음', isinstance(p['context'], str) and bool(p['context'].strip()))
check('(a5) rag_backend 내용 있음', 'vector_store' in p['rag_backend'], str(p['rag_backend']))
check('(a6) 최초 lazy sync', p['sync']['upserted'] == 2 and p['sync']['total'] == 2, str(p['sync']))

# (b) 현재 대화 제외 — 실행으로 확인 (소스 grep 아님)
with conversation_session_scope(cur):
    scoped = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5, conversation_id=None)
ids = [h['conversation_id'] for h in scoped['hits']]
check('(b1) conversation_id=None 이면 현재 대화 제외', cur not in ids, str(ids))
check('(b2) 과거 대화는 검색됨', past in ids, str(ids))

# (c) conversation_id 빈 문자열도 '미지정'이다 — 제외가 풀리면 필터도 제외도 없는 상태가 된다
with conversation_session_scope(cur):
    blank = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5, conversation_id='')
blank_ids = [h['conversation_id'] for h in blank['hits']]
check('(c) conversation_id=빈 문자열에서도 현재 대화 제외', cur not in blank_ids, '현재 대화 누출: ' + str(blank_ids))

# (d) conversation_id 명시 시 그 대화만
with conversation_session_scope(cur):
    pinned = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5, conversation_id=past)
check('(d) conversation_id 명시 시 그 대화만', set(h['conversation_id'] for h in pinned['hits']) == set((past,)), str([h['conversation_id'] for h in pinned['hits']]))

# (e) rows helper — 어디서도 안 부르면 미구현이어도 통과해 버린다
rows = m.search_conversation_message_rows(sq, query='제주도 여행', top_k=5)
check('(e1) rows helper가 list 반환', isinstance(rows, list), str(type(rows)))
check('(e2) rows helper == dict helper의 hits', rows == m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5)['hits'])

# (f) top_k 상한 준수
check('(f) top_k=1 준수', len(m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=1)['hits']) <= 1)

# (g) lazy sync가 '증분'인지 — 상수 dict를 넣어도 키 검사만으로는 통과한다
again = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5)
check('(g1) 재호출은 재임베딩 없음', again['sync']['upserted'] == 0 and again['sync']['skipped'] == 2, str(again['sync']))
sq.append_message(past, 'user', '성산일출봉도 갔었지')
changed = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5)
check('(g2) 변경된 대화만 재upsert', changed['sync']['upserted'] == 1 and changed['sync']['skipped'] == 1, str(changed['sync']))
sq.delete_conversation(past)
gone = m.search_conversation_messages_dict(sq, rag, query='제주도 여행', top_k=5)
check('(g3) 삭제된 대화는 stale 제거', gone['sync']['deleted'] == 1, str(gone['sync']))

if FAILS: raise SystemExit('CONVERSATION_RAG_FAIL: ' + ', '.join(FAILS))
print('CONVERSATION_RAG_OK')
"
```
확인 포인트: (a) `hits`/`rows`(동일)/`context`/`rag_backend`/`sync`, (b)(c) conversation_id가 `None`이든 `''`이든 현재 대화 제외, (d) 명시 시 해당 대화만, (e) `search_conversation_message_rows`가 dict helper의 `hits`와 일치, (f) top_k 상한, (g) sync가 증분(upsert/skip/delete) 의미를 실제로 가짐.

## 9. `search_nana_memory` 통합 검색 (키 불필요)

이 tool은 `week04_tools()`에 없어 agent에 노출되지 않는다. 그래서 **어디서도 부르지 않으면
미구현(`...` → None)이어도 전 단계가 통과한다.** 여기서 직접 invoke한다.

```bash
uv run python -X utf8 -c "
import json, os, tempfile
from pathlib import Path
from fixed.app_store import AppSQLiteStore
import student_parts.week04_retrieve_nanas_memory as m

class FakeRefStore:
    def backend_info(self): return {'vector_store': 'chromadb'}
    def add_personal_reference(self, title, content, tags=None):
        return {'reference_id':'ref_x','title':title,'content':content,'tags':tags or [],'backend':self.backend_info()}
    def search_personal_references(self, query, limit=3):
        return [{'id':'ref_1','title':'집중 시간','content':'오전에 집중이 잘 된다','tags':'pref','distance':0.1}]

tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(tmp / 'external.sqlite3')
sq = AppSQLiteStore(tmp / 'app.sqlite3')
m.SQLITE_STORE = sq
m.REFERENCE_STORE = FakeRefStore()
sq.save_structured_request({'kind':'personal_schedule','title':'팀 회식','date':'2026-03-10','start_time':'19:00','members':['민수']})
sq.save_structured_request({'kind':'personal_schedule','title':'치과','date':'2026-12-01','start_time':'10:00','members':[]})

FAILS = []
def check(label, ok, detail=''):
    print(('  OK   ' if ok else '  FAIL ') + label + (('  :: ' + detail) if (detail and not ok) else ''))
    if not ok: FAILS.append(label)

raw = m.search_nana_memory.invoke({'query':'회식','limit':5})
check('(a1) 미구현 아님', raw is not None)
if raw is None: raise SystemExit('search_nana_memory 미구현(None) — 이후 검사 불가')
out = json.loads(raw)
check('(a2) context 키 존재', 'context' in out, str(sorted(out)))
check('(a3) context 비어 있지 않음', isinstance(out.get('context'), str) and bool(out['context'].strip()))
print('nana_memory keys =', sorted(out))

# 스키마가 선언한 필터가 실제로 반영되는가.
# 받아 놓고 무시하면 LLM이 넘긴 조건이 조용히 사라진다(가이드는 '일정 chunk'를 묶으라고 하고,
# 스캐폴드는 _decode_attendees와 date/attendee 인자를 함께 준다).
def saved_of(payload):
    return payload.get('saved_requests', payload.get('rows', []))

wide = json.loads(m.search_nana_memory.invoke({'query':'','limit':20}))
narrow = json.loads(m.search_nana_memory.invoke({'query':'','limit':20,'date_from':'2026-03-01','date_to':'2026-03-31'}))
check('(b1) date_from/date_to가 결과를 좁힘', len(saved_of(narrow)) < len(saved_of(wide)), '무시됨 wide=' + str(len(saved_of(wide))) + ' narrow=' + str(len(saved_of(narrow))))
att = json.loads(m.search_nana_memory.invoke({'query':'','limit':20,'attendee':'존재하지않는사람'}))
check('(b2) attendee가 결과를 좁힘', len(saved_of(att)) < len(saved_of(wide)), '무시됨 att=' + str(len(saved_of(att))) + ' wide=' + str(len(saved_of(wide))))

if FAILS: raise SystemExit('NANA_MEMORY_FAIL: ' + ', '.join(FAILS))
print('NANA_MEMORY_OK')
"
```
확인 포인트: `search_nana_memory`가 실제로 동작하고 `context`를 만들며, **스키마에 선언한
`date_from`/`date_to`/`attendee`가 결과를 실제로 좁힌다.** (top-level 키 이름은 가이드가 못박지
않았으므로 `context` 외에는 단정하지 않는다.)

## 10. `tags=None` 정규화 + tool 안 `safe_limit` 보정 (키 불필요)

```bash
uv run python -X utf8 -c "
import json
import student_parts.week04_retrieve_nanas_memory as m

class RecordingRefStore:
    def __init__(self): self.added = []; self.limits = []
    def backend_info(self): return {'vector_store':'chromadb'}
    def add_personal_reference(self, title, content, tags=None):
        self.added.append(tags)
        return {'reference_id':'ref_x','title':title,'content':content,'tags':tags or [],'backend':self.backend_info()}
    def search_personal_references(self, query, limit=3):
        self.limits.append(limit); return []

class RecordingSQLiteStore:
    def __init__(self): self.limits = []
    def search_saved_requests(self, query, kind=None, limit=5):
        self.limits.append(limit); return []

FAILS = []
def check(label, ok, detail=''):
    print(('  OK   ' if ok else '  FAIL ') + label + (('  :: ' + detail) if (detail and not ok) else ''))
    if not ok: FAILS.append(label)

ref = RecordingRefStore()
sql = RecordingSQLiteStore()
m.REFERENCE_STORE = ref
m.SQLITE_STORE = sql

# 가이드 line 48-49: tags가 None이면 빈 list로 바꿔 store에 넘긴다
d = m.add_personal_reference_dict(ref, title='t', content='c', tags=None)
check('(a1) helper: tags=None -> []', ref.added[-1] == [], str(ref.added[-1]))
check('(a2) add 반환 키', set(d) == set(('reference_backend','reference')), str(sorted(d)))
json.loads(m.add_personal_reference.invoke({'title':'t2','content':'c2'}))
check('(a3) tool 경로: tags 미지정 -> []', ref.added[-1] == [], str(ref.added[-1]))

# 가이드 line 44: top_k 보정은 tool 안에서 safe_limit()으로 한다.
# 스키마 bounds에만 의존하면 .func 직접 호출/bounds 우회 경로에서 보정이 사라진다.
m.search_personal_references.func(query='q', top_k=999)
check('(b1) search_personal_references 상한 20', ref.limits[-1] == 20, str(ref.limits[-1]))
m.search_personal_references.func(query='q', top_k=0)
check('(b2) search_personal_references 하한 1', ref.limits[-1] == 1, str(ref.limits[-1]))
m.search_saved_requests.func(query='q', top_k=999)
check('(b3) search_saved_requests 상한 50 + limit 키워드 전달', sql.limits[-1] == 50, str(sql.limits[-1]))

if FAILS: raise SystemExit('ADD_AND_LIMIT_FAIL: ' + ', '.join(FAILS))
print('ADD_AND_LIMIT_OK')
"
```
확인 포인트: `tags=None` → `[]`(helper·tool 양쪽), 각 tool이 자기 default/maximum으로 `safe_limit`을
적용, `search_saved_requests`가 store에 `limit=`을 **키워드로** 넘김(positional로 넘기면 `kind` 자리에
들어가 조용히 오작동한다).

## 11. RAG 실경로 검증 (`.env`에 `PROXY_TOKEN`이 있을 때만)

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

# add -> search 인과 왕복. store가 DEFAULT_REFERENCES를 seed하므로 '집중' 같은 말로 검색하면
# add가 아무것도 저장하지 않아도 hits > 0 이 나온다. seed에 없는 고유 토큰으로 인과를 확인한다.
uniq = 'zqx크왁크왁프로토콜'
json.loads(m.add_personal_reference.invoke({'title':'검증용 원칙','content':'나는 ' + uniq + ' 규칙에 따라 회의를 잡는다','tags':['verify']}))
back = json.loads(m.search_personal_references.invoke({'query': uniq, 'top_k': 3}))
assert any(uniq in (item.get('content') or '') for item in back['hits']), 'add한 문서가 검색되지 않음(add가 실제로 저장하지 않았을 수 있음): ' + str(back['hits'])[:200]
print('add->search 인과 왕복 OK')

# search_conversation_messages → hits+rows+context+rag_backend+sync, 현재 대화 제외
out = json.loads(m.search_conversation_messages.invoke({'query':'회의','top_k':3}))
assert set(('hits','rows','context','rag_backend','sync')) <= set(out), f'conversation 반환 계약 위반: {sorted(out)}'
assert out['hits'] == out['rows'], 'hits와 rows가 같은 결과가 아님'
print('conv keys =', sorted(out), '| sync =', out['sync'])
print('RAG_REALPATH_OK')
"
```
확인 포인트: `add_personal_reference`는 `reference_backend`+`reference`, **add한 문서가 실제로 검색된다**(고유 토큰 인과 왕복), `search_personal_references`는 top-level `hits`(각 hit `id/content/distance/metadata`, metadata에 `title`/`tags`), `search_conversation_messages`는 실 embedding 경로에서도 `hits`/`rows`(동일)/`context`/`rag_backend`/`sync`. 대화 RAG의 제외 규칙·증분 sync 같은 **계약 본체는 8단계**에서 이미 결정적으로 쟀다(여기는 실경로 smoke). **출처별 라우팅**(질문 성격→맞는 tool)과 **답변 정확성·근거 규칙**은 이 skill이 아니라 `evals/week04_eval.py`에서 통과율로 판정한다.

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
- 1~10단계 전부 통과할 때만 "정적 검증 통과". 11단계를 건너뛰었으면(키 없음) 그 사실을 결론에 남긴다.

**Phase B 튜닝 지침**: assertion이 valid 구현을 FAIL시키면(특히 hit의 `role`·저장 wrapping 키처럼 가이드가
이름을 안 못박은 부분) skill을 완화한다. 반대로 가이드가 못박은 계약(반환 키 `hits`/`rows`/`context`/
`rag_backend`/`sync`/`reference_backend`/`reference`, hit의 `id/content/distance/metadata(title,tags)`,
무매칭 `rows=[]`, 현재 대화 제외, 임의값 금지)을 못 지키면 구현 결함이므로 FAIL로 남긴다.

**이 skill이 예전에 못 보던 것 (8~10단계가 메운 사각지대)**

`...`(미구현)이거나 계약을 어겨도 통과하던 경로들이다. 완화 대상이 아니라 유지 대상이다.

| 사각지대 | 왜 안 잡혔나 | 지금 잡는 곳 |
|---|---|---|
| `search_conversation_message_rows` | 어느 단계에서도 호출하지 않았다 | 8(e) |
| `search_nana_memory` | agent에 노출되지 않아 invoke하는 곳이 없었다 | 9 |
| `tags=None` → `[]` | 실경로 단계가 항상 tags를 넘겼다 | 10 |
| add → search 인과성 | seed된 DEFAULT_REFERENCES가 hits를 채워 add가 no-op이어도 통과했다 | 11 |
| 현재 대화 제외 | 소스 문자열 grep(7단계)일 뿐 실행 검증이 없었다 | 8(b)(c) |
| `sync` 값의 의미·bounds | 키 존재만 보거나 `print`만 하고 `assert`가 없었다 | 8(g), 3 |
