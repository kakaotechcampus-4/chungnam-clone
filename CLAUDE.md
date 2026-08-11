# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 저장소가 뭔지

LangChain 기반 "일정 관리 agent"(Kanana/Nana) 실습용 교육 프로젝트입니다. 주차마다 기능 한 슬라이스씩
추가됩니다 (개인 일정 CRUD → 자연어 요청 구조화 → SQLite 영속화 → RAG 메모리 → MCP 기반 외부 데이터).
학생은 주차마다 `student_parts/weekNN_*.py` 파일 하나씩만 구현하고, 그 외 거의 전부(`fixed/`,
`mcp_server/`)는 강사가 만든 기준 코드라 학생이 수정할 대상이 아닙니다.

이 저장소에는 **자동 테스트가 없습니다**(README.md 참고). 검증은 수동으로 합니다: 앱을 실행하고, 채팅
메시지를 보낸 뒤, UI의 trace(상세) 패널에서 tool 호출/결과 JSON을 확인합니다.

## 명령어

```bash
./run.sh --install        # 최초 설치: uv sync 후 Week 1 앱 실행
./run.sh                  # Week 1 앱 실행 (기본값)
./run.sh --week1          # 특정 주차 앱 실행 (이 브랜치 기준 --week1 ~ --week5)
./run.sh --help           # runner 옵션 목록
```

`run.sh`는 bash 스크립트입니다 — Windows에서는 Git Bash를 쓰거나, 아래처럼 직접 실행하세요:

```powershell
$env:KANANA_ACTIVE_WEEK = "4"   # app.py가 어느 주차 agent를 로드할지 지정
uv run python app.py
```

의존성 관리는 `uv` + `pyproject.toml`/`uv.lock`이 기준입니다. `requirements.txt`와
`environment.yml`은 conda runner(`./run.sh --conda [...]`)용 fallback입니다.

```bash
uv add "package-name>=1.0"
uv remove package-name
uv lock
```

lint/format/test 명령은 따로 설정돼 있지 않습니다(`pyproject.toml`에 ruff/mypy/pytest 없음). 임의로
만들어내지 마세요 — 여기서 "제대로 됐다"는 건 "tool이 실제로 호출되는지, 그 JSON payload가 해당 주차
가이드 주석이 설명하는 모양과 일치하는지"를 뜻합니다.

## 저장소 루트 문서 (변화가 느린 배경 지식 — 먼저 읽어볼 것)

- `README.md` — 설치/환경변수.
- `PROJECT_OVERVIEW.md` — 파일 지도와 "가이드 읽기 → 실행 → trace 확인 → 구현 → 재확인"이라는 표준 흐름.
- `CURRICULUM.md` — Week 1 미션/채점 기준 형태(멘토용).

이 문서들은 Week 1 또는 Week 1-4로 범위가 한정된 `main` 브랜치를 기준으로 쓰여 있어서, 실제 체크아웃된
상태보다 뒤처져 있을 수 있습니다. 어떤 주차까지 존재하는지는 `student_parts/` 디렉터리의 실제 파일
목록과 `fixed/week_agent_registry.py`의 `WEEK_AGENT_MODULES` dict를 기준으로 판단하세요. Week 1-6
전체 히스토리는 `week_1_to_6f` 브랜치에 있습니다.

## 아키텍처

### `fixed/` vs `student_parts/` 경계

`fixed/`는 기반 인프라입니다(설정, 저장소, LLM 연결, trace 추출, MCP client). 수업에서 구현할 대상이
아니므로, 별도 지시가 없는 한 수정 범위 밖이라고 간주하세요. `student_parts/weekNN_*.py` 파일마다
맨 위에 큰 `# [N주차 수강생 구현 가이드]` 주석 블록이 있는데, 이게 실제 스펙입니다 — 어떤 함수를
구현해야 하는지, 정확한 입출력 계약이 뭔지, 그리고 각 함수를 `[메인]`/`[추가]`/`[공통]`(메인과제/
심화과제/이미 구현됐으니 건드리지 않음)으로 태그한 "함수별 동작 설명" 섹션까지 담고 있습니다. 주차
파일을 건드리기 전에 이 블록부터 읽으세요 — 주변 코드만 보고 의도를 추측하는 것보다 이게 훨씬
신뢰할 수 있는 근거입니다.

