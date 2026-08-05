---
name: verify-week6
description: Week 6 구현(student_parts/week06_kanamate_decides_schedule.py)을 검증한다. 메인과제(week06/nana/kana prompt_parts와 supervisor_system_prompt, nana_agent/kana_agent 위임 wrapper tool)와 추가 과제(FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION/DECIDE_FINAL_SLOT_DESCRIPTION, find_common_available_slots_dict/find_common_available_slots/decide_final_slot)를 모두 다룬다. py_compile, 모듈 import, 스키마 필드·기본값·bounds, prompt 누적 구조(week05 누적/week04 누적/Kana 무누적), 반환 JSON 계약(ok/tool_name/members/busy_rows/candidate_slots/slot_source + top-level final_slot/reason/candidates/needs_agent_selection), 후보 겹침 검증 양방향(겹치는 후보 제외 · 안 겹치는 후보 보존), ISO datetime 날짜 정규화, busy_rows=None일 때 collect_member_schedules 수집(temp 외부 SQLite), "final_slot 자동 선택 금지" 안전규칙, fake 하위 agent 주입으로 nana_agent/kana_agent 반환 계약과 subagent 재사용·final_slot_payload 끌어올리기, supervisor_tools/agent_tool_names/build_week_agent 배선을 PROXY_TOKEN 없이 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 6 검증 (Verification)

Week 6는 **supervisor + Nana/Kana 하위 agent 위임 구조**다. 위임 wrapper는 `create_agent(...)`를 부르므로
원래는 `PROXY_TOKEN`이 필요하지만, 모듈 전역 `_NANA_SUBAGENT` / `_KANA_SUBAGENT` / `_SUPERVISOR_AGENT`에
**fake agent를 주입**하면 `create_agent` 경로를 타지 않고 반환 JSON 계약·trace 끌어올리기를 전부 검증할 수 있다
(week06 파일 line 33-35 전역, line 484-500 TODO의 "None일 때만 만들고 이후에는 재사용" 규칙이 그 근거다).
그래서 **1~11단계는 키 없이 실행 가능**하고, 키가 필요한 것은 실제 agent 조립(12단계, 선택)뿐이다.

각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. **코드는 수정하지 않는다.**

> **Phase A 뼈대 (구현 전 작성).** 이 skill은 대상 파일의 `[6주차 수강생 구현 가이드]`와
> 읽기 전용 `fixed/schedule_decision.py`·`fixed/langchain_trace.py`·`fixed/external_people_store.py`·
> `student_parts/week05_load_kanas_past_conversations.py` 계약에서 유도했다. builder 구현 후 verifier가 실행해 확정한다.
> 실행 중 실패가 나면 두 부류로 구분한다: **가이드/`fixed/`가 못박은 계약 위반 → FAIL(구현 결함)**,
> **valid 구현인데 assertion이 과하게 좁아 실패 → skill 완화 후보**(코드는 고치지 않는다).
> 특히 가이드가 못박지 않은 부분(prompt 본문 문구, `find_common_available_slots_dict`가 남기는 부가 키,
> tool description의 정확한 문장)은 단언하지 않는다. **prompt 문구 자체는 이 skill의 판정 대상이 아니다** —
> 위임 라우팅이 실제로 어디로 가는지는 확률적 행동이라 `evals/week06_eval.py`가 통과율로 잰다.

명령은 모두 `uv run python -X utf8`로 시작한다. `-X utf8`은 Windows 콘솔 코드페이지와 무관하게
한글 출력을 보존한다. `PYTHONIOENCODING=...` 접두어를 붙이면 `allowed-tools: Bash(uv *)` 패턴에서
벗어나 불필요한 권한 프롬프트가 뜨므로 쓰지 않는다.

## 왜 키 없이 다 되는가

| 검증 대상 | 키 없이 되는 이유 |
|---|---|
| 스키마·필드·bounds | Pydantic 인스턴스화뿐 |
| `find_common_available_slots` / `decide_final_slot` | 계산은 `fixed/schedule_decision.py`의 순수 함수. `busy_rows`를 인자로 주입하면 외부 DB도 안 탄다 |
| `busy_rows=None` 수집 경로 | Week 5 `collect_member_schedules` → MCP stdio subprocess → 순수 SQLite (임베딩 없음) |
| `nana_agent` / `kana_agent` 반환 계약 | 전역 `_NANA_SUBAGENT`/`_KANA_SUBAGENT`에 fake agent 주입 → `create_agent` 미호출 |
| prompt 누적 구조 | 문자열 조립뿐 |

## 격리 하네스 (외부/앱 DB를 건드리는 단계 공통 — 7단계)

⚠️ **환경변수 격리가 필수다.** `busy_rows=None` 경로는 Week 5 `collect_member_schedules`를 타고,
그 MCP subprocess는 `KANANA_EXTERNAL_DB_PATH`(없으면 `CONFIG.external_db_path`)를 읽는다
(mcp_server/sqlite_mcp_server.py:25). temp로 돌리지 않으면 검증이 사용자 실 외부 DB를 읽는다.
`fixed/mcp_client.py`가 **호출 시점에** `os.environ`을 복사하므로 첫 tool 호출 전에 세팅하면 된다.

⚠️ **앱 DB와 Week 1 인메모리 리스트도 같이 돌린다.** `collect_member_schedules`는 내 일정을
`AppSQLiteStore(CONFIG.app_db_path)` + `w1.PERSONAL_SCHEDULES`에서 읽는다(week05 모듈 전역).

