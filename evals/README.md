# Week 2 구조화 agent eval

프롬프트를 조금 바꿀 때마다 결과가 흔들리는 문제를 **재현 가능한 통과율**로 관리하기 위한 eval이다.
멘토 피드백("프롬프트가 미묘하게 달라질 때마다 결과값이 달라진다. 테스트 파이프라인을 어떻게
구성하면 결과값이 동일하게 나오는가?")에 대한 실증 답으로 만들었다.

이것은 업계에서 말하는 **골든 데이터셋(golden dataset / eval set)**이다 — (입력, 기대) 쌍을 버전관리에
고정해두고 모델·프롬프트가 바뀔 때마다 다시 돌려 회귀를 잡는 방식으로, 소프트웨어 테스트의
golden master / snapshot testing 계보에 있다.
단, LLM 출력은 확률적이라 **정확한 기대 출력(문자열)을 얼릴 수 없다.** 그래서 여기서는 기대 출력 대신
**기대 판정(필드 단정) + 통과율**을 계약으로 고정한다(`reason` 같은 자유 서술은 검사에서 제외).

과제 코드(`student_parts/`, `fixed/`)는 **import만** 하고 수정하지 않는다.

## 실행

```bash
uv run python -X utf8 evals/week02_eval.py --n 3                        # 실행
uv run python -X utf8 evals/week02_eval.py --n 3 --save evals/baseline.json   # 기준선 저장
uv run python -X utf8 evals/week02_eval.py --n 3 --baseline evals/baseline.json  # 기준선과 비교
```

exit code: 게이트 통과 `0` / 실패 `1` (CI 연동 가능).

## 7단계 파이프라인

| 단계 | 구현 |
| --- | --- |
| 1. 입력 고정 | 시계(`APP_TODAY=2026-03-04`) · 상태(`PERSONAL_SCHEDULES` 초기화+seed) · **호출 채널** 고정 |
| 2. 검사 항목 | `CASES` 골든셋 10개 |
| 3. 반복 | `--n` (기본 3) |
| 4. 판정 | structured output의 **필드 단정**. `reason` 등 자유 서술은 검사하지 않는다 |
| 5. 집계 | 케이스별 통과율 `n/N` |
| 6. 비교 | `--save` / `--baseline` 로 프롬프트 수정 전후 diff |
| 7. 게이트 | critical 1회 실패 = 전체 실패, non-critical 임계 통과율, non-zero exit |

## ⚠️ 채널을 고정해야 하는 이유 (가장 중요)

같은 프롬프트·모델·입력이라도 **호출 경로가 다르면 결과값이 달라진다.**

| 채널 | `"아침에"` → `start_time` |
| --- | --- |
| `chat_model().with_structured_output(...)` | `08:00` (지어냄) |
| `build_week02_agent()` (실제 앱, `create_agent`) | `None` (규칙 준수) |

차이는 structured output 방식이 아니라 `create_agent`가 만드는 **실행 경로(tool 바인딩 포함)**에 있다.
그래서 eval은 반드시 **서비스와 같은 채널(`build_week02_agent()`)**로 잰다. 다른 채널로 잰 수치는 무의미하다.

## 시계를 예시 날짜와 다르게 고정하는 이유

프롬프트 few-shot 예시가 `2026-05-11 → 2026-05-19`를 쓴다. 시계를 그 날짜로 고정하면
모델이 예시를 **그대로 베껴도 정답처럼 보여** 오염 버그를 놓친다. 그래서 겹치지 않는 `2026-03-04`(수)로 고정한다.

## 골든셋 (10항목)

| id | 검사 | 태그 |
| --- | --- | --- |
| `personal_full` | 생성 + 상대날짜·시간·멤버 정규화 | |
| `personal_boundary` | "팀원들이랑 회의 잡아줘" → personal | **ambiguous** |
| `group_vague` | group 분류 + 불충분 비우기 | |
| `todo_deadline` | 마감 있는 할 일 → todo | |
| `reminder_notime` | "아침에" → `start_time=None` (`08:00` 날조 금지) | **critical** |
| `multi_request` | 한 문장 다중 요청 → `requests` 2개 | |
| `date_leak` | few-shot 날짜(`2026-05-*`)가 출력에 새지 않음 | **critical** |
| `delete_vague` | "그 일정" → 삭제 tool 미호출, 일정 보존 | **critical** |
| `delete_specific` | 제목 특정 삭제 수행 + `kind=unknown` | |
| `list_query` | 조회 결과 각각 구조화 (A-1) | |

- **critical (3)**: 값 날조 / 날짜 오염 / 오삭제. **1회라도 실패하면 게이트 FAIL.** 데이터 파괴·규칙 위반이라 무관용.
- **ambiguous (1)**: `personal_boundary`. "팀원들이랑"(참석자=팀)인데 "잡아줘"(시점 이미 지시)라 personal/group
  신호가 충돌하는 **본질적으로 모호한 입력**. 측정상 personal ~56%(10/18)이며, 사람도 단정하기 어렵다.
  프롬프트로 억지로 한쪽에 밀지 않고 **통과율만 관측**한다(게이트 제외).

## 알려진 관찰

- **작은 표본의 위험**: `personal_boundary`는 N=10에서 `GGGGPPGPPP`처럼 결과가 몰려 나온다.
  N=3만 봤으면 `GGG`(완전 실패)로 오판했을 것이다. 확률적 출력은 표본을 키워 통과율로 봐야 한다.
- `reason` 필드는 few-shot 문구를 복사하는 경향이 있어 **판정에서 제외**했다.
