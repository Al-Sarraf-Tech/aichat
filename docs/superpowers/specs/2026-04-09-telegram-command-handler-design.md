# Telegram Command Handler — Design Spec

**Date:** 2026-04-09
**Branch:** `feat/telegram-command-handler` (off main)
**Repo:** `~/git/aichat`

## Overview

Add an inbound Telegram command handler to aichat-mcp. A long-polling background loop receives messages from Telegram, classifies intent via Gemma 4 E2B (local, free), and dispatches to either MCP tools (quick ops) or Claude Code CLI (coding tasks). Results and milestone updates are sent back to Telegram.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Auth | Single user (chat ID 7274294368) | Personal infrastructure bot |
| Routing | Natural language via Gemma 4 E2B | Local, free, already hot on Arc A380 |
| Progress | Acknowledge + milestone updates from stream-json | Real visibility into coding tasks |
| Concurrency | Concurrent coding tasks + instant quick ops | Quick ops never blocked |
| Results | Detailed summary (files, tests, commit, branch, description) | Know what happened without opening terminal |
| Architecture | Long-polling inside aichat-mcp (Approach 1) | Simplest, no new containers, no public URL |
| Announcements | "Got it" on receive, "Working on: X" after classification | Confirms receipt and intent |

## Architecture

```
Telegram -> getUpdates poll (30s long-poll) -> telegram_bot.py
                                                    |
                                      +-------------+-------------+
                                      v             v             v
                                Gemma 4 E2B    Quick tool     Coding task
                                classifies     (TOOL_HANDLERS) (claude CLI
                                intent                         via SSH)
                                      |             |             |
                                      |             v             v
                                      |        tool result   background task
                                      |             |        with milestones
                                      |             v             |
                                      +----> reply to Telegram <--+
```

## File Layout

```
docker/mcp/tools/telegram_bot.py    # poll loop, classifier, dispatchers, task tracker
tests/tools/test_telegram_bot.py    # unit tests
docker/mcp/app.py                   # ~5 lines to start poll loop on startup
```

No new dependencies. No new Docker config (env vars already in compose from notify tool).

## Component Design

### 1. Poll Loop

Background asyncio.Task started on aichat-mcp startup when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are both set.

- Calls getUpdates(offset=last+1, timeout=30) using Telegram long-poll (not a busy spin)
- On message: auth check, send "Got it" acknowledgment, classify via Gemma, dispatch, reply
- On error: log, sleep 5s, retry. Never crash the loop.
- Graceful shutdown: cancel task on SIGTERM
- No startup announcement to Telegram

### 2. Auth Gate

Single check against TELEGRAM_CHAT_ID environment variable. Messages from any other chat ID are silently ignored. No response to unauthorized users.

### 3. Intent Classifier

**Endpoint:** http://192.168.50.2:1234/v1/chat/completions (LM Studio, Gemma 4 E2B, already hot)

**System prompt:** Classifies user message into structured JSON with one of four types:

- `{"type": "tool", "tool": "<name>", "action": "<action>", "args": {...}}` for MCP tool calls
- `{"type": "code", "repo": "<repo or null>", "task": "<description>"}` for coding tasks in existing repos
- `{"type": "create", "name": "<project-name>", "description": "<what to build>", "language": "<rust|python|node|go|haskell|other>"}` for new project creation
- `{"type": "question", "text": "<the question>"}` for conversational queries

The prompt includes the full list of available tools and their actions, plus natural language examples for calibration. Gemma generalizes from examples to handle fuzzy input ("what's hot" maps to monitor/thermals, "put youtube on the tv" maps to iot/launch).

**Create examples:**
- "make me a rust cli for managing ssh keys" -> `{"type": "create", "name": "ssh-key-manager", "description": "Rust CLI tool for managing SSH keys", "language": "rust"}`
- "new python script to monitor disk usage" -> `{"type": "create", "name": "disk-monitor", "description": "Python script to monitor disk usage", "language": "python"}`
- "start a new go project for a webhook relay" -> `{"type": "create", "name": "webhook-relay", "description": "Go webhook relay service", "language": "go"}`

**Fallback directive:** "If the message doesn't clearly map to a tool action, prefer type 'question' over guessing wrong."

**Latency:** ~200ms. **Cost:** Zero (local model).

**Error handling:** If JSON parsing fails or type is unrecognized, reply with: "I didn't understand that. Try something like 'how's the fleet?' or 'fix the test in aihelp'."

### 4. Tool Dispatcher (Quick Ops)

For type "tool" intents:

1. Send "Running: {tool} {action}" to Telegram
2. Call TOOL_HANDLERS[tool]({"action": action, **args})
3. Extract text from response blocks
4. Reply with the result (truncated to 4096 chars for Telegram limit)

Latency: 2-5 seconds total. Runs inline, not as a background task.

### 5. Code Dispatcher (Coding Tasks)

For type "code" intents:

1. If repo is null, reply "Which repo? (e.g., aihelp, cyberdeck, aichat)" and wait for next message. Store pending task state keyed by chat ID.
2. Send "Working on: {task}" to Telegram
3. Spawn asyncio.Task running the code execution function
3. The task function:
   a. Constructs Claude Code CLI: `claude --output-format stream-json -p "<task>" --allowedTools "Edit,Write,Bash,Read,Glob,Grep"`
   b. If repo specified, prepends `cd ~/git/{repo} &&`
   c. Runs via SSH subprocess to amarillo with stdout piped
   d. Reads stdout line by line, parses JSON events
   e. Sends milestone updates on state transitions (deduplicated)
   f. On completion, assembles and sends detailed summary

**Milestone extraction from stream-json events:**