```python
# (참고용 하네스 — 7단계에 인라인됨)
import os, tempfile
from dataclasses import replace
from pathlib import Path
from fixed.config import CONFIG
import student_parts.week01_wake_up_nana as w1
import student_parts.week05_load_kanas_past_conversations as w5
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')   # MCP subprocess (첫 호출 전에)
w5.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w1.PERSONAL_SCHEDULES[:] = []
```

⚠️ **MCP subprocess가 stderr로 `INFO ... Processing request of type CallToolRequest` 로그를 뿜는다.**
7단계 판정 출력이 이 로그에 묻히므로 명령 끝에
`| grep -v "Processing request of type\|ListToolsRequest\|CallToolRequest\|server.py:"`
를 덧붙여 읽는다. **Python 코드 자체는 절대 바꾸지 않는다** — 필터는 출력 가독성용일 뿐이다.

temp 외부 DB는 첫 접근 때 `ExternalPeopleSQLiteStore`가 스스로 July 실습 fixture를 seed하므로
(fixed/external_people_store.py:65-84) **2026-07-07 ~ 2026-07-17 / 철수·영희·민준·서연·지훈·하린**을
별도 seed 없이 기대할 수 있다.

---

## 1. 구문 검사 (py_compile)
```bash
uv run python -m py_compile student_parts/week06_kanamate_decides_schedule.py
```

## 1-b. 미구현 placeholder 스캔 + 책임 경계 정적 검사
```bash
uv run python -X utf8 -c "
import ast, inspect
from pathlib import Path
src = Path('student_parts/week06_kanamate_decides_schedule.py').read_text(encoding='utf-8')
tree = ast.parse(src)

# (1) 함수 본문에 남은 '...' placeholder — 구현했다고 착각하기 가장 쉬운 실패다.
#     ⚠️ 문자열 '...' 검색은 쓰지 않는다. 가이드 주석이 '(...)' 표기를 쓰기 때문에 오탐한다.
left = []
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
                left.append(node.name + ':' + str(stmt.lineno))
print('placeholder 남은 함수 =', left)
assert not left, '미구현 placeholder가 남아 있음: ' + str(left)

# (2) nested LLM 금지 (가이드 line 94/98) — 후보/최종 결정 함수 안에서 LLM을 만들면 FAIL
import student_parts.week06_kanamate_decides_schedule as m
for name in ('find_common_available_slots_dict', 'find_common_available_slots', 'decide_final_slot'):
    obj = getattr(m, name)
    body = inspect.getsource(getattr(obj, 'func', obj))
    for banned in ('chat_model(', 'create_agent('):
        assert banned not in body, name + ' 안에서 ' + banned + ' 를 호출함 (nested LLM 금지)'
print('nested LLM 없음 OK')

# (3) create_agent는 위임 wrapper와 supervisor builder에서만 등장해야 한다
owners = set()
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and 'create_agent(' in ast.get_source_segment(src, node):
        owners.add(node.name)
print('create_agent 호출 함수 =', sorted(owners))
assert owners <= {'nana_agent', 'kana_agent', 'build_langchain_supervisor_agent'}, '예상 밖 위치에서 agent 생성: ' + str(sorted(owners))

# (4) 겹침 판정·시간 파싱 재구현 금지 (fixed/schedule_decision.py가 정본)
defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
dup = defined & {'busy_rows_overlap', 'parse_time_minutes', 'format_time_minutes', 'date_range',
                 'normalize_llm_candidate_slots', 'slot_to_text', 'normalize_date_bound'}
assert not dup, 'fixed/schedule_decision.py 함수를 재구현함: ' + str(sorted(dup))

# (5) 예외 삼킴 금지
assert 'except Exception:' not in src or 'pass' not in src.split('except Exception:')[-1][:40], '예외를 조용히 삼키는 블록이 있음'
print('STATIC_OK')
"
```
확인 포인트: `...` placeholder가 남아 있지 않고, 후보/결정 함수 안에서 **nested LLM을 만들지 않으며**,
`fixed/schedule_decision.py`의 겹침·시간 helper를 **재구현하지 않는다**.
> 5주차 실측: builder가 이미 있는 helper를 재구현했고 그 재구현이 곧 기능 결함이었다.
> `create_agent` 소유 함수 집합이 예상보다 작으면(예: `kana_agent` 누락) 10단계에서 다시 잡힌다.

## 2. import + 스키마 인스턴스화 스모크 테스트
```bash
uv run python -X utf8 -c "
import student_parts.week06_kanamate_decides_schedule as m
f = m.FindCommonAvailableSlotsInput(member_names=['철수'], date_from='2026-07-07', date_to='2026-07-10')
print('find duration=', f.duration_minutes, '| workday=', f.workday_start, f.workday_end, '| limit=', f.limit)
print('find busy_rows=', f.busy_rows, '| candidate_slots=', f.candidate_slots, '| llm_reason=', f.llm_reason)
d = m.DecideFinalSlotInput()
print('decide candidate_slots=', d.candidate_slots, '| final_slot=', d.final_slot, '| needs=', d.needs_agent_selection, '| duration=', d.duration_minutes)
p = m.ProposeGroupScheduleInput(title='t', member_names=['철수'])
print('propose candidate_slots=', p.candidate_slots, '| selected=', p.selected_slot)
a = m.AgentQueryInput(query='q'); print('agent query=', a.query)
print('kana tools =', [m.tool_name(t) for t in m.kana_tools()])
print('supervisor tools =', [m.tool_name(t) for t in m.supervisor_tools()])
print('OK')
"
```
확인 포인트: `FindCommonAvailableSlotsInput` 기본값 `duration_minutes=60`·`workday_start='09:00'`·
`workday_end='18:00'`·`limit=5`·`busy_rows=None`·`candidate_slots=[]`·`llm_reason=None`,
`DecideFinalSlotInput`은 **모든 필드가 선택**이고 `candidate_slots=[]`·`duration_minutes=60`·나머지 `None`.
**스키마는 과제 스캐폴드가 준 코드다 — 값이 바뀌어 있으면 그 자체가 FAIL이다(임의 수정 금지).**

