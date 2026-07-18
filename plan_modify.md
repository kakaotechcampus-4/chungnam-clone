# Plan for Code Review Modification — Week 03 Refactoring

> 이 문서는 PR 코드 리뷰 피드백을 반영하여 Week 2의 날짜 해석 한계 극복 및 Week 3 영속 기록장 시스템을 고도화하기 위한 리팩토링 계획서이다.

---

## 1. 반영할 리뷰 피드백 핵심 요약 및 수정 방향

### [A] 도구 논리 및 안전 가드 개선 (Core Logic)
- **도구 테이블 매핑 제한**: `personal_list_saved_schedules` 도구가 `todo`나 `reminder` 종류를 인입받았을 때 빈 결과를 반환하는 문제를 방지하기 위해, `kind` 입력 범위를 일정 유형(`personal_schedule`, `group_schedule`)으로 엄격히 제한하거나 테이블별 적절한 분기/경고 처리를 수행한다.
- **삭제 조건(`has_condition`) 결합 규칙 강화**: 특정 필드 단 하나만으로 광범위한 삭제가 일어나지 않도록 허용 시나리오를 명확히 정의하고, `delete_all`과 일반 필터 삭제의 경계를 재정립한다.

### [B] 프롬프트 최적화 (Prompt DRY 원칙)
- **중복 지시문 압축**: `WEEK03_TOOL_CALL_PROMPT` 및 프롬프트 파트에서 반복되는 `extract_schedule_request -> save_structured_request` 구조화 연쇄 지시 및 필터 제거 지침을 명확하고 간결한 단일 지시문으로 압축하여 토큰 비용을 절감하고 캐싱 효율을 높인다.

### [C] 타입 가드 및 도구 규격화 (Robustness & Uniformity)
- **`unwrap_legacy_payload` 확장**: 입력값이 이미 `StructuredRequest` 인스턴스인 경우를 정상 pass-through 처리하고, JSON 형태의 문자열 가드까지 포함하여 안정성을 높인다.
- **반환 규격 일치**: `personal_create_schedule` 도구의 최종 반환값 직렬화 전에 반드시 공통 헬퍼인 `tool_result`를 거치도록 수정하여 시스템 출력 포맷의 일관성을 유지한다.
- **메타데이터 가독성**: 호환 레이어의 `original_text` 하드코딩 문구가 자연스러운 컨텍스트를 방해하지 않는지 검토하고 다듬는다.

---

## 2. 세부 개발 마일스톤 (수정 절차)

### 마일스톤 R1: 프롬프트 압축 및 최적화 (DRY)
- [ ] `student_parts/week03_build_nanas_logbook.py` 내 `WEEK03_TOOL_CALL_PROMPT` 분석 및 중복 문장 제거.
- [ ] 에이전트 시스템 프롬프트 결합부에서 중복되는 지시사항을 단 한 번만 명확하게 전달하도록 텍스트 다이어트 수행.

### 마일스톤 R2: 입력 가드 및 데이터 흐름 안정화
- [ ] `unwrap_legacy_payload` 내부 가드 조건 수정: `isinstance(value, StructuredRequest)` 대응 및 JSON 문자열 역직렬화 안전망 추가.
- [ ] `personal_create_schedule` 내부 반환문을 `tool_result` 구조로 래핑하여 규격 일치화. `original_text` 주입 문구의 적절성 보완.

### 마일스톤 R3: 일정 조회/삭제 핵심 취약점 조치
- [ ] `personal_list_saved_schedules` 내부 로직에 `kind`가 `todo`, `reminder`일 경우 스토어 에러가 나거나 누락되지 않도록 제약 조건 추가 또는 경고 분기 연동.
- [ ] `_delete_saved_schedules`의 `has_condition` 판단 규칙을 강화하여 위험한 범위의 일괄 삭제 오작동 시나리오 차단.

---

## 3. 기술적 예외 상황 및 방어 전략

- **도구 식별 혼선 예방**: 프롬프트를 압축하는 과정에서 Week 1 도구와 Week 3의 `saved` 도구 간의 구분이 모호해지지 않도록, 압축은 하되 **"기록장 제어 시 반드시 이름에 saved가 들어간 도구를 사용하라"**는 핵심 경계선 규칙은 강력하게 유지한다.
- **Pydantic 상속 관계 유지**: `unwrap_legacy_payload` 수정 시 Pydantic V2 레이어와의 정합성이 깨져 `ValidationError`가 엉뚱한 곳에서 발생하지 않도록, 인스턴스 타입 체크 후 `.model_dump()` 처리를 유연하게 연동한다.

---

## 4. 자가 검증 (수정 후 테스트 계획)

1. **정적 검증**: `uv run python -c` 명령으로 리팩토링 후 임포트 및 문법 에러 유무 체크.
2. **유닛 가드 테스트**: 무조건 삭제 시도(`{}` 또는 단일 위험 필터) 시 `ok=False` 피드백이 정확히 떨어지는지 수동 유닛 테스트 실행.
3. **E2E 멀티턴 재검증**: `./run.sh --week3` 환경 하이브리드 구동 시 프롬프트 압축 후에도 에이전트가 헤매지 않고 구조화 후 저장을 매끄럽게 수행하는지 토큰 절감 효과와 함께 추적.
