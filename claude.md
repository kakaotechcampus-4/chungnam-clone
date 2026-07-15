# Nana 프로젝트 — Week 03

## 실행 명령어
```
./run.sh --week3
```

## 주요 파일
- 구현 파일: `student_parts/week03_build_nanas_logbook.py`
- 의존 파일: `student_parts/week02_structure_natural_language_requests.py` 
(StructuredRequest, RequestKind 등 상속 및 연동)  
`student_parts/week01_wake_up_nana.py` (join_system_prompt, week01_tools 등 상속)  
`fixed/config.py` (CONFIG 전역 설정 인입)  
`fixed/llm.py` (chat_model() 사용)  
`fixed/runtime_clock.py` (current_app_date_iso 사용)  
`fixed/app_store.py` (AppSQLiteStore 연동)  
- 에이전트 빌더: `build_week03_agent()` 및 런타임 엔트리 포인트 `build_week_agent()`

## 아키텍처
Week3의 핵심 목적은 Week2에서 완성한 자연어 구조화 결과물인 StructuredRequest 데이터를 Pydantic 입력 스키마로 1차 검증한 뒤, SQLite 영속 DB 저장소(AppSQLiteStore)에 연동하여 "저장 -> 조회 -> 수정/삭제"의 세로 슬라이스를 완성하는 것이다. 이를 통해 이 프로젝트는 일회성 대화 세션에 머무르던 Week1의 임시 메모리를 탈피하여, 앱이 종료되거나 새 대화가 시작되어도 SQLite DB에 남아 있는 기록을 기반으로 상태를 복원하고 지속적으로 일정을 관리하는 기록장(영속 메모리)을 확보하게 된다. 

```
[사용자 자연어 입력]
       ↓
[Week 3 Agent (with week03_tools)]
       ↓ (1단계: extract_schedule_request로 구조화)
[StructuredRequest (구조화 결과 확보)]
       ↓ (2단계: save_structured_request 등의 SQLite 저장 도구 호출)
[SQLite DB (AppSQLiteStore) 영속성 저장]
       ↓ (3단계: list_saved_requests / personal_list_saved_schedules 등으로 조회 및 대화 맥락 복원)
[기록된 정보 기반 답변 제공 및 영속적 유지]
```

## 개발 행동 원칙
클로드 코드는 본 프로젝트의 코드를 작성하고 수정하는 전 과정에서 다음의 4가지 핵심 개발 원칙을 강제 준수해야 한다.
1. **Think Before Coding**: 코딩 전 가정과 설계를 `plan.md`에 명시하고, 모호함이 있다면 즉시 정지 후 질문할 것
2. **Simplicity First**: 불필요한 추상화, 예외 래퍼, 미지정 라이브러리 추가 엄금
3. **Surgical Changes**: 타겟 함수 본문만 정교하게 수정하며, 주변 스타일(Pydantic V2 등)을 100% 모방할 것
4. **Goal-Driven**: "자가 테스트" 단계의 검증을 통과하는 것만을 목표로 삼을 것
5. **plan.md 수립**: 코드를 작성하기 전, 반드시 plan mode에서 전체 개발 마일스톤과 마주할 가정을 명시한 plan.md를 선제 작성하여 검수받아라. student_parts/week03_build_nanas_logbook.py 상단에 명시된 [3주차 수강생 구현 가이드] 및 코드 내부의 TODO 지시문을 완벽하게 파싱하여 기획에 통합하라.
6. **dev-log.md 실시간 체크리스트 및 상세 원리 기록**: 개발 과정에서 계획의 단계가 끝날 때마다 dev-log.md에 수립해 둔 체크리스트를 업데이트[x]하라.
사용자가 이 문서만 보고도 처음부터 끝까지 똑같이 코드를 구현하고 기술적으로 완벽히 이해흘 수 있는 수준으로 상술하라. 
7. **발생한 모든 에러는 dev-log.md에 기록하고, 어떻게 해결하였는지 흐름을 명시하라.**