## 3. 필드 스펙 + bounds 대조
```bash
uv run python -X utf8 -c "
import student_parts.week06_kanamate_decides_schedule as m
for n in ('FindCommonAvailableSlotsInput','DecideFinalSlotInput','ProposeGroupScheduleInput','AgentQueryInput'):
    c = getattr(m, n)
    print(n, {k: (str(v.annotation), v.is_required()) for k, v in c.model_fields.items()})

base = {'member_names': ['철수'], 'date_from': '2026-07-07', 'date_to': '2026-07-10'}
# bounds는 print만 하면 검사가 아니다 — 경계 밖 값이 실제로 거부되는지 단정한다.
for field_name, lo, hi in (('duration_minutes', 30, 480), ('limit', 1, 20)):
    for bad in (lo - 1, hi + 1):
        try:
            m.FindCommonAvailableSlotsInput(**base, **{field_name: bad})
            raise AssertionError('FindCommonAvailableSlotsInput.' + field_name + ' 가 경계 밖 값 ' + str(bad) + ' 를 통과시킴')
        except AssertionError:
            raise
        except Exception:
            pass
    print('FindCommonAvailableSlotsInput.' + field_name + ' bounds OK [' + str(lo) + ',' + str(hi) + ']')

# member_names/date_from/date_to 는 필수 — 빠지면 거부돼야 한다
for missing in ('member_names', 'date_from', 'date_to'):
    kwargs = {k: v for k, v in base.items() if k != missing}
    try:
        m.FindCommonAvailableSlotsInput(**kwargs)
        raise AssertionError(missing + ' 없이도 통과함 (필수 필드가 아님)')
    except AssertionError:
        raise
    except Exception:
        pass
print('SCHEMA_OK')
"
```
확인 포인트: 필드/타입이 파일 상단 정의와 일치하고 **경계 밖 값과 필수 필드 누락이 실제로 거부**된다.

## 4. prompt 누적 구조 대조 (메인과제)
```bash
uv run python -X utf8 -c "
import student_parts.week06_kanamate_decides_schedule as m
import student_parts.week04_retrieve_nanas_memory as w4
import student_parts.week05_load_kanas_past_conversations as w5

w5_parts, w4_parts = w5.week05_prompt_parts(), w4.week04_prompt_parts()
sup, nana, kana = m.week06_prompt_parts(), m.nana_prompt_parts(), m.kana_prompt_parts()

assert sup[:len(w5_parts)] == w5_parts, 'week06_prompt_parts가 week05_prompt_parts를 누적하지 않음 (가이드 line 69)'
assert nana[:len(w4_parts)] == w4_parts, 'nana_prompt_parts가 week04_prompt_parts를 누적하지 않음 (가이드 line 69)'
print('supervisor 신규 조각 =', len(sup) - len(w5_parts), '| nana 신규 조각 =', len(nana) - len(w4_parts), '| kana 조각 =', len(kana))
assert len(sup) > len(w5_parts), 'Week 6 supervisor 전용 조각이 비어 있음'
assert len(nana) > len(w4_parts), 'Nana 전용 조각이 비어 있음'
assert kana and all(str(p).strip() for p in kana), 'kana_prompt_parts가 비어 있음 (가이드 line 70: 누적 없이 처음부터 작성)'
# Kana는 다른 주차 prompt를 누적하지 않는다 (가이드 line 222)
assert kana[:1] != w4_parts[:1] and kana[:1] != w5_parts[:1], 'kana_prompt_parts가 이전 주차 조각을 누적함 (가이드 line 222 위반)'

sp, np_, kp = m.supervisor_system_prompt(), m.nana_system_prompt(), m.kana_system_prompt()
assert m.week06_system_prompt() == sp, 'week06_system_prompt()가 supervisor_system_prompt()와 다름'
assert sp.startswith(m.join_system_prompt(w5_parts)[:80]), 'supervisor prompt 앞부분이 누적 조각으로 시작하지 않음'
# supervisor는 위임 tool 두 개만 보므로 그 이름이 prompt에 있어야 한다 (가이드 line 241)
for name in ('nana_agent', 'kana_agent'):
    assert name in sp, 'supervisor prompt에 ' + name + ' 위임 지시가 없음'
assert len(sp) > len(m.join_system_prompt(w5_parts)), 'supervisor 실행 역할 지시가 덧붙지 않음 (가이드 line 240)'
assert len({sp, np_, kp}) == 3, '세 agent가 같은 system prompt를 쓰고 있음 (가이드 line 71/107)'

# 하위 agent는 supervisor prompt를 공유하지 않는다 (가이드 line 71) → Kana는 오늘 날짜를 스스로 가져야
# '다음 주'를 YYYY-MM-DD로 바꿀 수 있다. week06 line 13의 current_app_date_iso import가 그 자리다.
from fixed.runtime_clock import current_app_date_iso
today = current_app_date_iso()
assert today in kp, 'kana prompt에 오늘 날짜(' + today + ')가 없음 — 무누적이라 날짜 근거가 아예 사라짐'
print('len(supervisor)=', len(sp), '| len(nana)=', len(np_), '| len(kana)=', len(kp))
print('PROMPT_OK')
"
```
확인 포인트: 누적 규칙 3종(supervisor ← week05, Nana ← week04, Kana 무누적)이 지켜지고
`supervisor_system_prompt()`가 누적 조각 **뒤에** 실행 역할 지시를 덧붙이며, 세 prompt가 서로 다르다.
prompt **문구**는 판정하지 않는다 — 위임이 실제로 알맞은 하위 agent로 가는지는 `week06_eval`의 통과율로 잰다.
> 두 판정이 좁을 수 있다: `kana[:1] != w4_parts[:1]`(무누적)과 오늘 날짜 문자열 포함.
> 실패 시 **누적을 진짜 했는지 / 날짜를 다른 방식으로 주는지**를 먼저 확인하고, 후자면 skill 완화 후보다.

