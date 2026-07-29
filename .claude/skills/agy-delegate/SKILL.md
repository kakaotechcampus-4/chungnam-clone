---
name: agy-delegate
description: >
  Use this skill to delegate tasks to Google Antigravity CLI (`agy`) running in the background.
  Trigger on "delegate to antigravity", "send to antigravity", "ask agy to", "run with agy",
  "have antigravity do", "antigravity에게", "antigravity로", "안티그래비티에게", "안티그래비티로",
  "use antigravity for", "let agy handle", "agy한테 시켜", "agy로 돌려봐".
  Also trigger when the user wants a second opinion from another AI specifically
  mentioning Antigravity or agy, wants to compare Claude's approach with Antigravity,
  or asks to run a task with an external Google agent in parallel.
  Do NOT trigger for general "delegate" or "외부 AI" requests without mentioning
  Antigravity/agy — those may be better handled by gemini-delegate, codex-delegate,
  or other tools.
version: 0.1.0
---

# Antigravity Delegate

Delegate tasks to the Google Antigravity CLI (`agy`) agent running in the background and retrieve results.

## Prerequisites

- Antigravity CLI installed:
  - macOS/Linux: `curl -fsSL https://antigravity.google/cli/install.sh | bash`
  - Windows (PowerShell): `irm https://antigravity.google/cli/install.ps1 | iex`
  - The binary installs to `~/.local/bin/agy` (macOS/Linux) or `C:\Users\<Username>\AppData\Local\agy\bin` (Windows)
- Authentication completed: launch `agy` interactively once to sign in (local keyring lookup, falling back to a browser OAuth flow; over SSH it prints a URL to authorize manually)

## Execution Flow

### 1. Compose the Task Prompt

Build a clear, self-contained prompt for Antigravity. The prompt must work independently — `agy` has no access to this conversation's context.

**Gather context first.** Before composing the prompt, collect relevant information to include:

```
# Context to gather:
- Relevant source files (read key files and include snippets in the prompt)
- Recent git changes: git diff HEAD~3 --stat (if relevant)
- Project structure: a brief tree or file listing
- Build/test commands the agent should use
- Error messages or logs (if debugging)
```

**Compose the prompt** with this structure:
- Task description with enough background for a fresh agent
- Target files or directories (use absolute paths)
- Relevant code context (key file contents or snippets — include enough so the agent doesn't need to explore)
- Constraints, coding style, or preferences mentioned by the user
- Expected output format and success criteria

Wrap the prompt in single quotes to avoid shell interpolation issues. If the prompt contains single quotes, use a heredoc approach instead.

### 2. Launch Antigravity in Background

Generate a unique result file path and execute `agy` using the Bash tool with `run_in_background: true`:

```bash
RESULT_FILE="/tmp/agy-result-$(date +%s)-$RANDOM.md"
agy -p '<TASK_PROMPT>' \
  --dangerously-skip-permissions \
  --output-format text \
  --print-timeout 10m \
  > "$RESULT_FILE" 2>&1
echo "===AGY_RESULT_FILE:$RESULT_FILE==="
```

For prompts containing single quotes, use a heredoc:

```bash
RESULT_FILE="/tmp/agy-result-$(date +%s)-$RANDOM.md"
agy -p "$(cat <<'PROMPT'
<TASK_PROMPT_WITH_QUOTES>
PROMPT
)" \
  --dangerously-skip-permissions \
  --output-format text \
  --print-timeout 10m \
  > "$RESULT_FILE" 2>&1
echo "===AGY_RESULT_FILE:$RESULT_FILE==="
```

### Flag Reference

| Flag | Purpose |
|------|---------|
| `-p`, `--print`, `--prompt <text>` | Run a single prompt non-interactively and print the response (required for background execution) |
| `--dangerously-skip-permissions` | Auto-approve all tool permission requests (default: false) |
| `--sandbox` | Run with terminal sandbox restrictions enabled (default: false) — use alongside skipped permissions for safer execution |
| `--output-format <fmt>` | `text` (default), `json`, or `stream-json` |
| `--json-schema <schema-or-path>` | Schema string or file path to enforce structured output |
| `--model <slug>` | Model slug for this run (see `agy models`) |
| `--effort <level>` | Reasoning effort: `low`, `medium`, or `high` |
| `--agent <name>` | Agent to use for this run (see `agy agents`) |
| `--continue`, `-c` | Continue the most recent conversation (default: false) |
| `--conversation <id>` | Resume a conversation by ID |
| `--print-timeout <dur>` | Maximum time to wait for a response (default: `5m`) |

Output goes to stdout; diagnostics route to stderr, so redirecting `> "$RESULT_FILE" 2>&1` captures everything in one file.

### 3. Notify the User

After launching, inform the user:
- The task has been delegated to Antigravity (`agy`)
- It is running in the background
- They can continue with other work in the meantime

### 4. Check Progress (if needed)

If the task is taking long or the user asks for status, check the background task:

```bash
# Check if the process is still running
TaskOutput(task_id="<task_id>", block=false)

# Peek at partial output
tail -20 /tmp/agy-result-*.md 2>/dev/null
```

If the task appears stuck (no output for extended time), inform the user and offer to cancel and retry — note that `--print-timeout` (default `5m`) will eventually abort the run on its own.

### 5. Retrieve and Present Results

When the background task completes:

1. Read the result file using the Read tool
2. If the file is empty or missing, check the Bash task output for errors
3. Present a concise summary of what Antigravity accomplished
4. If Antigravity modified files, run `git diff` to show what changed
5. Ask the user if they want to keep, revert, or adjust the changes

## Parallel Delegation

When the user wants to compare approaches or get a second opinion, send the same task to both Antigravity and Gemini/Codex simultaneously. Launch both with `run_in_background: true` in the same turn:

```bash
# Launch Antigravity
AGY_RESULT="/tmp/agy-result-$(date +%s)-$RANDOM.md"
agy -p '<TASK>' --dangerously-skip-permissions --output-format text > "$AGY_RESULT" 2>&1

# Launch Gemini (in a separate Bash call, same turn)
GEMINI_RESULT="/tmp/gemini-result-$(date +%s)-$RANDOM.md"
gemini -p '<TASK>' -y --model gemini-3.1-pro-preview -o text > "$GEMINI_RESULT" 2>&1
```

When both complete, present a side-by-side comparison highlighting differences in approach, code style, and correctness.

## Configuration Overrides

By default, omit `--model` to use the CLI's configured default. Override only when the user explicitly requests a specific model or effort level:

```bash
# Default
agy -p "task" --dangerously-skip-permissions --output-format text

# Override model and reasoning effort
agy -p "task" --dangerously-skip-permissions --output-format text --model <slug> --effort high
```

Run `agy models` to list available model slugs.

## Error Handling

| Error | Action |
|-------|--------|
| Auth failure | Run `agy` interactively to re-authenticate (browser OAuth, or the SSH manual-URL flow); `/logout` first if stale credentials are suspected |
| Empty output | Check stderr from background task output |
| Timeout | Check task status with `TaskOutput(block=false)`; the run auto-aborts after `--print-timeout` (default `5m`) — offer to cancel/retry with a longer timeout |
| Command not found | Re-run the install script for the platform (see Prerequisites) |
| Process stuck | No new output for >2min — inform user, offer cancel |

## Security Note

`--dangerously-skip-permissions` allows Antigravity to execute commands and modify files without approval. This is required for unattended background execution. Add `--sandbox` alongside it to keep terminal execution restricted when the task doesn't need unrestricted system access.
