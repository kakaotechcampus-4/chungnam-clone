# Kanana Schedule Agent (Nana 프로젝트)

카카오테크캠퍼스 실습용 LangChain 일정 에이전트. `student_parts/weekNN_*.py`의 TODO 스텁을 주차별로 채워나가는 구조이며, 이 브랜치는 Week 1-6까지 전부 포함한다.

## 실행

```bash
./run.sh --install   # 최초 1회 (uv sync 포함)
./run.sh --week6      # 현재 작업 주차 (1~6 중 택1)
./run.sh --help
```

`.env`에 `PROXY_TOKEN`이 없으면 LLM/임베딩 호출이 즉시 `RuntimeError`로 실패한다 — `.env.example` 참고.

## 구조

| 경로 | 역할 |
| --- | --- |
| `student_parts/` | 학생 구현 대상. 각 파일 상단 `[N주차 수강생 구현 가이드]` 주석이 그 주차의 1차 소스다 |
| `student_parts_baseline/` | 강사 완성 참조 구현(대부분 TODO 0개) — 대조용이며 수정 대상 아님. week01만 TODO 1개가 남아 있다 |
| `fixed/` | 기준 구현 — 참고만 하고 수정하지 않는다 |
| `fixed/schedule_decision.py` | Week 6 공통 가능 시간 검증(`find_common_available_slots_payload`)·최종 결정(`decide_final_slot_payload`) payload 생성 — LLM이 고른 candidate를 검증만 한다 |
| `mcp_server/sqlite_mcp_server.py` | Week 5+ 외부 SQLite MCP 서버 구현 — 수정 대상 아님 |
| `app.py` | Gradio 채팅 UI + 상세 trace 화면 |
| `공지_코드업데이트.md` | 강사 공지 — 이미 구현된 주차의 계약이 바뀌면 여기 먼저 반영되고 학생 코드에 소급 적용을 요구한다. 새 CLAUDE.md/plan.md를 쓰기 전에 항상 먼저 읽는다 |
| `docs` | 심볼릭 링크(`C:/Users/user/Desktop/옵시디언/개발`)이며 이 저장소의 실제 디렉터리가 아니다. 주차별 `plan.md`/`error-log.md`는 완료 후 `weekN_*.md` 이름으로 그 vault에 옮겨 보관한다 |

## 이 코드베이스의 함정

