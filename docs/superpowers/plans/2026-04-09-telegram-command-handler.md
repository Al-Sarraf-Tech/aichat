# Telegram Command Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inbound Telegram command handler to aichat-mcp — receive natural language messages, classify intent via Gemma 4, dispatch to MCP tools or Claude Code, stream milestone updates back.

**Architecture:** Long-polling background loop in telegram_bot.py started on aichat-mcp startup. Gemma 4 E2B classifies intent into tool/code/create/question. Tool calls dispatch inline to existing TOOL_HANDLERS. Coding and create tasks spawn background asyncio.Tasks that stream Claude Code output via SSH and extract milestones. All replies go back to Telegram.

**Tech Stack:** Python 3.14, asyncio, httpx, FastAPI lifespan hook. No new dependencies.

**Spec:** docs/superpowers/specs/2026-04-09-telegram-command-handler-design.md

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| docker/mcp/tools/telegram_bot.py | Create | Poll loop, auth, classifier, dispatchers, task tracker |
| docker/mcp/app.py:33-35 | Modify | Add import + lifespan handler |
| docker/mcp/app.py:787 | Modify | Add lifespan to FastAPI app |
| tests/tools/test_telegram_bot.py | Create | Unit tests (~18) |
| tests/tools/test_telegram_bot_integration.py | Create | Integration tests (~3) |

---

## Build Sequence

7 tasks:

1. Telegram API helpers + unit tests
2. Intent classifier + unit tests
3. Poll loop + auth gate + unit tests
4. Dispatchers + milestone streaming + unit tests
5. Task tracker + question handler + unit tests
6. App.py integration
7. Build + live test + verify

---

## Task 1: Telegram API Helpers + Unit Tests

**Files:**
- Create: docker/mcp/tools/telegram_bot.py
- Create: tests/tools/test_telegram_bot.py

- [ ] **Step 1.1:** Create test file with tests for _send_telegram (sends POST with chat_id, text, parse_mode, reply_to_message_id), truncation over 4096 chars, _get_updates (calls with offset and timeout=30), _get_updates error recovery (returns empty list, no crash)

- [ ] **Step 1.2:** Run tests, verify they fail

- [ ] **Step 1.3:** Create telegram_bot.py with:
  - Constants: _TOKEN, _CHAT_ID, _BASE_URL, _LM_STUDIO_URL, _MAX_MSG_LEN (4096), _SSH_HOST, _SSH_PORT, _SSH_USER, _SSH_KEY from environment
  - _send_telegram(text, reply_to=None): POST to /sendMessage with parse_mode Markdown, truncate with "(truncated)" suffix if over limit, catch and log errors
  - _get_updates(offset): GET /getUpdates with offset, timeout=30, allowed_updates=["message"], catch and log errors returning empty list

- [ ] **Step 1.4:** Run tests, verify they pass

- [ ] **Step 1.5:** Commit: feat(mcp): add Telegram bot helpers -- send, getUpdates, truncation

---

## Task 2: Intent Classifier + Unit Tests

**Files:**
- Modify: docker/mcp/tools/telegram_bot.py
- Modify: tests/tools/test_telegram_bot.py

- [ ] **Step 2.1:** Add tests for _classify_intent: tool intent parsed (monitor/overview), code intent parsed (repo + task), create intent parsed (name + description + language), question intent parsed, malformed JSON returns fallback question intent, LM Studio timeout returns fallback

- [ ] **Step 2.2:** Run tests, verify they fail

- [ ] **Step 2.3:** Implement:
  - Intent dataclass with fields: type, tool, action, args, repo, task, name, description, language, text
  - _CLASSIFIER_SYSTEM_PROMPT with all tool actions, 4 intent types (tool/code/create/question), natural language examples, fallback directive
  - _classify_intent(message) -> Intent: POST to LM Studio /v1/chat/completions with gemma-4-e2b-it, temperature 0.1, max_tokens 256. Parse JSON from response (handle markdown wrapping). On any error, return Intent(type="question", text=message)

- [ ] **Step 2.4:** Run tests, verify they pass