### 주차별 agent가 선택/실행되는 방식

`fixed/week_agent_registry.py`가 `KANANA_ACTIVE_WEEK`(이 브랜치 기준 1~5)를 `student_parts.weekNN_*`
모듈에 매핑하고, 그 모듈의 `build_week_agent()`를 호출합니다. 모든 주차 모듈이 이 이름의 함수를
구현하고 있어서(내부적으로 `build_weekNN_agent()`의 얇은 별칭), `app.py`는 지금 몇 주차가 활성화됐는지
전혀 몰라도 되고 그냥 `run_active_week_agent()`/`stream_active_week_agent()`만 호출하면 됩니다.
`build_weekNN_agent()`는 프로세스당 `create_agent(model, tools, system_prompt)` LangChain agent
하나를 지연 생성해서 캐싱합니다.

### 주차 누적 패턴 (모든 주차가 이 방식을 따름)

각 주차의 tool 목록과 system prompt는 이전 주차 것 위에 쌓입니다. 예:

```python
def week04_tools() -> list[Any]:
    return [*week03_tools(), add_personal_reference, search_personal_references, ...]

def week04_prompt_parts() -> list[str]:
    return [*week03_prompt_parts(), "이번 주차 tool을 위한 새 지시문 ..."]
```

`join_system_prompt()`(week01에 정의, 모든 주차에서 재사용)는 prompt 조각들을 이어붙이면서, "같은
주제의 지시가 여러 번 나오면 더 뒤에/높은 주차 지시가 우선한다"는 헤더를 붙입니다. 새 tool을
추가할 땐 반드시 "LLM이 언제 이 tool을 호출해야 하는지" 알려주는 prompt 문장이 같이 있어야 합니다 —
prompt에 언급 없이 tool 목록에만 넣으면, 기술적으로는 연결돼 있어도 agent가 그 tool을 아예 안 부를
수 있습니다.

### 헬퍼 함수 / `@tool` wrapper 분리

모든 주차에서 실제 비즈니스 로직은 평범한 함수에 있고, `@tool`이 붙은 함수는 Pydantic
`args_schema`로 입력을 검증하고, 그 헬퍼를 호출한 뒤 `json_payload(result)`(한글이 깨지지 않도록
`json.dumps(..., ensure_ascii=False)`로 감싸는 함수)를 반환하는 얇은 껍데기입니다. 헬퍼는 store
인스턴스를 명시적 파라미터로 받고(예: `add_personal_reference_dict(reference_store, *, title,
content, tags=None)`) 모듈 전역 변수를 직접 참조하지 않습니다 — 모듈 전역 `REFERENCE_STORE`/
`SQLITE_STORE` 등 싱글턴을 직접 건드리는 건 오직 `@tool` 함수뿐입니다. 어떤 헬퍼는 이미 tool의
최종 JSON 모양 그대로를 반환해서 tool 본문이 한 줄이면 되고, 어떤 헬퍼는 순수 list/값만 반환해서
tool 쪽에서 `{"hits": [...]}`/`{"rows": [...]}` 같은 봉투로 한 번 더 감싸야 합니다 — 헬퍼의 반환
타입 힌트와 가이드가 명시한 top-level JSON 계약을 먼저 확인하고 어느 경우인지 판단하세요.

### 데이터 계층

- `fixed/app_store.py`(`AppSQLiteStore`) — 앱 자체 SQLite DB(`data/kanana_app.sqlite3`): 구조화된
  일정/할 일/알림 요청, 그리고 `conversations`/`messages` 채팅 기록.
