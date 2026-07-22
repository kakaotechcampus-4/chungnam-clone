# Week 4 — 통합 시나리오 검증 결과 (경계값 10종)

`./run.sh --week4` 앱에서 애매한 경계 질문 10개를 입력해, LLM이 출처(참고자료 references / 저장기록 saved_requests)를 올바르게 라우팅하고 근거(hits/rows)로 답하는지 확인했다. 라우팅은 프롬프트+docstring 기반이라 확률적이므로, "정답"보다 **어느 tool로 갔고 근거로 답했는지**를 관찰한다.

## 준비
- 참고자료(references): seed 3건 — 집중 회의 선호(오전 10~12시) / 점심 보호(12~13시) / 팀 싱크(60분·아젠다).
- 저장기록(saved_requests): `디자인 리뷰 회의`(group_schedule, 2026-07-23 15:00) 저장.
- 기준일: 2026-07-22.

## 결과 요약

| # | 입력 | 호출된 tool | 결과 | 판정 |
|---|---|---|---|---|
| 1 | 회의는 보통 언제 하는 게 좋아? | search_personal_references | 오전 10~12시 선호 답변 | ✅ 의도대로(선호=참고자료) |
| 2 | 다음 주에 회의 있어? | personal_list_saved_schedules(group, 다음주) | "다음 주 저장 회의 없음" | ✅ 저장기록 조회 |
| 3 | 팀 회의 관련 정보 알려줘 | search_saved_requests("팀 회의") → [] | "저장된 팀 회의 없음" | ⚠️ **아쉬움** — 팀 싱크 참고자료(references)가 있는데 그쪽을 안 봄 |
| 4 | 내 집중 시간대 언제였지? | search_personal_references | 오전 10~12시 답변 | ✅ |
| 5 | 금요일 오후에 미팅 잡아도 돼? | extract_schedule_request | "13:00 가능, 등록할까요?" | ⚠️ **주목** — "금요일 오후 외부미팅 안 잡음" 선호를 확인 안 하고 일정생성으로 해석 |
| 6 | 저장한 일정 중에 리뷰 있어? | list_saved_requests("리뷰") | 디자인 리뷰 회의 찾음 | ✅ 저장기록 조회 |
| 7 | 점심 관련 규칙 있었나? | search_personal_references | 점심 보호 규칙 답변 | ✅ |
| 8 | 내일 일정 뭐야? | personal_list_saved_schedules(2026-07-23) → [] | "내일 저장 일정 없음" | ⚠️ **한계** — 내일(07-23) 디자인 리뷰가 group_schedule인데 기본 필터 personal_schedule이라 놓침 |
| 9 | 내가 회의 준비에 대해 적어둔 거 찾아줘 | search_personal_references | 팀 싱크·집중 선호 답변 | ✅ |
| 10 | 아메리카노 얘기 저장돼 있어? | search_personal_references(무관 hit, distance 1.6+) | "저장된 내용 없음" | ✅✅ **우수** — 무관한 hit에도 지어내지 않고 "없다"고 답 |

## 관찰된 특이점 3건 (원인 가설)

### ⚠️ #5 "금요일 오후에 미팅 잡아도 돼?" — 선호 확인 없이 일정 생성으로 해석
- 현상: `extract_schedule_request`로 가서 "13:00 가능, 등록할까요?"라 답함. "금요일 오후 외부 미팅은 안 잡는다"는 참고자료를 **참조하지 않음** → 규칙과 반대되는 "가능" 답변.
- 가설: "미팅 잡아도 돼?"의 "잡"이 일정 생성 신호로 강하게 읽혀, 참고자료 확인 단계를 건너뜀.
- 개선안(옵션): 프롬프트에 "일정을 잡거나 가능 여부를 답하기 전에, 관련 선호/규칙 참고자료를 먼저 확인한다" 규칙 추가.

### ⚠️ #3 "팀 회의 관련 정보 알려줘" — 참고자료를 안 봄
- 현상: `search_saved_requests`(SQL)만 호출 → 저장기록 없어서 "없음". 하지만 seed에 "팀 싱크" 참고자료가 있어 references였다면 유용했음.
- 가설: "팀 회의"의 "회의"가 저장기록 쪽 신호로 읽힘. 진짜 모호한 케이스라 한쪽만 선택.
- 개선안(옵션): 모호할 때 두 출처를 모두 검색하도록 프롬프트 유도(가이드도 "둘 다 선택 가능" 명시).

### ⚠️ #8 "내일 일정 뭐야?" — group_schedule을 놓침
- 현상: `personal_list_saved_schedules`가 기본 `kind=personal_schedule`이라, 내일(07-23)에 있는 디자인 리뷰(group_schedule)를 조회에서 제외 → "없음".
- 성격: **버그가 아니라 스펙**(가이드 "기본 kind=personal_schedule"). 다만 "내일 일정 전체"를 물으면 개인 일정만 봐서 놀랄 수 있는 한계.
- 개선안(옵션): "종류를 특정하지 않은 조회는 kind 필터 없이 전체를 본다"는 규칙, 또는 tool에 kind=None 허용.