## 5. tool description 상수 계약 (추가 과제 — 미구현이면 N/A)
```bash
uv run python -X utf8 -c "
import student_parts.week06_kanamate_decides_schedule as m
find_d, dec_d = m.FIND_COMMON_AVAILABLE_SLOTS_DESCRIPTION, m.DECIDE_FINAL_SLOT_DESCRIPTION
print('len(find)=', len(find_d), '| len(decide)=', len(dec_d))
assert find_d.strip() and dec_d.strip(), 'tool description 상수가 빈 문자열 (추가 과제 미구현이면 N/A로 보고)'

# 가이드가 못박은 '형식 고정' 토큰만 단언한다 (line 90-91, 301-303). 문장 표현은 자유다.
for token in ('candidate_slots', 'busy_rows', 'date', 'start_time', 'end_time', 'duration_minutes', 'reason', 'YYYY-MM-DD', 'HH:MM', 'decide_final_slot'):
    assert token in find_d, 'FIND description에 ' + token + ' 계약이 없음 (가이드 line 289-292)'
for token in ('final_slot', 'needs_agent_selection', 'selected_index', 'reason', 'YYYY-MM-DD', 'HH:MM'):
    assert token in dec_d, 'DECIDE description에 ' + token + ' 계약이 없음 (가이드 line 300-303)'

# tool 객체에 실제로 실린 description이 상수와 같은지 (계약이 두 곳으로 갈라지면 agent가 틀린 인자를 넘긴다)
assert find_d in m.find_common_available_slots.description, 'find tool의 description이 상수와 다름'
assert dec_d in m.decide_final_slot.description, 'decide tool의 description이 상수와 다름'
print('DESCRIPTION_OK')
"
```
확인 포인트: 두 상수가 비어 있지 않고 **스키마 필드명·형식 토큰**을 말로 풀어 담고 있으며,
`@tool(description=...)`로 실제 tool에 실려 있다. 문장 표현은 판정하지 않는다.
> ⚠️ **`description=""`은 docstring으로 대체되지 않는다.** LangChain은 `description`이 `None`일 때만
> docstring을 쓴다(`langchain_core/tools/structured.py`). 즉 상수가 빈 문자열이면 Kana agent는 두 tool에
> 대한 신호를 **0** 받는다 — "구현은 했는데 agent가 안 부른다"의 가장 흔한 원인이라 이 단계가 게이트다.
> "tool이 후보를 대신 계산하지 않는다"는 지시(가이드 line 88-89)는 문구가 자유라 정적으로 못 잰다 →
> `week06_eval`의 "agent가 candidate_slots를 채워 넘겼는가" 축이 담당한다.