- [ ] **Step 2.5:** Commit: feat(mcp): add Gemma 4 intent classifier for Telegram bot

---

## Task 3: Poll Loop + Auth Gate + Unit Tests

**Files:**
- Modify: docker/mcp/tools/telegram_bot.py
- Modify: tests/tools/test_telegram_bot.py

- [ ] **Step 3.1:** Add tests for: _is_authorized (correct ID true, wrong ID false, missing ID false), poll_loop processes authorized message, ignores unauthorized, updates offset, recovers from error with sleep+retry

- [ ] **Step 3.2:** Run tests, verify they fail

- [ ] **Step 3.3:** Implement:
  - _pending_code dict for "which repo?" follow-ups keyed by chat_id
  - _is_authorized(message): check message.chat.id against _CHAT_ID
  - _handle_message(message): extract text and msg_id, check pending repo follow-up, check status/cancel keywords, send "Got it -- classifying..." ack, classify intent, dispatch based on type (tool/code/create/question), handle missing repo with "Which repo?" prompt
  - poll_loop(): if token+chat_id not set return immediately with log. Long-poll loop: getUpdates with offset tracking, auth check, spawn _handle_message as fire-and-forget task. On error: log, sleep 5s, retry. On CancelledError: return cleanly.

- [ ] **Step 3.4:** Run tests, verify they pass

- [ ] **Step 3.5:** Commit: feat(mcp): add Telegram poll loop with auth gate

---

## Task 4: Dispatchers + Milestone Streaming + Unit Tests

**Files:**
- Modify: docker/mcp/tools/telegram_bot.py
- Modify: tests/tools/test_telegram_bot.py

- [ ] **Step 4.1:** Add tests for: tool dispatcher calls correct TOOL_HANDLERS and sends reply, unknown tool sends error, code dispatcher sends ack and spawns task, milestone extraction from stream-json (Read->reading, Edit->writing, Bash+pytest->testing, git commit->committing), detailed summary format on completion, heartbeat after 90s, subprocess failure handled, create dispatcher validates name and sends ack, create constructs scaffolding prompt with language and conventions

- [ ] **Step 4.2:** Run tests, verify they fail

- [ ] **Step 4.3:** Implement:
  - TaskState dataclass: task_id (short uuid), repo, description, status (running/done/failed), started_at, asyncio_task, process
  - _active_tasks dict
  - _validate_repo_name(name): alphanumeric + hyphens only
  - _dispatch_tool(intent, reply_to): send "Running: tool action", call TOOL_HANDLERS, extract text from content blocks, reply. Handle unknown tool and exceptions.
  - _stream_claude(ssh_command, reply_to, task_state) -> str: SSH subprocess with stdout piped, read line by line parsing JSON, detect milestones (Read/Glob/Grep -> "Reading codebase...", Edit/Write -> "Writing code...", Bash with test keywords -> "Running tests...", Bash with git commit -> "Committing..."), deduplicate milestones via set, heartbeat task fires at 90s silence, track edited files and bash output, return _build_summary on completion
  - _build_summary(task_state, final_text, edited_files, last_bash_output, returncode): format "Done/Failed -- repo (elapsed)" with description, files, commit info. Omit fields that cannot be extracted.
  - _dispatch_code(intent, reply_to): validate repo name, create TaskState, spawn asyncio.Task that: builds SSH command with cd to repo + claude --output-format stream-json --dangerously-skip-permissions -p, calls _stream_claude, sends summary, handles cancel/failure, cleans up _active_tasks
  - _dispatch_create(intent, reply_to): validate project name, send "Creating project: name (lang)", create TaskState, spawn task that: mkdir + git init + claude with scaffolding prompt (CLAUDE.md, CI, README, source, no macOS, no attestations), stream milestones, send summary

- [ ] **Step 4.4:** Run tests, verify they pass

- [ ] **Step 4.5:** Commit: feat(mcp): add tool, code, and create dispatchers with milestone streaming

---

## Task 5: Task Tracker + Question Handler + Unit Tests

