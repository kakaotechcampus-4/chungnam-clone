# Week 02 구현 계획 — 자연어 요청 구조화

## 목표
자연어 요청(또는 Week1 tool JSON)을 `StructuredRequestBatch`로 변환하는 LangChain agent를 완성한다.
데이터 저장은 하지 않고, 구조화된 Batch 객체 반환만 목적이다.

## 구현 대상 (파일: `student_parts/week02_structure_natural_language_requests.py`)

### 1. `StructuredRequest` 스키마
CLAUDE.md의 Hard constraint 규격 그대로 구현. 모든 필드에 한국어 `description` 부착.

| 필드 | 타입 | 기본값 |
|---|---|---|
| kind | `RequestKind` (Literal) | — (필수) |
| title | `str \| None` | None |
| date | `str \| None` | None (확실할 때만 YYYY-MM-DD) |
| start_time | `str \| None` | None (확실할 때만 HH:MM) |
| end_time | `str \| None` | None (확실할 때만 HH:MM) |
| members | `list[str]` | `default_factory=list` |
| priority | `str \| None` | None |
| reason | `str \| None` | None |
| original_text | `str` | "" |

- 가이드 주석은 title도 `str | None`(기본 None)을 요구하므로 이를 따른다.
  (CLAUDE.md 표에는 title이 str로 되어 있으나, 파일 내 구현 TODO 지시가 더 구체적이라 우선.)

### 2. `StructuredRequestBatch` 스키마
- `requests: list[StructuredRequest]` — `default_factory=list`. 요청이 하나여도 list 유지.
- `base_date: str` — `default_factory=current_app_date_iso`. 상대 날짜 해석 기준일.
- 두 필드 모두 한국어 description 부착.

### 3. `week02_tools()`
- `week01_tools()`를 그대로 반환 (Week1 자산 누적 상속).

### 4. `week02_prompt_parts()`
- `week01_prompt_parts()` 위에 append:
  - Week2 구조화 agent 역할 + 현재 날짜(`current_app_date_iso()`) 기준 명시
  - 자연어를 StructuredRequest 필드(kind/title/date/start_time/end_time/members 등)로 구조화
  - 모르는 값 억지 생성 금지 → None / 빈 리스트
  - Week1 tool JSON을 받은 경우 tool 재호출 없이 payload(created_schedule) 읽어 구조화
  - Week2에서는 SQLite 저장 / RAG / 외부 멤버 일정 조율 하지 않음 명시

### 5. `week02_system_prompt()`
- `join_system_prompt(...)`로 `week02_prompt_parts()` + 최종 답변 규칙 결합:
  - 최종 답변은 `StructuredRequestBatch` 형식
  - 요청이 하나여도 requests 목록에 담기
  - personal_create_schedule 결과 JSON의 created_schedule 읽어 필드 채우기

### 6. `build_week02_agent()` + 실행기 엔트리 포인트 연결
- `CONFIG.has_openai_key` 없으면 `RuntimeError("PROXY_TOKEN이 .env에 필요합니다.")`
- 전역 `_WEEK02_AGENT` 재사용, 없을 때만 생성
- `create_agent(model=chat_model(), tools=week02_tools(), response_format=StructuredRequestBatch, system_prompt=week02_system_prompt())`
- **엔트리 포인트 확인**: 런타임 실행기(`run.sh`)가 호출하는 표준 함수는 `build_week_agent()`이다.
  현재 파일 하단(178~181행)에 `build_week_agent()`가 이미 `return build_week02_agent()`로 구현되어 있으므로,
  이 연결이 그대로 유지되는지 확인한다(신규 구현 아님, 수정하지 않음). 구현한 agent가 실행기로 정상 노출되는지 보장.

## 건드리지 않는 것
- 예약 함수(`_coerce_structured_request`, `extract_structured_request`, `extract_schedule_request`)는 이후 회차용이므로 `...` 유지.
- Week1 파일은 수정하지 않음.

## 검증
### 정적 테스트
1. `python -c "import ..."` 로 문법/import 확인
2. 스키마 인스턴스화 및 필드 description 존재 확인
3. (키 없으면) build 시 RuntimeError 확인

### 통합(E2E) 테스트 — 가이드 명시 시나리오
4. `./run.sh --week2` 실행 후 `"다음 주 화요일 오후 3시에 철수랑 회의 잡아줘"` 입력 →
   최종 답변이 `StructuredRequestBatch` 형식의 `structured_response`로 파싱되는지 확인.
   - 기대: `requests`에 `StructuredRequest` 1건(kind=`personal_schedule` 또는 `group_schedule`,
     date=다음 주 화요일 YYYY-MM-DD, start_time=`15:00`, members에 "철수"), `base_date`=기준일.
   - 제약: 이 단계는 실제 LLM 호출이라 `.env`의 `PROXY_TOKEN`이 필요하다.
     키가 없는 환경에서는 실행 절차만 명시하고, 실제 통과 확인은 사용자 실행에 맡긴다.
