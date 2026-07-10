# Repository Instructions

## Project Context

- This repository is the student workspace of `Kanana Schedule Agent`.
- Week 1 implemented personal schedule CRUD tools; Week 2 implements natural language request structuring with structured output.
- The schedule owner is the user. Do not describe schedules as "Nana's personal schedules".
- `Nana` may appear in existing course comments or function text, but new implementation docs, tool descriptions, prompts, and tests should use `Kanana Schedule Agent` for the agent name and "user's personal schedule" for the schedule owner.

## Coding Conventions (from mentor review feedback)

- Write filter/transform logic as list comprehensions with logical operators, not sequential `if` statements. Include None-safety conditions inside the comprehension.
- Access required fields with direct indexing (`obj["key"]`), not `.get()`. Avoid unnecessary defensive code.
- Keep existing function signatures unless the user explicitly approves a change.

## Prompt Conventions

- Weekly prompt parts accumulate: `weekNN_prompt_parts()` starts with `*week(NN-1)_prompt_parts()` and appends this week's instructions.
- Prefix the first prompt part of each week's block with `WEEK N:` so the accumulated system prompt shows which week each instruction belongs to.
- Put only accumulate-safe instructions in `weekNN_prompt_parts()`. Week-specific final-answer rules (tied to that week's `response_format`) go in `weekNN_system_prompt()` so they do not leak into later weeks.
- State the current date explicitly (`오늘 날짜는 {current_app_date_iso()}이다`) in the week's prompt part.

## Week 1 Scope (merged)

- Implementation file: `student_parts/week01_wake_up_nana.py`, plan: `docs/plan_week01.md`, tests: `test/test_week01_personal_schedule.py`.
- LangChain tools return JSON strings via `_json(...)`, keeping top-level payload keys:
  - `personal_create_schedule`: `ok`, `tool_name`, `created_schedule`
  - `personal_list_schedules`: `ok`, `tool_name`, `schedules`
  - `personal_delete_schedule`: `ok`, `tool_name`, `deleted`
- Add explicit `@tool("tool_name", description="...")` descriptions instead of relying on docstrings as tool descriptions.
- Week 1 schedules live only in `PERSONAL_SCHEDULES` with `session_id=current_session_scope()`; listing and deletion operate only on the current session scope; deletion preserves the list object with `PERSONAL_SCHEDULES[:] = kept_schedules`.

## Week 2 Scope

- Implementation file: `student_parts/week02_structure_natural_language_requests.py`, plan: `docs/plan_week02.md`, tests: `test/test_week02_structured_request.py`.
- Implement `StructuredRequest` / `StructuredRequestBatch` schemas with Korean field descriptions, `week02_tools()`, `week02_prompt_parts()`, `week02_system_prompt()`, and `build_week02_agent()` with `response_format=StructuredRequestBatch`.
- Do not fabricate uncertain values: unknown fields stay None or empty list; `date` is `YYYY-MM-DD` and times are `HH:MM` only when certain.
- Week 2 does not persist anything: no SQLite, RAG, or external member coordination.
- `_coerce_structured_request()`, `extract_structured_request()`, and `extract_schedule_request()` are reserved for later weeks — leave them unimplemented.
- Week 1 file may only receive prompt-string changes (e.g. `WEEK 1:` prefix); do not change its tool/agent logic.

## Common Scope Rules

- Do not modify `fixed/`, `mcp_server/`, `app.py`, or `static/`.
- Do not add DB, MCP, RAG, or Week 3+ persistence behavior ahead of schedule.

## Verification

- Tests assert tool result payloads, trace events, and structured_response contents, not final LLM wording.
- Week 2 LLM integration checks are skipped when `PROXY_TOKEN` is absent; Week 1's require it.
- Run:

```bash
python -m compileall -q app.py fixed student_parts mcp_server
python test/test_week01_personal_schedule.py
python test/test_week02_structured_request.py
```