### ✅ #10 — 지어내지 않기(가장 중요한 성공)
- 무관한 참고자료만 hit(distance 1.6+)됐는데도 "저장된 내용 없음"이라 정직하게 답함. M4 프롬프트의 "근거 없으면 지어내지 말라"가 실제로 작동.

## 개선 전 trace 기록 (요약)
각 시나리오의 tool_call(인자)과 결과 핵심만 발췌.

```text
#1  search_personal_references(query="회의 선호 시간")
      → hits: ref_focus(1.132), ref_lunch(1.150)
#2  personal_list_saved_schedules(kind=group_schedule, 2026-07-27~08-02)
      → schedules: []  "다음 주 저장 회의 없음"
#3  search_saved_requests(query="팀 회의", top_k=3)
      → rows: []  "저장된 팀 회의 없음"   ← 팀 싱크 참고자료는 미검색
#4  search_personal_references(query="내 집중 시간대")
      → hits: ref_focus(1.364), ref_lunch(1.368)
#5  extract_schedule_request(query="금요일 오후에 미팅 잡아도 돼?")
      → kind=personal_schedule, 2026-07-24 13:00  "가능, 등록할까요?"   ← 선호 규칙 미확인
#6  list_saved_requests(query="리뷰", top_k=3)
      → rows: [디자인 리뷰 회의 / group_schedule / 2026-07-23 15:00]
#7  search_personal_references(query="점심 규칙")
      → hits: ref_lunch(1.319), ref_focus(1.575)
#8  personal_list_saved_schedules(2026-07-23)  (기본 kind=personal_schedule)
      → schedules: []  "내일 저장 일정 없음"   ← 내일의 group_schedule(디자인 리뷰) 누락
#9  search_personal_references(query="회의 준비")
      → hits: ref_focus(1.357), ref_sync(1.417)
#10 search_personal_references(query="아메리카노")
      → hits: ref_sync(1.612), ref_lunch(1.768)  "저장된 내용 없음"   ← 지어내지 않음(우수)
```

## 개선 조치 (프롬프트 규칙 추가)
`week04_prompt_parts`에 경계 교정 규칙 3개 추가:
- ④ 일정 생성/가능여부("~해도 돼?") 답변 전에 관련 선호·규칙을 참고자료에서 먼저 확인 → **#5 대상**
- ⑤ 출처가 모호하면 참고자료+저장기록을 모두 검색해 종합 → **#3 대상**
- ⑥ 종류 불특정 일정 조회는 개인만 보지 말고 저장기록 전체를 확인 → **#8 대상**

## 개선 후 재실행 비교 (#3·#5·#8)

| # | 개선 전 | 개선 후 | 판정 |
|---|---|---|---|
| 3 | saved_requests만 → "없음" | **references+saved 둘 다** 호출, 팀 싱크 메모(distance 0.984) 근거로 답 | ✅ 해결 |
| 5 | extract만 → 규칙 무시 "가능" | extract **+ search_personal_references** 호출(규칙 ④ 발동) | 🟡 부분 |
| 8 | personal만 → 내일 group 누락 | **search_saved_requests(query="2026-07-23")** → 디자인 리뷰 회의 찾음 | ✅ 해결 |

### 개선 후 trace 기록 (요약)
```text
#3  search_personal_references("팀 회의") + search_saved_requests("팀 회의")   ← 두 출처 동시
      → references hit: ref_sync(0.984), ref_lunch(1.234) / saved rows: []
      → "팀 싱크는 60분 이하로 잡고 전날 아젠다 공유" (참고자료 근거로 정답)
#5  extract_schedule_request(...) + search_personal_references("미팅 선호 시간")
      → references hit: ref_lunch(1.382), ref_sync(1.442)  [top_k=2]
      → "점심시간 피해 오후 1시 이후 가능" (참고자료를 확인은 했으나 아래 한계 참고)
#8  search_saved_requests(query="2026-07-23", top_k=10)
      → rows: [디자인 리뷰 회의 / group_schedule / 2026-07-23 15:00]
      → "내일 15시 디자인 리뷰 회의 예정" (정답)
```

### #5 관찰 기록
- 규칙 ④ 이후 동작 변화: 가능여부를 답하기 전에 `search_personal_references`를 실제로 호출함(개선 전에는 호출하지 않음).
- 검색 결과: 질의 "미팅 선호 시간" + top_k=2 기준 상위 2건은 seed의 점심(1.382)·팀싱크(1.442) 메모였고, 사용자가 저장한 "금요일 오후엔 외부 미팅을 잡지 않는다" 메모는 상위 2건에 포함되지 않았음.
- 그 결과 최종 답변은 점심 규칙만 반영해 "오후 1시 이후 가능"으로 나옴.
- (사용자 메모는 준비 단계에서 저장 완료, 새 대화에서 실행함.)

## 결론
- 명확한 케이스(1·2·4·6·7·9·10)는 의도대로 동작. 특히 #10은 무관한 검색 결과에도 지어내지 않고 "없음"으로 답함.
- 경계 케이스: 프롬프트 규칙 ④⑤⑥ 추가 후 #3·#8은 의도대로 교정됨(양쪽 검색 / 전체 조회). #5는 참고자료 검색을 호출하도록 바뀌었으나, 위 관찰대로 사용자 메모가 상위 검색 결과에 잡히지 않아 답변에 반영되지 않음.