- tool의 top-level JSON 키는 주차/도구마다 고정 계약이다 (예: 검색류 도구는 `hits`/`rows`). 기존 도구의 키 구조 계약을 준수한다.
- JSON 직렬화는 각 파일의 `json_payload(...)` 헬퍼를 거친다 (`ensure_ascii=False`) — `json.dumps`를 직접 쓰면 한글이 깨진다.
- `weekNN_prompt_parts()`는 이전 주차 리스트에 **append만** 한다. 새 프롬프트를 앞 주차 것 없이 새로 만들면 이전 주차 tool 라우팅 지시가 통째로 사라진다. **단, Week 6 `kana_prompt_parts()`는 이 관례의 유일한 예외** — 다른 주차를 누적하지 않고 Kana 역할을 처음부터 새로 쓴다 (Kana는 Nana/supervisor와 별개 페르소나이므로 이전 주차 개인 일정 지시를 물려받을 이유가 없다).
- Week 6 supervisor가 직접 볼 수 있는 tool은 `nana_agent`/`kana_agent` 두 개뿐이다. 그런데 `week06_prompt_parts()`는 `week05_prompt_parts()`를 누적하므로 Week 1-5의 개별 tool(`personal_create_schedule` 등) 라우팅 지시를 그대로 물려받는다 — supervisor 프롬프트의 마지막 조각에서 "그 tool들은 하위 agent 소유이고 너는 직접 호출하지 않는다, 반드시 nana_agent 또는 kana_agent를 호출한다"로 명시적으로 덮어써야 한다. 하위 agent(Nana/Kana)는 supervisor prompt를 공유하지 않으므로 필요한 지시는 각자 프롬프트 안에 있어야 한다.
- Week 6 `find_common_available_slots`/`decide_final_slot`은 후보나 최종 시간을 **계산하지 않는다** — LLM agent가 tool description을 읽고 직접 고른 값을 인자로 넘기면 그 값을 검증/기록만 한다. Python 구현과 tool description이 서로 다른 계약을 말하면 agent가 잘못된 인자를 넘긴다.
- busy-time row의 `end_time`이 `"미정"`이거나 비어 있으면 `fixed/schedule_decision.py`의 시간 파서가 자정(`24*60`)으로 폴백해 그 시각부터 하루 끝까지 통째로 바쁜 것으로 처리된다. busy row를 만드는 쪽(`collect_member_schedules` 등)에서 `"미정"`을 구체적인 시각(예: `"18:00"`)으로 정규화하지 않으면 그날 후보가 전부 사라진다.
- `top_k`/`limit`이 Pydantic `Field(ge=.., le=..)`로 이미 검증되는 경로에서는 helper 내부에서 `safe_limit()`을 중복 호출하지 않는다. Pydantic 검증을 거치지 않고 helper가 직접 호출될 새 경로가 생길 때만 추가한다.
- Windows 콘솔에 한글 출력이 깨져 보이는 건 대부분 코드페이지 문제이지 데이터 손상이 아니다 (파일로 리다이렉트해 재확인).
- `docs`는 심볼릭 링크라서 `mkdir docs`나 `docs/` 하위에 직접 쓰기를 시도하면 실패하거나 개인 vault를 오염시킨다 — 저장소 안에 문서를 남기려면 다른 이름의 폴더를 쓴다.

## 검증

자동 테스트 하네스가 없으므로, 기능 검증은 `./run.sh --weekN` 실행 후 Gradio 상세 trace 화면의 `tool_name` 및 결과 payload로 수행한다. Week 6은 supervisor trace에서 `nana_agent`/`kana_agent` 중 무엇이 선택됐는지, 하위 agent trace의 `inner_tool_names`, 그룹 조율의 경우 `final_slot_payload`까지 함께 확인한다.

## 개발 워크플로우 (문서화 + agy 검수)

이 저장소는 구현 결과뿐 아니라 **구현에 이르는 판단 과정 자체**를 기록으로 남기는 것을 요구한다. 주차 작업 시 저장소 루트에 아래 세 문서를 쓴다 (완료 후 Obsidian vault로 `weekN_*.md` 이름으로 옮기는 관례는 위 `docs` 항목과 동일):

- **`plan.md`** — 코딩 전 설계, 확정한 계약, 검증 계획.
- **`dev-log.md`** — 구현하며 실제로 내린 판단의 기록. 무엇을 구현했는지뿐 아니라, 어떤 대안을 검토했고 왜 그 대안을 버리고 이 방식을 골랐는지 근거(가능하면 `파일:줄`)와 함께 남긴다. plan.md와 실제 구현이 달라졌다면 그 지점과 이유도 적는다.
- **`error-log.md`** — 마주친 에러를 그때그때 기록한다. 해결된 에러는 "해결됨"으로 표시하고 원인과 해결 방법을 함께 남긴다(미해결이면 상태를 그대로 열어 둔다).

각 개발 마일스톤을 완료할 때마다, 다음 마일스톤으로 넘어가기 전에 `agy-delegate` 스킬로 Antigravity CLI(`agy`)의 검수를 받는다 — 그 마일스톤의 diff와 관련 plan.md/dev-log.md 근거를 함께 넘기고, PASS를 확인한 뒤에만 다음 마일스톤을 시작한다.

## 더 알아보기

- 저장소 전체 지도: [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)
- 주차별 수업 진행: [CURRICULUM.md](CURRICULUM.md)
- 지난 주차 설계/에러 기록: Obsidian vault의 `weekN_plan.md` / `weekN_error-log.md` (이 저장소 밖)
