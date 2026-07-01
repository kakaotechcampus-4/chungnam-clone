# Repository Instructions

## Project Context

- This repository is the Week 1 branch of `Kanana Schedule Agent`.
- The Week 1 task is to implement personal schedule CRUD tools for the user's personal schedules.
- The schedule owner is the user. Do not describe schedules as "Nana's personal schedules".
- `Nana` may appear in existing course comments or function text, but new implementation docs, tool descriptions, prompts, and tests should use `Kanana Schedule Agent` for the agent name and "user's personal schedule" for the schedule owner.

## Week 1 Implementation Scope

- Primary implementation file: `student_parts/week01_wake_up_nana.py`.
- Follow `docs/plan_week01.md` for the current Week 1 work plan.
- Implement these functions:
  - `personal_create_schedule`
  - `personal_list_schedules`
  - `personal_delete_schedule`
  - `week01_prompt_parts`
- Do not modify `CHAT_MEMORY_PROMPT` for Week 1 unless the plan is explicitly revised. It is currently not connected to the prompt flow.
- Do not modify `fixed/`, `mcp_server/`, `app.py`, or `static/` for the Week 1 CRUD task.
- Do not add DB, MCP, RAG, or Week 3+ persistence behavior to Week 1 tools.

## Tool Contract

- LangChain tools must return JSON strings, not Python dictionaries.
- Use `_json(...)` for tool return values.
- Keep these top-level payload keys:
  - `personal_create_schedule`: `ok`, `tool_name`, `created_schedule`
  - `personal_list_schedules`: `ok`, `tool_name`, `schedules`
  - `personal_delete_schedule`: `ok`, `tool_name`, `deleted`
- Add explicit `@tool("tool_name", description="...")` descriptions instead of relying on docstrings as tool descriptions.
- Keep existing function signatures unless the user explicitly approves a change.

## Session Scope Rules

- Week 1 schedules live only in `PERSONAL_SCHEDULES`.
- Created schedules must include `session_id=current_session_scope()`.
- Listing and deletion must operate only on schedules in the current session scope.
- For deletion, preserve the list object with `PERSONAL_SCHEDULES[:] = kept_schedules`.
- Compute `deleted` by comparing list length before and after deletion.

## Verification

- Create Week 1 verification code under `test/test_week01_personal_schedule.py` when implementing the plan.
- Tests should assert tool result payloads and trace events, not final LLM wording.
- Include direct tool verification and LLM integration verification when `PROXY_TOKEN` is available.
- Run:

```bash
python -m compileall -q app.py fixed student_parts mcp_server
python test/test_week01_personal_schedule.py
```