## SQLite 물리 테이블 스키마 규격 (Hard constraint)
AppSQLiteStore 연동 및 쿼리 제어 시, 파이썬 파일 내에 드러나지 않는 아래의 실제 SQLite 스키마 구성을 엄격히 참조하여 쿼리 불일치 에러를 원천 봉쇄하라.
- structured_requests (원본 메타 저장소)
    - request_id (TEXT, PRIMARY KEY)  
    - source_text (TEXT, NOT NULL)  
    - kind (TEXT, NOT NULL)  
    - payload_json (TEXT, NOT NULL)  
    - created_at (TEXT, NOT NULL)  
    
- schedules (일정 데이터 테이블)
   - id (INTEGER PRIMARY KEY AUTOINCREMENT) 
   - request_id (TEXT, FOREIGN KEY REFERENCES structured_requests)  
   - title (TEXT, NOT NULL)  
   - date (TEXT), start_time (TEXT), end_time (TEXT)  
   - members_json (TEXT, NOT NULL)  
   - reason (TEXT), schedule_type (TEXT, NOT NULL)  

- todos (할 일 데이터 테이블)
  - id (INTEGER PRIMARY KEY AUTOINCREMENT)  
  - request_id (TEXT, FOREIGN KEY REFERENCES structured_requests)  
  - title (TEXT, NOT NULL)  
  - due_date (TEXT), priority (TEXT), reason (TEXT)  
  
- reminders (알림 데이터 테이블)
  - id (INTEGER PRIMARY KEY AUTOINCREMENT)  
  - request_id (TEXT, FOREIGN KEY REFERENCES structured_requests)  
  - title (TEXT, NOT NULL)  
  - date (TEXT), start_time (TEXT), reason (TEXT) 

 

## 절대 금지 / 반드시 지킬 것 (Known gotchas)
- 코드 베이스 가이드 최우선 참조: student_parts/week03_build_nanas_logbook.py 내부의 SaveStructuredRequestInput 스키마 설계 방식, 각종 조회/수정/삭제 입력 스펙(SavedRequestListInput 등) 및 각 함수의 TODO 제약 조건을 실시간으로 직접 읽고 한 자도 빠짐없이 지켜 구현하라. 
- Pydantic 재생성 금지: 도구 본문(특히 save_structured_request) 내부에서는 Pydantic 클래스 인스턴스를 직접 불필요하게 다시 생성하지 말 것 (이미 @tool(args_schema=...)가 유효성 검증을 완료함).  
- 임시 래퍼 직접 저장 금지: DB의 payload_json 등에 원본 구조화 정보를 저장할 때, ok, tool_name, base_date와 같은 임시 결과 래퍼 필드를 원시 데이터에 적재하지 말 것.  
- 안전한 삭제 제어: 무조건적인 전체 삭제를 예방하기 위해, 모든 필터 인자가 비어있는 상태로 들어올 때 삭제를 안전하게 거절하는 Guard 규칙을 _delete_saved_schedules에 반드시 구현하라.  
- 조회 실패 대응: DB 단건 및 목록 조회 과정에서 매칭되는 데이터 레코드가 없더라도 예외나 에러를 발생시키지 말고, 단건은 row=None, 목록은 rows=[] 형태를 반환하여 에러 전파를 방지하라.  
- 유니코드 보존: 한글이 깨지거나 유실되지 않도록 모든 도구의 JSON 문자열 직렬화 시 json_payload 함수(ensure_ascii=False 설정 적용)를 거쳐서 리턴하라.   
- 프롬프트 누적: week03_prompt_parts() 구현 시 기존 Week 1, 2 프롬프트 리스트 위에 Week 3 규칙(SQLITE_MEMORY_PROMPT, WEEK03_TOOL_CALL_PROMPT 지침)을 누적(Append)하여 조립하라 (덮어쓰기 금지).  
- Pydantic V2 문법 고수: Pydantic V2 양식을 준수하여 @model_validator(mode="before") 및 .model_dump() 형식을 일관되게 활용하라.  
