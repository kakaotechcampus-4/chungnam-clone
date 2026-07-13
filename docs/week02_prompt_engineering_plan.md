# Week 2 — 프롬프트 정교화 plan

대상 파일: [`student_parts/week02_structure_natural_language_requests.py`](../student_parts/week02_structure_natural_language_requests.py)
수정 범위: `week02_prompt_parts()` · `week02_system_prompt()` **내부만**
작업 브랜치: `yoojongho/week2`

> 이 문서는 2주차 **프롬프트 설계 근거와 결정**을 남긴다. 스키마·tool·agent 빌더 구현은
> [week02_subagent_setup_plan.md](week02_subagent_setup_plan.md) 흐름으로 이미 완료됐고, 여기서는 프롬프트 품질만 다룬다.

---

## 1. 배경 (Context)

Week 2는 자연어/Week1 tool JSON을 `StructuredRequestBatch`로 **구조화**하는 과제다.
6개 TODO 구현과 클래스 docstring 보강은 끝났고, 남은 개선 대상은 **system prompt의 품질**이다.

프롬프트 엔지니어링 강의 노트에서 도출한 원칙을 적용한다. 같은 원칙을 에이전트가 매번 자동으로
적용하도록 [`.claude/skills/prompt-engineering/SKILL.md`](../.claude/skills/prompt-engineering/SKILL.md)로도 인코딩해
planner·builder에 preload했다.

### 강의에서 도출한 원칙 (요약)
| 원칙 | 내용 |
| --- | --- |
| 기법 4종 | zero-shot / **few-shot** / Instruction / CoT |
| **구조화 출력 = few-shot** | "return 받고 싶은 형태를 그대로 보여주면 그대로 출력한다" |
| Instruction | 허용값을 명시 제약("N개 중 하나로만"), 최종 출력 형식 강제 |
| CoT | 다단계 판단을 번호로 분해, 각 단계에 "근거 없으면 null" 탈출 조건 |
| tool description | tool 검색의 **입력** → 언제 선택되는지 + 인자 형식 |
| 배치·길이 | **앞쪽 프롬프트가 중요**, markdown 구조화, **lost in the middle** → 예시 1~2개로 절제 |
| 검증 | agent는 컴파일 에러가 없다 → **trace로 검증·튜닝**, tool call 최소화 |

### 순서 결정 (냉정한 판단)
verifier는 **정적 검증**이라 프롬프트 *품질*을 판정할 수 없다. 곧 바뀔 파일을 미리 검증하면 재검증 패스가 낭비된다.
→ **프롬프트 정교화를 먼저 끝내고 → verifier를 최종본에 1회** 실행한다. 품질의 진짜 검증은 `./run.sh --week2` **라이브 trace**.

---

## 2. 적용안 (4가지)

1. **few-shot 예시 2개 추가** — 자연어 입력 → `StructuredRequestBatch` JSON. 정보가 **충분한 케이스 1개 + 불충분한 케이스 1개**.
2. **CoT 절차 5단계** — ①`kind` 분류 → ②상대 날짜 → `YYYY-MM-DD` → ③시간 → `HH:MM` → ④`members` 추출 → ⑤불확실하면 `null`/빈 리스트.
3. **Instruction 제약 강화** — `kind`는 5개 값 **중 하나로만**, 최종 답변은 **반드시** `StructuredRequestBatch` structured_response.
4. **markdown 구조화 + 핵심 규칙 앞쪽 배치** — 조각을 헤더/불릿으로 구성.

---

## 3. 구조적 제약 (실제 코드 확인 결과)