## 6. find_common_available_slots — 반환 계약 + 겹침 검증 양방향 (추가 과제)
```bash
uv run python -X utf8 -c "
import json
import student_parts.week06_kanamate_decides_schedule as m

BUSY = [
    {'member_name': '철수', 'date': '2026-07-09', 'start_time': '14:00', 'end_time': '15:00', 'title': '고객 인터뷰'},
    {'member_name': '나',   'date': '2026-07-09', 'start_time': '10:00', 'end_time': '11:00', 'title': '팀 리뷰'},
]
ARGS = {'member_names': ['철수'], 'date_from': '2026-07-09', 'date_to': '2026-07-09', 'busy_rows': BUSY, 'duration_minutes': 60}

# (1) 걸러야 할 것을 거르는가 — busy와 겹치는 후보는 남아선 안 된다
bad = json.loads(m.find_common_available_slots.invoke({**ARGS, 'candidate_slots': [
    {'date': '2026-07-09', 'start_time': '14:00', 'end_time': '15:00', 'duration_minutes': 60, 'reason': '철수 회의와 겹침'},
    {'date': '2026-07-09', 'start_time': '10:30', 'end_time': '11:30', 'duration_minutes': 60, 'reason': '내 일정과 겹침'},
]}))
print('overlap keys =', sorted(bad))
assert bad['ok'] is True and bad['tool_name'] == 'find_common_available_slots', bad
assert bad['candidate_slots'] == [], '겹치는 후보가 그대로 남음: ' + str(bad['candidate_slots'])

# (2) 걸러선 안 될 것을 남기는가 — 겹치지 않는 후보는 반드시 살아야 한다 (과잉 제거 = 결함)
good = json.loads(m.find_common_available_slots.invoke({**ARGS, 'candidate_slots': [
    {'date': '2026-07-09', 'start_time': '16:00', 'end_time': '17:00', 'duration_minutes': 60, 'reason': '둘 다 비어 있음'},
]}))
slots = good['candidate_slots']
assert len(slots) == 1 and slots[0]['start_time'] == '16:00', '안 겹치는 후보가 사라짐(과잉 제거): ' + str(slots)
for key in ('date', 'start_time', 'end_time', 'duration_minutes', 'reason'):
    assert key in slots[0], 'candidate_slots 항목에 ' + key + ' 누락: ' + str(slots[0])
for key in ('ok', 'tool_name', 'members', 'busy_rows', 'candidate_slots', 'slot_source', 'payload_source', 'llm_reason'):
    assert key in good, 'fixed/schedule_decision.py의 payload 키 ' + key + ' 가 사라짐: ' + str(sorted(good))
assert good['slot_source'] == 'llm' and good['payload_source'] == 'tool_description', good
assert good['busy_rows'] == BUSY, 'busy_rows가 그대로 기록되지 않음'
# 내 일정도 근거이므로 members에 '나'가 포함돼야 한다 (가이드 line 383-384)
assert '나' in good['members'], \"members에 '나'가 없음 (가이드 line 384): \" + str(good['members'])
assert '철수' in good['members'], 'members에서 외부 멤버가 빠짐: ' + str(good['members'])
print('members =', good['members'])

# (3) tool이 후보를 대신 계산하지 않는다 — candidate_slots를 안 넘기면 빈 목록이어야 한다 (가이드 line 88-89)
none_given = json.loads(m.find_common_available_slots.invoke(ARGS))
assert none_given['candidate_slots'] == [], 'agent가 후보를 안 넘겼는데 tool이 후보를 만들어냄: ' + str(none_given['candidate_slots'])

# (4) date_from/date_to에 ISO datetime이 와도 날짜 부분만 쓴다 (가이드 line 96)
iso = json.loads(m.find_common_available_slots.invoke({**ARGS, 'date_from': '2026-07-09T00:00:00', 'date_to': '2026-07-09T23:59:59',
    'candidate_slots': [{'date': '2026-07-09', 'start_time': '16:00', 'end_time': '17:00', 'duration_minutes': 60, 'reason': 'r'}]}))
assert len(iso['candidate_slots']) == 1, 'ISO datetime 경계에서 후보가 전부 탈락 → normalize_date_bound 미적용: ' + str(iso['candidate_slots'])

# (5) busy_rows=[]는 '빈 목록을 이미 받았다'는 뜻이다 — None이 아니므로 재수집하면 안 된다.
#     `if not busy_rows:` 로 잘못 쓰면 외부 DB를 타면서 rows가 채워져 여기서 잡힌다.
given_empty = json.loads(m.find_common_available_slots.invoke({**ARGS, 'busy_rows': [],
    'candidate_slots': [{'date': '2026-07-09', 'start_time': '14:00', 'end_time': '15:00', 'duration_minutes': 60, 'reason': 'r'}]}))
assert given_empty['busy_rows'] == [], \"busy_rows=[]를 받고도 재수집함 ('is None'이 아니라 falsy로 판정): \" + str(given_empty['busy_rows'])
assert len(given_empty['candidate_slots']) == 1, 'busy가 없는데 후보가 탈락함: ' + str(given_empty['candidate_slots'])
print('FIND_SLOTS_OK')
"
```
확인 포인트: `find_common_available_slots_payload(...)`의 8개 키가 그대로 나오고,
겹침 판정이 **양방향**(겹치는 후보 제외 · 안 겹치는 후보 보존)으로 동작하며,
tool이 스스로 후보를 만들지 않고 `date_from/date_to`의 ISO datetime을 정규화한다.

## 7. busy_rows=None 수집 경로 (추가 과제 · temp 외부 DB)
```bash
uv run python -X utf8 -c "
import json, os, tempfile
from dataclasses import replace
from pathlib import Path
_tmp = Path(tempfile.mkdtemp())
os.environ['KANANA_EXTERNAL_DB_PATH'] = str(_tmp / 'external.sqlite3')
from fixed.config import CONFIG
import student_parts.week01_wake_up_nana as w1
import student_parts.week05_load_kanas_past_conversations as w5
import student_parts.week06_kanamate_decides_schedule as m
w5.CONFIG = replace(CONFIG, app_db_path=_tmp / 'app.sqlite3', external_db_path=_tmp / 'external.sqlite3')
w1.PERSONAL_SCHEDULES[:] = []

out = m.find_common_available_slots_dict(
    member_names=['철수', '영희'], date_from='2026-07-07', date_to='2026-07-17', candidate_slots=None
)
rows = out['busy_rows']
print('collected rows =', len(rows), '| sample =', rows[0] if rows else None)
assert rows, 'busy_rows=None인데 collect_member_schedules로 수집하지 않음 (가이드 line 97/382)'
assert {r.get('member_name') for r in rows} & {'철수', '영희'}, '외부 멤버 busy-time이 안 들어옴: ' + str(rows[:2])
assert out['members'] and '나' in out['members'], \"members에 '나'가 없음 (가이드 line 384)\"

# 수집한 rows를 근거로 겹치는 후보는 걸러져야 한다 (수집 경로와 검증 경로가 실제로 연결됐는지)
first = rows[0]
overlap = m.find_common_available_slots_dict(
    member_names=['철수', '영희'], date_from='2026-07-07', date_to='2026-07-17',
    busy_rows=rows,
    candidate_slots=[{'date': first['date'], 'start_time': first['start_time'], 'end_time': first.get('end_time') or '23:59',
                      'duration_minutes': 60, 'reason': '일부러 겹치게'}],
)
assert overlap['candidate_slots'] == [], '수집된 busy row와 겹치는 후보가 통과함: ' + str(overlap['candidate_slots'])
print('COLLECT_PATH_OK')
" | grep -v "Processing request of type\|ListToolsRequest\|CallToolRequest\|server.py:"
```
확인 포인트: `busy_rows=None`이면 직접 SQL이 아니라 **Week 5 `collect_member_schedules`**로 rows를 채우고
(책임 경계: 이 파일에서 외부 DB를 직접 열면 FAIL), 수집한 rows가 겹침 검증의 실제 근거로 쓰인다.

