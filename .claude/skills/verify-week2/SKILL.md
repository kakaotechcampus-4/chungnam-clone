---
name: verify-week2
description: Week 2 구현(student_parts/week02_structure_natural_language_requests.py)을 키 없이 정적 검증한다. py_compile, 모듈 import, StructuredRequest/StructuredRequestBatch 인스턴스화와 필드/기본값/description을 확인할 때 사용.
allowed-tools: Bash(uv *)
---

# Week 2 정적 검증 (Static verification)

`PROXY_TOKEN` 없이 실행 가능한 정적 검증만 수행한다. 각 단계 명령을 실행하고 원문 출력과 함께 PASS/FAIL을 보고한다. 코드는 수정하지 않는다.

## 1. 구문 검사 (py_compile)
```bash
uv run python -m py_compile student_parts/week02_structure_natural_language_requests.py
```

## 2. import + 스키마 인스턴스화 스모크 테스트
```bash
uv run python -c "import student_parts.week02_structure_natural_language_requests as m; r=m.StructuredRequest(kind='personal_schedule'); b=m.StructuredRequestBatch(requests=[r]); print('kind=', r.kind, '| title=', r.title, '| members=', r.members, '| original_text=', repr(r.original_text)); print('batch.base_date=', b.base_date, '| len(requests)=', len(b.requests)); print('week02_tools len=', len(m.week02_tools())); assert m.StructuredRequestBatch().requests == []; print('OK')"
```
확인 포인트: `kind` 필수 동작, `title=None`, `members=[]`, `original_text=''`, `base_date`가 오늘 날짜, `week02_tools()` 길이 3.

## 3. 필드 스펙 대조
```bash
uv run python -c "import student_parts.week02_structure_natural_language_requests as m; f=m.StructuredRequest.model_fields; print({k:(str(v.annotation), v.is_required(), bool(v.description)) for k,v in f.items()}); print({k:(str(v.annotation), v.is_required(), bool(v.description)) for k,v in m.StructuredRequestBatch.model_fields.items()})"
```
확인 포인트: `kind`만 `is_required=True`, 나머지 필드는 기본값 보유, 모든 필드에 description 존재.

## 보고
- 각 단계 명령 + 원문 출력 + PASS/FAIL.
- 실패 항목은 무엇이·왜 어긋났는지 근거(`file:line`)와 함께 명시.
- 세 단계 모두 통과할 때만 "전체 통과"로 결론.