- **`join_system_prompt`** ([week01_wake_up_nana.py:35](../student_parts/week01_wake_up_nana.py#L35))
  = `"\n\n".join([header, *strip된 조각들])` → 조각 **내부의 markdown·개행은 그대로 보존**된다. 빈 문자열 조각은 drop.
- header가 **"뒤 지시를 우선한다"**고 명시 → 가장 강한 제약(`structured_response_rule`)을 **마지막**에 두는 현재 구조가 이미 최적. 유지.
- **f-string 금지** — few-shot JSON의 `{`/`}` 때문에 few-shot 조각은 일반 삼중따옴표로 작성. 역할/오늘 날짜 조각만 f-string.
- `week02_prompt_parts()`의 첫 원소들 `*week01_prompt_parts()`(**6개**)는 **그대로 유지**. week02 조각 6개 추가 → 총 **12개**.
  (초기 계획은 week01을 5개로 잘못 셌다. builder가 실제 실행으로 6개임을 확인 — 검증 스크립트는 하드코딩 대신 `len(week01_prompt_parts())`를 쓴다.)
- `next_weekday_iso` import는 수정 범위 밖 → few-shot 날짜를 계산해 넣을 수 없다.
- `personal_create_schedule`의 `end_time` 기본값이 문자열 `"미정"` ([week01:176](../student_parts/week01_wake_up_nana.py#L176)) → `HH:MM`이 아니다.

---

## 4. 설계 결정

| # | 결정 | 근거 |
| --- | --- | --- |
| 1 | few-shot 날짜는 **구체 날짜 + 가드 문장** | "그대로 보여주면 그대로 출력한다"는 few-shot 효과를 살리기 위함. 가드: "아래 날짜는 예시용 가정이며 실제 변환은 위에 제시된 오늘 날짜를 기준으로 한다" |
| 2 | 가정 날짜 = **2026-05-11(월) → 다음 주 화요일 = 2026-05-19** | 검증 완료: 2026-05-11은 월요일이고, 프로젝트 `next_weekday_date` 규칙(다음 월요일 = +7−weekday, 화요일 = +1)상 2026-05-19(실제 화요일) |
| 3 | 예시2(`"조만간 팀 회고 한번 하자"`)의 `kind` = **`group_schedule`** | `members`가 빈 리스트인 group 예시가 "모르면 비워둔다"를 잘 전달 |
| 4 | **`end_time`이 `"미정"`처럼 HH:MM이 아니면 `null`** 규칙 추가 | week01 tool의 `end_time` 기본값이 `"미정"` → "지어내지 않는다" 원칙의 구체화 |
| 5 | few-shot은 **정확히 2개** (3개 이상 금지) | lost in the middle 대비 |
| 6 | `structured_response_rule`은 `week02_system_prompt()` **내부 지역 문자열**, 조각 **맨 마지막** | `join_system_prompt` header의 "뒤 지시 우선" 규약 |
| 7 | 예시1 `kind` = `personal_schedule` + **분류 기준 한 줄 추가**("본인 캘린더=personal / 팀 전체가 시점 정하는 모임=group") | 두 예시(멤버 있는 personal, 멤버 없는 group)로부터 모델이 역상관을 잘못 일반화하는 것 방지 |
| 8 | **정보 부족 시 되묻지 않고** null로 채운 structured_response 반환 (명시 문장으로 추가) | week01의 되묻기 지시(`week01:283-285`)와 충돌. "뒤 지시 우선" 규약에 암묵 의존하지 않고 명시적으로 오버라이드 |
| 9 | **"structured_response는 정확히 하나의 JSON 객체로만 낸다"** 제약 추가 | 라이브에서 `StructuredOutputValidationError: Extra data`(JSON 뒤 추가 텍스트) 1회 관측. few-shot이 JSON 블록을 2개 보여주므로 모델이 복수 블록을 흉내낼 여지가 있음 |
| 10 | **조회·삭제 요청도 `requests`를 비우지 않고** `kind=unknown` 하나를 담고 `original_text`에 원문 | 라이브에서 "내 일정 전부 보여줘" → `requests=[]` 관측. 가이드의 "요청이 하나뿐이어도 리스트에 하나를 담는다"와 어긋남. `RequestKind`에 조회/삭제 값이 없으므로 `unknown`이 타당 |

### `reason` 문체 모방 완화
verifier가 "라이브 `reason`이 few-shot 문구를 거의 그대로 재사용한다"고 관측 → ①가드 문장에
`"reason도 예시 문구를 베끼지 말고, 실제 입력에 근거해 직접 짧게 쓴다."` 추가, ②예시 `reason`을 짧게 축약.
결과: `reason`이 실제 입력 문구를 인용해 근거화됨(예: `"상대 날짜 '다음 주 화요일'과 시간 '오후 3시'를 오늘 기준으로 환산. 종료 시각 미언급."`).

### 스타일 정리
`role_part`의 3개 리터럴에 붙어 있던 불필요한 `f` 접두사 제거(placeholder가 있는 마지막 줄만 유지).
AST `JoinedStr` 1개 유지, 프롬프트 출력 문자열 불변(3367자 동일)을 확인한 **동작 보존 리팩터링**.

### 보존해야 할 기존 지시 (verifier 확인 대상)
역할 + 오늘 날짜 기준 상대 날짜 해석 / 필드 구조화 / 지어내지 않음(`None`·빈 list, `YYYY-MM-DD`·`HH:MM`) /
`created_schedule` 재호출 없이 읽기 + **`attendees` → `members`** / SQLite·RAG·외부 멤버 조율 안 함 /
(system_prompt) 최종답변 `StructuredRequestBatch` · 단건도 `requests` 리스트 · `created_schedule` 읽어 채우기.

---

## 5. 조각 구성

`week02_prompt_parts()` 반환 리스트:
```
*week01_prompt_parts()            # 6개, 그대로 유지
① 역할 + 오늘 날짜                 # f-string
② Week2 핵심 구조화 규칙            # kind는 5개 중 하나만
③ CoT 절차 5단계
④ Week1 tool 결과 처리             # attendees→members, end_time "미정"→null
⑤ Week2 범위 제한                  # SQLite/RAG/외부조율 안 함
⑥ few-shot 출력 예시 2개           # 일반 문자열(f-string 금지)
```
`week02_system_prompt()`:
```
structured_response_rule = "## 최종 출력 규칙 (가장 우선한다)" + 4개 불릿
return join_system_prompt([*week02_prompt_parts(), structured_response_rule])
```

---

## 6. 검증 계획

### 6-1. 정적 검증 (키 불필요, 필수)
```bash
uv run python -m py_compile student_parts/week02_structure_natural_language_requests.py
```
- `week02_prompt_parts()[:len(w1)] == week01_prompt_parts()` (week01 조각 보존 — **주 계약**)
- `len(week02_prompt_parts()) == len(week01_prompt_parts()) + 6` (= 12, 보조 계약. 조각 수를 하드코딩하지 않는다)
- `week02_system_prompt()`에 필수 키워드 포함: 오늘 날짜, `kind` 5종, `StructuredRequestBatch`, `structured_response`,
  `created_schedule`/`attendees`/`members`, `YYYY-MM-DD`/`HH:MM`, `SQLite`/`RAG`, "지어내"/"빈 리스트", "구조화 절차"/"출력 예시"/`base_date`
- `'### 예시'` 정확히 **2회**, f-string 미치환 흔적(`{current_app_date_iso`) 없음
- 스키마 회귀: `base_date == 오늘`, `members == []`, `date is None`
- [`/verify-week2`](../.claude/skills/verify-week2/SKILL.md) skill로도 재현 가능

### 6-2. 라이브 검증 (품질 판정 — 정적으로 불가)
```bash
./run.sh --week2      # .env의 PROXY_TOKEN 필요
```
| 시나리오 | 확인 포인트 |
| --- | --- |
| "다음 주 화요일 오후 3시에 철수랑 회의 잡아줘" | `base_date`가 **실제 오늘**인지 (‼️ `2026-05-11`로 나오면 **few-shot 날짜 오염**), `date`=실제 다음 주 화요일, `members=["철수"]`, `end_time` null |
| "조만간 회식 한번 하자" | `date`/`start_time` null, `members` `[]`, 지어내기 없음 |
| Week1 tool 경유 | 재호출 없이 `created_schedule.attendees` → `members`, `end_time "미정"` → `null` |

**오염 관측 시 fallback**: 예시의 `base_date`를 플레이스홀더로 바꾸거나, `structured_response_rule`의
base_date 문장을 "예시의 날짜가 아니라 반드시 위에 제시된 오늘 날짜"로 강화한다.

### 6-3. 라이브 검증 실제 결과 (오늘 = 2026-07-08 수요일)
- **few-shot 날짜 오염 없음** — 8회 모두 `base_date=2026-07-08`, "다음 주 화요일" → `2026-07-14`(실제 화요일)
- 생성 요청("내일…만들어줘") → `personal_create_schedule` 호출, `attendees→members`, `end_time "미정"→null` 확인
- 조회·삭제 → `kind=unknown` 하나, `original_text`에 원문 (결정 #10 반영 후)
- **실패 0/8**

### 알려진 이슈 (flaky)
`StructuredOutputValidationError: Extra data`가 **1회** 발생했으나 이후 재현되지 않았다(모델의 간헐적 출력 이상).
결정 #9의 "정확히 하나의 JSON 객체" 제약을 추가했지만, **원래도 간헐적이었으므로 이 한 줄이 원인을 제거했다고 단정할 수 없다.**
재발 시 few-shot 예시를 1개로 줄이거나 JSON 블록 표기를 바꾸는 것을 검토한다.

---

## 7. tool 호출 회귀와 재설계 (라이브가 잡아낸 문제)

### 발견
결정 #10 적용 후 라이브에서 **tool 호출이 사라졌다**: 생성 "…잡아줘" 0/2, 조회 0/2.
원인 3가지 (모두 프롬프트 문구 문제):
1. `## Week 1 tool 결과 처리` 조각이 **"다시 tool을 호출하지 않고"**로 시작 → 호출 억제 신호만 줌. "호출하라"는 말이 없었다.
2. `## 범위 제한`의 "SQLite **저장**을 하지 않는다"를 모델이 "생성 tool을 호출하지 말라"로 오독.
3. 가장 강한 마지막 규칙(`## 최종 출력`)이 전부 "JSON을 내라"여서 tool 호출을 밀어냄. `join_system_prompt` header가 "뒤 지시 우선"이라 week01의 라우팅 규칙이 진다.

### 재설계 (결정 #11~#13)
| # | 결정 | 근거 |
| --- | --- | --- |
| 11 | week02 프롬프트에서 **tool 라우팅을 명시적으로 재확인** (생성→create, 조회→list, 삭제→delete). "구조화만 하면 된다는 이유로 tool 호출을 생략하지 않는다" | week01 규칙이 "뒤 지시 우선"에 밀리므로 week02에서 다시 선언해야 함 |
| 12 | **A-1**: 조회 요청은 `personal_list_schedules` 결과의 **일정들을 각각 StructuredRequest로** 담는다. 결과가 비면 `kind=unknown` 1건 | 모델의 자연스러운 동작이며 정보량이 큼. `requests 구성` 조각으로 분리 |
| 13 | **B 삭제 안전장치**: `schedule_id`·제목·날짜 중 하나로 지목되면 (필요 시 list로 id를 찾아) 삭제한다. `'그 일정'`처럼 아무것도 지목 못 하면 **삭제 tool을 호출하지 않고 구조화만** 한다 | "되묻지 않는다"와 삭제 tool이 결합해 **일정 6건을 임의 삭제**하는 사고가 라이브에서 관측됨 |

> 결정 13의 경계는 **긍정/부정 예시**를 함께 넣어야 작동했다. 예시 없이 "특정하지 못하면"만 쓰자
> 제목으로 특정된 삭제(`"치과 진료 일정 지워줘"`)까지 과잉 차단됐다. (prompt-engineering §2 few-shot 원칙)

### 규칙 정리 (lost-in-the-middle 대응)
- 중복 제거: `"지어내지 않는다"`·`"kind는 다섯 값 중 하나로만"`이 3개 조각에 중복 → **1곳으로 통합**
- CoT 각 단계의 반복되는 "근거 없으면 null" 탈출 조건을 **헤더 한 문장으로 통합**
- `## 최종 출력`을 8불릿 → **5불릿**으로 축소
- **결과**: 중복은 줄었으나 새 규칙 2개(`requests 구성`, 삭제 안전장치) 때문에 총량은 3578 → **4128자로 순증가**.
  추가 축소는 방금 라이브로 검증한 동작을 되돌릴 위험이 커서 하지 않았다. 대신 구조 원칙은 유지:
  핵심 규칙 앞쪽 배치 · 가장 강한 제약 마지막 · few-shot 2개 상한.
- 조각 수: week01 6개 + **week02 7개 = 13개** (`requests 구성` 추가로 6→7)

### 결정 #14~#15 (verifier 2차 검증이 잡아낸 문제)
| # | 문제 | 결정 |
| --- | --- | --- |
| 14 | `"금요일까지 보고서 초안 마무리해야 해"` → `kind=personal_schedule`(should be `todo`), 일부 런에서 `start_time=00:00` **날조** | `## 핵심 규칙`에 분류 기준("마감·기한 있는 해야 할 일은 todo, 알림 요청은 reminder")과 "시각이 없으면 null, `00:00` 같은 기본값 금지"를 추가. few-shot 2개 상한을 지키기 위해 **예시 추가 대신 규칙 문장**으로 해결 |
| 15 | 프롬프트 내부 모순 — `requests 구성`은 삭제를 `unknown`이라 했으나, 삭제 성공 시 모델이 `kind=personal_schedule`을 채움 | **삭제 요청은 수행 여부와 무관하게 항상 `kind=unknown`**으로 일의화 |

> **미해결(의도적)**: `reason` 복사 가드는 부분적으로만 유효하다. 입력이 few-shot 예시1과 구조가 닮으면(날짜+시간+인물)
> 4회 중 1회 예시 문구를 그대로 재현했다. 정확도에는 영향이 없어 그대로 두기로 했다.

### 최종 라이브 결과
| 시나리오 | 결과 |
| --- | --- |
| 생성 "…잡아줘" / "…만들어줘" | `personal_create_schedule` **3/3, 3/3** |
| 조회 "내 일정 전부 보여줘" | `personal_list_schedules` **3/3**, 조회된 일정들을 각각 구조화 |
| 삭제 "그 일정 지워줘" (모호) | **차단 3/3** — tool 미호출, 일정 보존 |
| 삭제 "치과 진료 일정 지워줘" (제목) | `list → delete` **3/3**, 대상 1건만 삭제 |
| 삭제 "\<id\> 일정 지워줘" | `list → delete` **2/2** |
| `base_date` | 전 실행에서 실제 오늘 (few-shot 날짜 오염 없음) |

---

## 7. 이후 단계

프롬프트 정교화(builder) → 정적 검증 → **verifier 위임(최종본 1회, 편향 없는 독립 검증)** → 수렴 → (선택) 라이브 trace.