| Stream event | Milestone |
|---|---|
| First tool_use with Read/Glob/Grep | "Reading codebase..." |
| First tool_use with Edit/Write | "Writing code..." |
| First tool_use with Bash containing test keywords | "Running tests..." |
| First tool_use with Bash containing git commit | "Committing..." |
| No milestone for 90 seconds | "Still working..." |
| Result with success | Detailed summary |
| Result with error | Error report |

Each milestone fires only once per task (tracked in a set). Heartbeat fires at 90s of silence to prevent dead air.

### 6. Create Dispatcher (New Projects)

For type "create" intents:

1. Send "Creating project: {name} ({language})" to Telegram
2. Spawn asyncio.Task running the create function
3. The task function:
   a. Creates directory: `mkdir -p ~/git/{name} && cd ~/git/{name} && git init`
   b. Runs Claude Code with a scaffolding prompt via SSH:
      `claude --output-format stream-json -p "Create a new {language} project: {description}. Set up project structure, CLAUDE.md (inheriting from ~/.claude/CLAUDE.md conventions), CI workflow for GitHub Actions (self-hosted runners, no attest-build-provenance), README, and initial source files. Initialize git and make the first commit." --allowedTools "Edit,Write,Bash,Read,Glob,Grep"`
   c. Same streaming milestone extraction as code dispatcher
   d. Sends detailed summary when done

**Conventions enforced via the prompt:**
- CLAUDE.md created per user's new-repo convention (from memory)
- CI workflow follows the user's patterns (self-hosted runners, no attestations, no macOS)
- Project lives at `~/git/{name}` (filesystem containment)
- Language-specific setup: Cargo.toml for Rust, pyproject.toml for Python, package.json for Node, go.mod for Go, cabal file for Haskell

**Name validation:** Same rules as _validate_repo() — alphanumeric + hyphens only. If invalid, reply with error.

**Example flow:**
```
You: "make me a rust cli for managing ssh keys"
Bot: "Got it — classifying..."
Bot: "Creating project: ssh-key-manager (rust)"
Bot: "Reading codebase..."
Bot: "Writing code..."
Bot: "Committing..."
Bot: "Done — ssh-key-manager (35s)
     
     Created Rust CLI project with clap argument parsing, 
     SSH key generation, and listing commands.
     
     Files: src/main.rs, src/lib.rs, Cargo.toml, CLAUDE.md, 
            .github/workflows/ci-rust.yml, README.md
     Commit: abc1234 on main
     Branch: main"
```

### 7. Detailed Summary Format (Code and Create)

```
Done -- {repo} ({elapsed}s)

{2-3 sentence description of what was done}

Files: {file list with +/- line counts}
Tests: {pass/fail count}
Commit: {short SHA} on {branch}
Branch: {branch name}
```

Assembled from stream events:
- Description: from the result event text field
- Files: from Edit/Write tool_use events
- Tests: from last Bash event containing test output
- Commit: from Bash event containing git commit output

If any field cannot be extracted, it is omitted (not fabricated).

### 7. Task Tracker

In-memory dict tracking active coding tasks:

```python
@dataclasses.dataclass
class TaskState:
    task_id: str            # short UUID
    repo: str | None
    description: str
    status: str             # "running", "done", "failed"
    started_at: float
    milestones_sent: set[str]
    asyncio_task: asyncio.Task

_active_tasks: dict[str, TaskState] = {}
```

- Quick tool calls are NOT tracked (run inline)
- Coding tasks get a TaskState entry when spawned
- Multiple coding tasks run concurrently
- "status" / "what's running" queries list active tasks with elapsed time
- "cancel" / "stop" kills the most recent coding task subprocess
- Completed/failed tasks removed after summary sent
- On aichat-mcp restart, dict is empty (acceptable for single-user)

### 8. Question Handler

For type "question" intents, route to Gemma 4 with a conversational system prompt. Handles queries that don't map to tools and don't need Claude Code. If the user seems to want an action, Gemma suggests the right phrasing.

## Telegram API Details

**getUpdates (long-poll):**
```
GET /bot{token}/getUpdates?offset={offset}&timeout=30&allowed_updates=["message"]
```

**sendMessage:**
```
POST /bot{token}/sendMessage
{chat_id, text, parse_mode: "Markdown", reply_to_message_id}
```

All replies use reply_to_message_id to thread responses to the original message.

**Truncation:** Messages exceeding 4096 chars are truncated with "...\n\n(truncated)".

## Testing Strategy

### Unit Tests (~15)

| Test Group | Tests | Mock Strategy |
|---|---|---|
| Auth gate | correct ID accepted, wrong ID rejected, missing ID | Mock getUpdates response |
| Intent classifier | tool intent, code intent, create intent, question intent, bad JSON fallback | Mock LM Studio response |
| Tool dispatcher | correct tool+action called, reply sent, error handled | Mock TOOL_HANDLERS + httpx |
| Code dispatcher | acknowledge sent, milestones from stream, summary format, heartbeat | Mock subprocess + httpx |
| Create dispatcher | project name validated, scaffolding prompt correct, milestones sent, summary | Mock subprocess + httpx |
| Task tracker | concurrent tasks, status query, cancel kills task | Direct function calls |
| Poll loop | offset tracking, error recovery with retry | Mock httpx |

### Integration Tests (~3)

- Live Gemma classification (skip if LM Studio unavailable)
- Live tool dispatch through classification and execution
- Skip if Telegram token not available

## Security

- Single-user auth: only TELEGRAM_CHAT_ID can interact
- No secrets in responses: tool results go through existing error sanitization
- No direct code execution from Telegram input: Gemma classifies into structured intents, dispatch handles execution through existing validated tools
- Claude Code runs with --allowedTools restriction
- Repo names validated by existing _validate_repo() in git tool