## 8. decide_final_slot — 반환 계약 + "자동 선택 금지" 안전규칙 (추가 과제)
```bash
uv run python -X utf8 -c "
import json
import student_parts.week06_kanamate_decides_schedule as m

CANDS = [
    {'date': '2026-07-09', 'start_time': '16:00', 'end_time': '17:00', 'duration_minutes': 60, 'reason': 'a'},
    {'date': '2026-07-10', 'start_time': '11:00', 'end_time': '12:00', 'duration_minutes': 60, 'reason': 'b'},
]

# (1) course repo 계약: top-level final_slot / reason / candidates 는 반드시 있어야 한다 (가이드 line 100)
picked = json.loads(m.decide_final_slot.invoke({'candidate_slots': CANDS, 'selected_index': 1,
    'final_slot': '2026-07-10 11:00-12:00', 'needs_agent_selection': False, 'reason': '둘 다 비어 있어 뒤쪽 선택',
    'member_names': ['나', '철수'], 'date_from': '2026-07-07', 'date_to': '2026-07-17'}))
print('decide keys =', sorted(picked))
for key in ('final_slot', 'reason', 'candidates'):
    assert key in picked, 'top-level ' + key + ' 누락 (가이드 line 100): ' + str(sorted(picked))
assert picked['final_slot'] == '2026-07-10 11:00-12:00', picked
assert picked['needs_agent_selection'] is False, picked
assert isinstance(picked['candidates'], list) and picked['candidates'], picked
assert picked['selected_index'] == 1 and picked.get('selected_slot') == CANDS[1], picked
# 근거 trace 키 (가이드 line 101)
for key in ('members', 'date_from', 'date_to', 'candidate_slots'):
    assert key in picked, '근거 키 ' + key + ' 누락 (가이드 line 101): ' + str(sorted(picked))

# (2) 안전규칙: selected_index/selected_slot/final_slot 이 전부 없으면 자동으로 고르지 않는다 (가이드 line 102)
held = json.loads(m.decide_final_slot.invoke({'candidate_slots': CANDS}))
assert held['final_slot'] is None, 'agent가 안 골랐는데 tool이 최종 시간을 자동 선택함: ' + str(held['final_slot'])
assert held['needs_agent_selection'] is True, 'needs_agent_selection이 True로 유지되지 않음: ' + str(held)
assert held['candidates'], '후보는 전달됐는데 candidates가 비어 있음'
print('held =', {k: held[k] for k in ('final_slot', 'needs_agent_selection')})

# (3) 후보가 아예 없어도 계약 키는 유지된다
empty = json.loads(m.decide_final_slot.invoke({}))
for key in ('final_slot', 'reason', 'candidates'):
    assert key in empty, '빈 입력에서 ' + key + ' 누락: ' + str(sorted(empty))
assert empty['final_slot'] is None and empty['candidates'] == [], empty
assert empty['reason'], 'reason이 비어 있음 (사용자-facing 설명이 사라짐)'

# (4) selected_slot만 넘겨도 final_slot이 채워진다 (fixed/schedule_decision.py:223)
by_slot = json.loads(m.decide_final_slot.invoke({'candidate_slots': CANDS, 'selected_slot': CANDS[0]}))
assert by_slot['final_slot'] == '2026-07-09 16:00-17:00', by_slot
assert by_slot['needs_agent_selection'] is False, by_slot

# (5) 범위 밖 selected_index → 확정하지 않고 사유를 남긴다 (fixed/schedule_decision.py:212-227)
oob = json.loads(m.decide_final_slot.invoke({'candidate_slots': CANDS, 'selected_index': 9}))
assert oob['final_slot'] is None, '범위 밖 index인데 최종 시간을 확정함: ' + str(oob['final_slot'])
assert '범위' in oob['reason'], '범위 초과 사유가 reason에 남지 않음 (인자를 가공했을 가능성): ' + str(oob['reason'])
print('DECIDE_OK')
"
```
확인 포인트: 반환이 `decide_final_slot_payload(...)` 결과를 **가공 없이** 담고,
`final_slot` 자동 선택 금지 규칙이 지켜진다. 이 tool이 스스로 후보를 고르면 FAIL(가이드 line 98·102).

