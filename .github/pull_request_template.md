## 과제 목표

- Nana가 "내가 적어 둔 참고자료"와 "SQLite에 저장된 일정/할 일 기록"을 구분해서 검색하게 합니다.
- Week 4의 핵심은 RAG를 하나의 마법 함수로 보지 않고, 데이터 출처별 검색 tool을 분리하는 것입니다.

---

## 과제 위치

- 작업 브랜치 : `parkjeonghyeon/week4` → 본인 통합 브랜치 `parkjeonghyeon/final` 로 PR
- 주요 파일 : `student_parts/week04_retrieve_nanas_memory.py`

---

## 과제 범위

이번 PR 에서 어디까지 했는지 체크해요. (해당하는 곳에 모두)

- [x] 1차 과제 완료
- [ ] 2차 과제 완료

---

## 구현한 기능

- [x] add_personal_reference() 함수 구현하기
- [x] search_personal_references() 함수 구현하기
- [x] search_saved_requests() 함수 구현하기

---

## 도전 기능

(수정사항과 피드백 남겨주시면 2차 PR 때 구현해보도록 하겠습니다 ㅜㅜ)

---

### add_personal_reference() 함수 구현하기

- AI 활용 내용 :

```
add_personal_reference는 REFERENCE_STORE.add_personal_reference가 반환하는 딕셔너리에서
backend 정보를 분리해야 하는데 여기서 반환값 구조가 어떻게 생겼는지 알려 줘.
```

위의 프롬프트로 PersonalReferenceStore.add_personal_reference 가 {reference_id, title, content, tags, backend} 형태의 dict를 반환하고, 이 중 backend 는 vector store 메타 정보이므로 나머지 참고자료 본문과 분리해서 reference_backend / reference 두 키로 나눠 담으면 된다는 것을 확인한 뒤, 설계 방향을 간단히 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : tags가 None으로 들어올 수 있으므로 tags or [] 로 빈 리스트 보정을 먼저 한 뒤 REFERENCE_STORE.add_personal_reference(title, content, tags) 에 넘겼다. 반환된 dict에서 backend 키만 reference_backend 로 빼고, 나머지 키들은 dict comprehension으로 걸러서 reference 에 담았다.
- 수정 이유 : tags가 None인 채로 store에 넘어가면 내부에서 ",".join(None) 으로 문제가생기기 때문에 tool 진입 시점에 비어있는 리스트로 바꿔야 한다. 또 backend 정보는 저장 결과 확인용 메타데이터이고 참고자료 본문과 성격이 다르므로, LLM이 응답 근거와 저장소 정보를 혼동하지 않도록 top level에서 분리해야 하기 때문이다.

### search_personal_references() 함수 구현하기

- AI 활용 내용 :

```
search_personal_references는 store 검색 결과가 id, title, content, tags, distance로 flat하게 오는데,
이걸 tool 반환용으로 재정리하려면 어떤 구조가 좋은지 알려 줘. 추가로 top-level 키는 hits로 감싸야 하는 건지도 같이 봐 줘.
```

위의 프롬프트로 store hit의 flat 구조에서 title 과 tags 는 문서 메타데이터 성격이므로 metadata 하위로 묶고, id/content/distance 는 검색 결과 자체이므로 top-level에 두면 된다는 것을 확인한 뒤, course repo 계약에 맞게 {"hits": [...]} 형태로 감싸는 방향을 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : REFERENCE_STORE.search_personal_references(query, limit=top_k) 호출 전에 safe_limit(top_k, default=2, maximum=20) 으로 top_k를 보정했다. 반환된 각 hit에서 id , content , distance 는 그대로 꺼내고, title 과 tags 는 metadata dict 안에 넣어서 재구성한 뒤, 최종 결과를 {"hits": hits} 로 감싸 반환했다.
- 수정 이유 : LLM이 top_k에 비정상적인 값을 넘길 수 있으므로 safe_limit으로 1~20 범위를 보장해야 한다. 또 store가 반환하는 flat 구조를 그대로 쓰면 검색 근거(content)와 부가 정보(title, tags)가 같은 레벨에 섞여서 LLM이 답변 근거를 파싱하기 어려워지므로, metadata로 분리하고 top-level hits 키로 감싸서 계약을 준수해야 하기 때문이다.

### search_saved_requests() 함수 구현하기

- AI 활용 내용 :