- `fixed/reference_store.py`(`PersonalReferenceStore`) — ChromaDB 기반 개인 참고자료 벡터 저장소
  (`add_personal_reference`로 저장하는 사용자 선호/메모).
- `fixed/conversation_rag_store.py`(`ConversationRAGStore`) — `AppSQLiteStore`의 대화 기록을
  ChromaDB 컬렉션에 lazy sync합니다(메시지 단위가 아니라 대화 하나 전체가 chunk 하나). 과거 채팅에
  대한 의미 기반 검색을 위한 것이며, `search()`는 `exclude_conversation_id`(현재 대화는 제외 —
  "방금 한 말"을 회상된 기억처럼 취급하면 안 되므로)와 `conversation_id`(특정 대화 안에서만 검색)를
  둘 다 지원합니다.
- 두 벡터 저장소 모두 `OpenAIEmbeddingFunction`(`fixed/reference_store.py`)으로
  `CONFIG.embedding_proxy_url`/`CONFIG.openai_embedding_model`을 통해 임베딩합니다.
- `fixed/session_scope.py` — `ContextVar` 기반 "현재 conversation_id"로, Week 1의 인메모리 일정
  저장소와 위의 conversation-RAG 제외 로직에서 함께 씁니다.

### MCP 계층 (Week 5+)

Week 5부터는 일부 데이터("Kana"의 과거 대화, 사용자 간 공유 일정 저장소)가 별도의 MCP 서버
(`mcp_server/sqlite_mcp_server.py`, `@mcp.tool` 데코레이터 — LangChain의 `@tool`과는 다른
프로토콜/데코레이터) 뒤에 있고, `fixed/mcp_client.py`가 이걸 stdio subprocess로 띄웁니다.
`student_parts/week05_*.py`가 할 일은 *wrapper* tool뿐입니다: `call_mcp_tool_sync(tool_name, args)`
(`fixed/mcp_client.py`의 `call_local_mcp_tool_sync` 별칭)를 호출하거나, `load_conversation_messages`
한정으로는 `call_external_tool_payload(...)`(`fixed/external_mcp.py`)를 호출해서 결과를 그대로
전달합니다 — SQL도, 중복된 정규화 로직도 여기 두지 않습니다. 그 로직은 서버 쪽에 있고 학생이
수정할 대상이 아닙니다.

### Trace / 검증

`fixed/langchain_trace.py`가 LangChain agent의 메시지 목록에서 `tool_call`/`tool_result` 이벤트(와
최종 답변 텍스트)를 뽑아, Gradio UI의 "상세" trace 패널이 렌더링하는 JSON으로 만듭니다.
`fixed/trace.py`와 `fixed/agent_runtime.py`는 채팅 저장과 pending-message UI 상태를 담당합니다.
자동 테스트 하네스가 없으므로, 코스 가이드가 가리키는 실제 합격 기준은 테스트 실행이 아니라 이
trace 패널입니다("실행하고 trace에서 어떤 tool이 호출됐는지 확인하세요"). `tool_result`의 `content`는
wrapper 함수가 반환한 문자열 그 자체이고, 그 이벤트의 형제 필드인 `id`는 LangChain의 tool-call
상관관계 id(`tool_call`과 `tool_result`를 짝짓는 용도)이지, wrapper 함수가 만들어내는 값이 아닙니다.

## Git / PR 워크플로우

학생 브랜치는 `<username>/weekN` 형태로 이름 붙이고, PR은 `main`이 아니라 `<username>/final`(개인
통합 브랜치)을 대상으로 엽니다. `.github/workflows/`가 PR이 열리면 리뷰어를 자동 배정하고 알림을
보냅니다 — 리뷰어를 수동으로 추가하지 마세요. `.github/pull_request_template.md`는 기능별로 "AI
활용 내역" 섹션을 요구합니다: 어떤 prompt를 썼는지가 아니라, AI가 준 결과가 뭐였고 그중 뭘 직접
고쳤는지, 왜 고쳤는지를 적어야 합니다.