## 9. nana_agent — fake 하위 agent 주입으로 반환 계약 검증 (메인과제 · 키 불필요)
```bash
uv run python -X utf8 -c "
import json
from langchain_core.messages import AIMessage, ToolMessage
import student_parts.week06_kanamate_decides_schedule as m

class FakeAgent:
    def __init__(self, messages): self.messages, self.calls = messages, []
    def invoke(self, payload):
        self.calls.append(payload)
        return {'messages': self.messages}

inner = json.dumps({'ok': True, 'tool_name': 'personal_list_saved_schedules', 'rows': [{'title': '팀 리뷰'}]}, ensure_ascii=False)
fake = FakeAgent([
    AIMessage(content='', tool_calls=[{'name': 'personal_list_saved_schedules', 'args': {}, 'id': 'c1'}]),
    ToolMessage(content=inner, name='personal_list_saved_schedules', tool_call_id='c1'),
    AIMessage(content='저장된 일정은 팀 리뷰입니다.'),
])
m._NANA_SUBAGENT = fake   # create_agent 경로를 타지 않으므로 PROXY_TOKEN 불필요

out = json.loads(m.nana_agent.invoke({'query': '내 일정 알려줘'}))
print('nana keys =', sorted(out))
for key in ('answer', 'trace', 'inner_tool_names'):
    assert key in out, 'nana_agent 반환에 ' + key + ' 누락 (가이드 line 76/488): ' + str(sorted(out))
assert out['answer'] == '저장된 일정은 팀 리뷰입니다.', 'extract_final_text 미사용: ' + str(out['answer'])
assert out['inner_tool_names'] == ['personal_list_saved_schedules'], 'inner_tool_names 불일치: ' + str(out['inner_tool_names'])
assert out.get('selected_agent') == 'nana_agent', \"selected_agent가 'nana_agent'가 아님: \" + str(out.get('selected_agent'))
events = out['trace']['events'] if isinstance(out['trace'], dict) else out['trace']
assert any(e.get('event') == 'tool_call' for e in events), 'trace에 tool_call 이벤트가 없음: ' + str(events)[:200]

# query가 user 메시지로 하위 agent에 전달됐는가
sent = json.dumps(fake.calls, ensure_ascii=False, default=str)
assert '내 일정 알려줘' in sent, 'query가 하위 agent에 전달되지 않음: ' + sent[:200]
assert 'user' in sent, 'query를 user 메시지로 넘기지 않음: ' + sent[:200]

# 재사용: 두 번째 호출에서도 같은 객체를 쓴다 (가이드 line 485-486)
m.nana_agent.invoke({'query': '두 번째'})
assert m._NANA_SUBAGENT is fake, '_NANA_SUBAGENT를 매번 새로 만듦 (재사용 규칙 위반)'
assert len(fake.calls) == 2, 'invoke 횟수 불일치: ' + str(len(fake.calls))
print('NANA_AGENT_OK')
"
```
확인 포인트: `_NANA_SUBAGENT`가 이미 있으면 **재생성하지 않고** 재사용하며,
`extract_agent_events` / `extract_final_text`로 뽑은 `answer`/`trace`/`inner_tool_names`(+`selected_agent`)를
JSON 문자열로 반환한다.
> `selected_agent` 단언이 실패하면 먼저 **가이드 line 488이 요구한 키인지**를 확인한다(요구 키다).
> 반대로 `trace` 형태(dict vs list)는 가이드가 못박지 않아 양쪽을 모두 허용한다.

## 10. kana_agent — final_slot_payload 끌어올리기 (메인과제 · 키 불필요)
```bash
uv run python -X utf8 -c "
import json
from langchain_core.messages import AIMessage, ToolMessage
import student_parts.week06_kanamate_decides_schedule as m

class FakeAgent:
    def __init__(self, messages): self.messages, self.calls = messages, []
    def invoke(self, payload):
        self.calls.append(payload); return {'messages': self.messages}

find_out = json.dumps({'ok': True, 'tool_name': 'find_common_available_slots', 'candidate_slots': [
    {'date': '2026-07-10', 'start_time': '11:00', 'end_time': '12:00', 'duration_minutes': 60, 'reason': 'b'}]}, ensure_ascii=False)
decide_out = json.dumps({'final_slot': '2026-07-10 11:00-12:00', 'reason': '둘 다 비어 있음',
    'candidates': ['2026-07-10 11:00-12:00'], 'needs_agent_selection': False}, ensure_ascii=False)
fake = FakeAgent([
    AIMessage(content='', tool_calls=[{'name': 'collect_member_schedules', 'args': {'member_names': ['철수']}, 'id': 'c1'}]),
    ToolMessage(content=json.dumps({'ok': True, 'rows': []}), name='collect_member_schedules', tool_call_id='c1'),
    AIMessage(content='', tool_calls=[{'name': 'find_common_available_slots', 'args': {}, 'id': 'c2'}]),
    ToolMessage(content=find_out, name='find_common_available_slots', tool_call_id='c2'),
    AIMessage(content='', tool_calls=[{'name': 'decide_final_slot', 'args': {}, 'id': 'c3'}]),
    ToolMessage(content=decide_out, name='decide_final_slot', tool_call_id='c3'),
    AIMessage(content='7월 10일 11:00-12:00으로 제안합니다.'),
])
m._KANA_SUBAGENT = fake

out = json.loads(m.kana_agent.invoke({'query': '철수랑 회의 시간 잡아줘'}))
print('kana keys =', sorted(out))
for key in ('answer', 'trace', 'inner_tool_names', 'final_slot_payload', 'final_decision_payload'):
    assert key in out, 'kana_agent 반환에 ' + key + ' 누락 (가이드 line 82/499): ' + str(sorted(out))
assert out['inner_tool_names'] == ['collect_member_schedules', 'find_common_available_slots', 'decide_final_slot'], out['inner_tool_names']
fsp = out['final_slot_payload']
assert isinstance(fsp, dict) and fsp.get('final_slot') == '2026-07-10 11:00-12:00', \
    'decide_final_slot 결과를 final_slot_payload로 끌어올리지 못함 (가이드 line 81): ' + str(fsp)
assert out['answer'] == '7월 10일 11:00-12:00으로 제안합니다.', out['answer']

# final_slot이 하위 trace에 없으면 끌어올릴 것도 없다 — 지어내면 안 된다 (임의값 금지)
bare = FakeAgent([AIMessage(content='아직 후보를 못 찾았습니다.')])
m._KANA_SUBAGENT = bare
out2 = json.loads(m.kana_agent.invoke({'query': '아무거나'}))
assert not out2.get('final_slot_payload'), 'decide_final_slot을 안 불렀는데 final_slot_payload를 만들어냄: ' + str(out2.get('final_slot_payload'))
assert out2['inner_tool_names'] == [], out2['inner_tool_names']
print('KANA_AGENT_OK')
"
```
확인 포인트: 하위 trace에서 `final_slot`이 든 dict를 찾아 `final_slot_payload`로 올리고,
없으면 **지어내지 않는다**(양방향 검사). `final_decision_payload`는 `propose_group_schedule` 계열
`final_decision` 값을 담는 자리이므로 없으면 `None`이어도 된다.