```
search_saved_requests는 SQLITE_STORE.search_saved_requests를 호출하면 되는 건지, 시그니처가 (query, limit)인지 (query, kind, limit)인지도 같이 알려 줘.
```

```
결과가 비어 있을 때 빈 배열을 그대로 두면 되는지도 어떻게 되는거지??
```

위의 프롬프트로 AppSQLiteStore.search_saved_requests 의 실제 시그니처가 (query, kind=None, limit=5) 이고, tool에서는 kind 없이 (query, limit=top_k) 만 넘기면 store 쪽에서 kind 조건을 알아서 빼준다는 것을 확인한 뒤, 설계 방향을 간단히 설명 듣고나서 구현하였다.

- 직접 수정한 부분 : safe_limit(top_k, default=3, maximum=50) 으로 top_k를 보정한 뒤, SQLITE_STORE.search_saved_requests(query, limit=top_k) 를 호출해서 결과를 rows 에 담았다. 검색 결과가 비어 있어도 빈 리스트를 그대로 유지하고, 최종 반환은 {"rows": rows} 로 감쌌다.
- 수정 이유 : SQL 쿼리 조립과 LIKE 검색 로직은 store 메서드가 전부 처리하므로 tool에서 쿼리를 직접 쓸 필요가 없고, kind 필터도 store 기본값 None이 조건에서 빠지게 해준다. 또 결과가 없을 때 예외를 던지거나 None을 반환하면 LLM이 오류로 오해할 수 있으므로, rows=[] 를 유지해서 "검색했지만 없다"를 정상 응답으로 전달해야 하기 때문이다.

---

## 구현하면서 고민한 점

고민한 점 : Week 4 RAG 도구를 완성한 뒤 실제로 "출처별 검색"이 동작하게 만드는 과정에서 한가지 문제에 직면했다.

1. 개인 참고자료 검색 도구(search_personal_references)를 구현할 때, 저장소가 돌려주는 검색 결과 구조와 도구가 지켜야 하는 반환 계약이 서로 달라 저장소 결과를 그대로 반환하면 계약을 위반하는 문제이다.
   이 문제는 PersonalReferenceStore.search_personal_references가 id/title/content/tags/distance를 평면 구조로 돌려주는 반면, course repo 계약은 각 hit이 id/content/distance/metadata(title/tags) 구조에 top-level 키가 hits여야 한다는 점을 놓친 것이었고, 여기에 더해 저장소 조회 메서드는 limit 인자를 받는데 도구는 top_k 인자를 노출하는 이름 불일치도 함께 있었다.
   해결 방법 : 우선적으로 클로드 코드에게 질문하였고, 저장소 reference_store.py, app_store.py (fixed 폴더의 저장소 코드들)의 실제 메서드 시그니처와 주차별 프롬프트 상속 구조를 분석해 코드를 수정했다.
   1번 문제는 '헬퍼가 조회 결과를 정리하고 도구가 json payload로 감싸 반환한다'는 설계 분리를 지키면서, search_personal_reference_hits에서 저장소의 평면 결과를 id/content/distance/metadata(title/tags) 구조로 재조립하고 top-level 키를 hits로 맞춰 해결했다.
   같은 맥락에서 add_personal_reference는 저장소가 돌려준 dict를 reference_backend와 reference로 분리했고, search_saved_requests는 도구의 top_k를 저장소의 limit 인자로 매핑하며 결과를 top-level 키 rows로 감싸도록 바로잡았으며, top_k/limit 보정은 safe_limit()으로 도구 안에서 처리했다.
   마지막으로 각 도구가 돌려주는 JSON의 top-level 키가 각각 hits, rows인지 확인하고, 참고자료성 질문과 저장 일정성 질문에 대해 trace에서 search_personal_references와 search_saved_requests가 각각 호출되는지 확인함으로써, 출처별 검색 흐름이 끝까지 동작하는 것을 검증할 수 있었다.

---

## 과제 회고 (KPT)

- **Keep** (좋았고 계속 유지할 점) : AI-first로 문제해결을 맡기고, 직접 판단하는 시간을 가졌는데 나쁘지 않았던 것 같다..
- **Problem** (아쉬웠거나 막혔던 점) : 강사님이 실시간 라이브에서 설명해주시는 내용들을 뭔가 적용해서 이해해보고 싶은데 그게 조금 어려운 것 같다.
- **Try** (다음에 시도해볼 점) : 2차 과제 구현하기