**Files:**
- Modify: docker/mcp/tools/telegram_bot.py
- Modify: tests/tools/test_telegram_bot.py

- [ ] **Step 5.1:** Add tests for: _dispatch_status lists active tasks with elapsed, returns "No active tasks" when empty, _dispatch_cancel cancels most recent task and sends confirmation, returns "Nothing to cancel" when empty, _dispatch_question sends to Gemma and replies, _dispatch_question handles timeout

- [ ] **Step 5.2:** Run tests, verify they fail

- [ ] **Step 5.3:** Implement:
  - _dispatch_status(reply_to): list _active_tasks with task_id, description, elapsed, status. "No active tasks" if empty.
  - _dispatch_cancel(reply_to): find most recent task (max started_at), cancel asyncio_task, kill process, remove from dict, confirm. "Nothing to cancel" if empty.
  - _QUESTION_SYSTEM_PROMPT: infrastructure assistant for Fedora 43 home lab, concise answers, suggest action phrasing
  - _dispatch_question(intent, reply_to): POST to LM Studio with question system prompt, temperature 0.7, max_tokens 512. Reply with answer. Handle errors.

- [ ] **Step 5.4:** Run tests, verify they pass

- [ ] **Step 5.5:** Commit: feat(mcp): add task tracker (status/cancel) and question handler

---

## Task 6: App.py Integration

**Files:**
- Modify: docker/mcp/app.py

- [ ] **Step 6.1:** Add import (after existing tools imports, ~line 35):
  ```python
  from tools.telegram_bot import poll_loop as _telegram_poll_loop
  ```

- [ ] **Step 6.2:** Add lifespan handler before app = FastAPI line (~line 787):
  ```python
  from contextlib import asynccontextmanager

  @asynccontextmanager
  async def _lifespan(app):
      task = asyncio.create_task(_telegram_poll_loop())
      yield
      task.cancel()
      try:
          await task
      except asyncio.CancelledError:
          pass
  ```

- [ ] **Step 6.3:** Change FastAPI creation to include lifespan:
  ```python
  app = FastAPI(title="aichat-mcp", lifespan=_lifespan)
  ```

- [ ] **Step 6.4:** Verify import works:
  ```bash
  cd ~/git/aichat && PYTHONPATH=docker/mcp:$PYTHONPATH python -c "from tools.telegram_bot import poll_loop; print('OK')"
  ```

- [ ] **Step 6.5:** Run all existing tests to verify no regressions:
  ```bash
  cd ~/git/aichat && python -m pytest tests/tools/ tests/test_architecture.py --tb=short -q
  ```

- [ ] **Step 6.6:** Commit: feat(mcp): wire Telegram poll loop into app.py lifespan

---

## Task 7: Build + Live Test + Verify

- [ ] **Step 7.1:** Run full unit test suite
  ```bash
  cd ~/git/aichat && python -m pytest tests/tools/ tests/test_architecture.py -v --tb=short
  ```

- [ ] **Step 7.2:** Run orchestrator scan
  ```bash
  orchestrator-enterprise scan ~/git/aichat/.github/workflows/ --fail-on error
  ```

- [ ] **Step 7.3:** Rebuild and restart aichat-mcp
  ```bash
  cd ~/git/aichat && docker compose build aichat-mcp && docker compose up -d --force-recreate aichat-mcp
  ```

- [ ] **Step 7.4:** Verify health endpoint
  ```bash
  curl -sf http://127.0.0.1:8096/health | jq '{ok: .ok, tools: .tools}'
  ```

- [ ] **Step 7.5:** Check container logs for poll loop start
  ```bash
  docker logs aichat-aichat-mcp-1 --tail 20 2>&1 | grep -i telegram
  ```

- [ ] **Step 7.6:** Live test from Telegram:
  1. "how's the fleet?" -- should get monitor overview
  2. "is CI green?" -- should get CI scorecard
  3. "status" -- should get "No active tasks"
  4. "what time is it?" -- should get Gemma conversational answer
  5. Coding task test when ready

- [ ] **Step 7.7:** Commit any fixes needed