## 11. 배선 대조 — supervisor_tools / agent_tool_names / extract_langchain_trace
```bash
uv run python -X utf8 -c "
import json
import student_parts.week06_kanamate_decides_schedule as m
import student_parts.week04_retrieve_nanas_memory as w4

sup = [m.tool_name(t) for t in m.supervisor_tools()]
assert sup == ['nana_agent', 'kana_agent'], 'supervisor가 볼 수 있는 tool은 두 개뿐이어야 함 (가이드 line 43/60): ' + str(sup)
kana = [m.tool_name(t) for t in m.kana_tools()]
print('kana tools =', kana)
assert 'extract_schedule_request' in kana and 'collect_member_schedules' in kana, kana
assert 'personal_list_saved_schedules' not in kana, 'Nana 담당 개인 일정 tool이 Kana에 섞임: ' + str(kana)
assert m.agent_tool_names('nana_agent') == [m.tool_name(t) for t in w4.week04_tools()], 'nana_agent tool 목록이 week04_tools()가 아님'
assert m.agent_tool_names('kana_agent') == kana and m.agent_tool_names('supervisor') == sup
assert m.agent_tool_names('없는이름') == []

# extract_langchain_trace: supervisor 결과에서 위임 대상과 inner tool을 끌어올린다 (제공 코드 회귀)
class FakeMsg:
    type = 'tool'
    def __init__(self, name, content): self.name, self.content, self.tool_call_id = name, content, 'x'
class FakeAI:
    type = 'ai'
    def __init__(self, tool_calls=None, content=''): self.tool_calls, self.content = tool_calls or [], content
inner = json.dumps({'answer': 'a', 'inner_tool_names': ['collect_member_schedules', 'decide_final_slot'],
                    'final_slot_payload': {'final_slot': '2026-07-10 11:00-12:00'}}, ensure_ascii=False)
tr = m.extract_langchain_trace({'messages': [
    FakeAI(tool_calls=[{'name': 'kana_agent', 'args': {'query': 'q'}, 'id': 'c1'}]),
    FakeMsg('kana_agent', inner),
    FakeAI(content='최종 답변'),
]})
assert tr['supervisor_selected_agent'] == 'kana_agent', tr
assert tr['inner_tool_names'] == ['collect_member_schedules', 'decide_final_slot'], tr
assert tr['final_slot_payload']['final_slot'] == '2026-07-10 11:00-12:00', tr
print('WIRING_OK')
"
```
확인 포인트: supervisor tool은 **정확히 두 개**, Kana에 개인 일정 tool이 섞이지 않고,
`agent_tool_names`의 3분기 + 미지정 이름 빈 목록이 유지된다.
추가 과제를 구현하지 않기로 했다면 `kana_tools()`에서 `find_common_available_slots`/`decide_final_slot`이
빠져 있어야 하고(가이드 line 85·125-126), 그때 5~8단계는 **N/A(범위 밖)** 로 보고한다.

## 12. propose_group_schedule 회귀 (제공 구현 — 변경되면 FAIL)
```bash
uv run python -X utf8 -c "
import json
import student_parts.week06_kanamate_decides_schedule as m
r = json.loads(m.propose_group_schedule.invoke({'title': '회의', 'member_names': ['철수'],
    'candidate_slots': [{'date': '2026-07-10', 'start_time': '11:00', 'end_time': '12:00', 'duration_minutes': 60, 'reason': 'b'}],
    'selected_slot': {'date': '2026-07-10', 'start_time': '11:00', 'end_time': '12:00', 'duration_minutes': 60, 'reason': 'b'}}))
assert r['ok'] is True and r['tool_name'] == 'propose_group_schedule', r
fd = r['final_decision']
assert fd['status'] == 'confirmed' and fd['members'] == ['철수'] and fd['selected_slot'], fd
assert 'propose_group_schedule' not in [m.tool_name(t) for t in m.kana_tools()], 'kana_tools()에 들어가서는 안 됨 (가이드 line 116)'
print('PROPOSE_REGRESSION_OK')
"
```
확인 포인트: 구현 완료 상태가 유지되고 `kana_tools()`에는 들어가지 않는다.

## 13. agent 조립 (선택 — `PROXY_TOKEN` 있을 때만)
```bash
uv run python -X utf8 -c "
from fixed.config import CONFIG
import student_parts.week06_kanamate_decides_schedule as m
if not CONFIG.has_openai_key:
    print('SKIP: PROXY_TOKEN 없음 — agent 조립은 12단계에서만 필요')
else:
    m._SUPERVISOR_AGENT = None
    a = m.build_langchain_supervisor_agent()
    assert a is not None and m.build_week_agent() is a, 'supervisor agent가 재사용되지 않음'
    print('AGENT_BUILD_OK type =', type(a).__name__)
"
```
확인 포인트: supervisor agent가 **한 번만** 만들어지고 `build_week_agent()`가 같은 객체를 돌려준다.

---

## 보고 형식

각 단계 → `PASS` / `FAIL` / `N/A(사유)` + 실행 원문 출력. FAIL은 **가이드/`fixed/` 계약 위반(구현 결함)** 과
**assertion이 좁아서 생긴 오판(skill 완화 후보)** 을 구분해 적는다. **코드는 절대 수정하지 않는다.**

이 skill은 **결정적 계약만** 판정한다. 위임이 알맞은 하위 agent로 가는지, Kana가 `find_common_available_slots`
→ `decide_final_slot`을 실제로 이어 부르는지는 **확률적 행동**이므로 `evals/week06_eval.py`가 통과율로 잰다.
