# AIChat

[![CI](https://github.com/Al-Sarraf-Tech/aichat/actions/workflows/ci-aichat.yml/badge.svg)](https://github.com/Al-Sarraf-Tech/aichat/actions/workflows/ci-aichat.yml)
[![Dart CI](https://github.com/Al-Sarraf-Tech/aichat/actions/workflows/ci-dart.yml/badge.svg)](https://github.com/Al-Sarraf-Tech/aichat/actions/workflows/ci-dart.yml)
[![Orchestrator Scan](https://github.com/Al-Sarraf-Tech/aichat/actions/workflows/orchestrator-scan.yml/badge.svg)](https://github.com/Al-Sarraf-Tech/aichat/actions/workflows/orchestrator-scan.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Dart](https://img.shields.io/badge/dart-stable-blue)
![Platform](https://img.shields.io/badge/platform-Linux-informational)
![Version](https://img.shields.io/badge/version-0.2.0-green)

> CI runs on self-hosted runners governed by the [Haskell Orchestrator](https://github.com/Al-Sarraf-Tech/Haskell-Orchestrator).

A local-first AI platform built for production use on bare-metal Linux. 16-container Docker Compose stack, 2,200+ Python source files, 85 MCP tools, three user interfaces, and no dependency on cloud AI services.

**Interfaces:**
- **Web UI** — Dart/Shelf backend with vanilla JS frontend. JWT auth, SSE streaming, tool cards, image grids, per-model capability badges, 31 personalities.
- **Terminal UI (TUI)** — Python/Textual application. 14 keybinds, full MCP tool access, session management, one-shot CLI mode.
- **Telegram bot** — Python bot with streaming replies, tool dispatch, and per-chat session isolation.

**AI backends:**
- [LM Studio](https://lmstudio.ai) on RTX 3090 for main inference (OpenAI-compatible API)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) on RTX 3090 for image generation (FLUX Schnell, FLUX Dev, SDXL Lightning, SDXL Turbo)
- Intel Arc A380 running `qwen2.5-3b-instruct` for tool routing and prompt preprocessing
- Intel Arc A380 OpenVINO for embeddings and CLIP operations

---

## Table of Contents

- [Architecture](#architecture)
- [Hardware Layout](#hardware-layout)
- [Services](#services)
- [Models](#models)
- [MCP Tool System](#mcp-tool-system)
- [Tool Tier System](#tool-tier-system)
- [Interfaces](#interfaces)
  - [Web UI](#web-ui)
  - [Terminal UI (TUI)](#terminal-ui-tui)
  - [Telegram Bot](#telegram-bot)
- [Image Generation](#image-generation)
- [GPU Resource Management](#gpu-resource-management)
- [Arc A380 Preprocessing](#arc-a380-preprocessing)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [CI/CD Pipeline](#cicd-pipeline)
- [Development](#development)
- [Test Coverage](#test-coverage)
- [Known Limitations](#known-limitations)

---

## Architecture

```
                    ┌──────────────────────────────────────────────────────────────┐
                    │  Clients                                                      │
                    │  Browser (:8200) ── Terminal (aichat TUI) ── Telegram bot    │
                    └─────────┬──────────────────────┬─────────────────────────────┘
                              │                      │
                              ▼                      │
                    ┌──────────────────┐             │
                    │   aichat-auth    │             │
                    │   Flask JWT proxy│             │
                    │   :8200 (public) │             │
                    │   :8247 (admin)  │             │
                    └────────┬─────────┘             │
                             │                       │
                             ▼                       ▼
                    ┌──────────────────┐    ┌─────────────────────┐
                    │   aichat-web     │    │   TUI / Telegram     │
                    │   Dart/Shelf     │    │   MCP stdio / HTTP   │
                    │   SSE streaming  │    └──────────┬──────────┘
                    │   :8200 (int)    │               │
                    └──────┬───────────┘               │
                           │                           │
                           └──────────┬────────────────┘
                                      │
                                      ▼
                          ┌───────────────────────────┐
                          │   aichat-mcp  :8096        │
                          │   FastAPI MCP gateway      │
                          │   85 tools, 16 mega-tools  │
                          │   HTTP + SSE (JSON-RPC)    │
                          └────┬──────┬──────┬────┬───┘
                               │      │      │    │
                  ┌────────────┘  ┌───┘  ┌──┘  ┌─┘
                  ▼               ▼      ▼     ▼
           ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
           │aichat-   │  │aichat-   │  │aichat-   │  │aichat-   │  │aichat-   │
           │data:8091 │  │vision    │  │docs:8101 │  │sandbox   │  │browser   │
           │          │  │:8099     │  │          │  │:8095     │  │:8104     │
           │memory    │  │OCR       │  │PDF ingest│  │Python/JS │  │Playwright│
           │graph     │  │CLIP emb  │  │full-text │  │bash exec │  │Chromium  │
           │planner   │  │YOLOv8n   │  │openpyxl  │  │          │  │          │
           │jobs      │  │FFmpeg    │  │pdfminer  │  │          │  │          │
           │embeddings│  │OpenVINO  │  │          │  │          │  │          │
           └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
                │
    ┌───────────┼──────────────┐
    ▼           ▼              ▼
┌─────────┐ ┌──────────┐ ┌─────────┐
│Postgres │ │Qdrant    │ │Valkey   │
│:5432    │ │:6333     │ │(Redis)  │
│auth,data│ │vectors   │ │ctx cache│
└─────────┘ └──────────┘ └─────────┘

┌──────────────────────────────────────────────┐
│   Intel Arc A380  (amarillo, :1235)           │
│   qwen2.5-3b-instruct (always loaded)        │
│   Tool routing · prompt compression ·        │
│   context compaction (Redis-cached)          │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│   LM Studio  (dominus, 192.168.50.2:1234)    │
│   Main inference — 6 active chat models      │
│   RTX 3090 · 24 GB VRAM · JIST auto-swap    │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│   ComfyUI  (dominus WSL2, 100.91.44.100:8188)│
│   FLUX Schnell · FLUX Dev · SDXL Lightning   │
│   SDXL Turbo · GPU TTL auto-unload           │
└──────────────────────────────────────────────┘
```

### Request Flow

```
Browser → :8200 (aichat-auth JWT check)
  → aichat-web (Dart/Shelf)
    → Arc A380 :1235 (tool routing + prompt compression, ~3-5s)
    → aichat-mcp :8096 (tool execution)
      → aichat-searxng (web/image search)
      → aichat-browser (page scraping/screenshots)
      → aichat-vision (OCR, CLIP, face detection, video)
      → aichat-sandbox (code execution)
      → ComfyUI :8188 (image generation — FLUX/SDXL)
    → RTX 3090 :1234 (main LLM generation, SSE streaming)
  ← SSE tokens stream back to browser
```

---

## Hardware Layout

```
┌──────────────────────────────────────────┐     ┌─────────────────────────────────────┐
│  amarillo  (Fedora 43, Intel Arc A380)   │     │  dominus  (Windows, RTX 3090)        │
│  192.168.50.5                            │     │  192.168.50.2                        │
│                                          │     │                                      │
│  Docker Compose stack (16 containers)   │     │  LM Studio :1234                     │
│  NVMe: /mnt/nvmeINT/aichat/             │─────►│  6 chat models, JIST auto-swap      │
│  ~930 GB free                            │     │  24 GB VRAM                          │
│                                          │     │                                      │
│  Intel Arc A380 (6 GB VRAM)             │     │  ComfyUI :8188 (WSL2)               │
│  LM Studio :1235 (qwen2.5-3b)           │     │  FLUX Schnell, FLUX Dev              │
│  OpenVINO embeddings (:8105)            │     │  SDXL Lightning, SDXL Turbo          │
└──────────────────────────────────────────┘     └─────────────────────────────────────┘
```

Single-machine operation is supported — point `LM_STUDIO_URL` and `TOOL_ROUTER_URL` to `localhost` and leave `COMFYUI_URL` unset to skip image generation.

---

## Services

| Service | Port | Persistence | Purpose |
|---------|------|-------------|---------|
| `aichat-db` | 5432 (host: 5435) | `/mnt/nvmeINT/aichat/postgres/` | PostgreSQL 16 — auth, articles, metadata |
| `aichat-vector` | 6333 | `/mnt/nvmeINT/aichat/qdrant/` | Qdrant — vector search, CLIP embeddings |
| `aichat-redis` | 6379 (internal) | `/mnt/nvmeINT/aichat/redis/` | Valkey — context compaction cache, tool routing cache |
| `aichat-minio` | 9001/9002 | `/mnt/nvmeINT/aichat/minio/` | MinIO S3-compatible object storage |
| `aichat-data` | 8091 | — | Consolidated data API: memory, knowledge graph, planner, jobs, research, embeddings, batch ops |
| `aichat-vision` | 8099 | — | OCR (Tesseract), CLIP (OpenVINO), object detection (YOLOv8n), video (FFmpeg), OpenVINO SDXL |
| `aichat-docs` | 8101 | — | PDF extraction (pdfminer), document ingestion, full-text search, Excel (openpyxl) |
| `aichat-sandbox` | 8095 | — | Isolated code execution: Python, JavaScript, bash (non-root container) |
| `aichat-browser` | 8104 | — | Headless Chromium via Playwright — scraping, screenshots, page images |
| `aichat-searxng` | 8080 (internal) | — | Self-hosted SearXNG meta-search (Google, Bing, DuckDuckGo) |
| `aichat-jupyter` | 8098 (internal) | — | Stateful Jupyter kernel for code execution |
| `aichat-inference` | 8105 | — | Intel Arc OpenVINO embeddings (offloads from RTX 3090) |
| `aichat-whatsapp` | 8097 | `/mnt/nvmeINT/aichat/whatsapp/` | WhatsApp bot integration |
| `aichat-mcp` | **8096** | — | MCP HTTP/SSE gateway — 85 tools, 16 mega-tool categories |
| `aichat-web` | 8200 (internal) | `/mnt/nvmeINT/aichat/web-db/` | Dart/Shelf web server + vanilla JS frontend |
| `aichat-auth` | **8200** (public), 8247 (admin) | — | Flask JWT auth proxy + user management admin panel |

Only `:8200` and `:8247` are publicly exposed. All inter-service communication is on the internal Docker network. Host ports are defined exclusively in `docker-compose.ports.yml` (not the base compose file).

All data volumes persist to NVMe at `/mnt/nvmeINT/aichat/`. Adjust this path in `docker-compose.yml` for other systems.

---

## Models

| Model | Type | Quant | Context | Tools | Reasoning |
|-------|------|-------|---------|-------|-----------|
| `openai/gpt-oss-20b` | LLM | MXFP4 | 131K | 9 | No |
| `dolphin-mistral-glm-4.7-flash-24b` | LLM | Q4_K_S | 32K | 9 | Yes |
| `qwen/qwen3.5-9b` | VLM | Q4_K_M | 262K | 7 | Yes |
| `zai-org/glm-4.6v-flash` | VLM | Q8_0 | 131K | 7 | Yes |
| `ibm/granite-4-h-tiny` | LLM | Q8_0 | 1M | 5 | No |
| `microsoft/phi-4-mini-reasoning` | LLM | Q8_0 | 131K | 2 | Yes |

The `qwen2.5-3b-instruct` model runs permanently on the Intel Arc A380 as the tool router and prompt preprocessor (not a chat model).

---

## MCP Tool System

The MCP server (`aichat-mcp`, port 8096) exposes **85 callable tools** organized into **16 mega-tool categories**. It speaks MCP JSON-RPC over HTTP and SSE, making it compatible with LM Studio, Claude Desktop, and any MCP-aware client. The LM Studio connection config is in `lmstudio-mcp.json`.

### Tool Categories

| Category | Tools | Description |
|----------|-------|-------------|
| `web` | `web_search`, `web_fetch`, `page_extract`, `page_scrape`, `page_images`, `extract_article`, `structured_extract`, `smart_summarize` | SearXNG/DDG/Bing search, page fetching, content extraction |
| `browser` | `browser`, `screenshot`, `scroll_screenshot`, `bulk_screenshot`, `screenshot_search`, `browser_download_page_images`, `browser_save_images` | Headless Chromium automation, visual captures |
| `image` | `image_search`, `image_generate`, `image_pipeline`, `image_edit`, `image_enhance`, `image_remix`, `image_caption`, `image_annotate`, `image_crop`, `image_diff`, `image_stitch`, `image_upscale`, `image_zoom`, `image_scan`, `fetch_image`, `ocr_image` | Full image lifecycle: search, gen (ComfyUI/FLUX/SDXL), editing, analysis |
| `document` | `docs_ingest`, `docs_extract_tables`, `pdf_read`, `pdf_edit`, `pdf_merge`, `pdf_split`, `pdf_fill_form`, `ocr_pdf` | PDF operations, document ingestion, OCR, table extraction |
| `code` | `code_run` | Python, JavaScript, bash execution in the sandbox container |
| `media` | `video_info`, `video_frames`, `video_thumbnail`, `tts` | Video analysis, frame extraction, text-to-speech |
| `memory` | `memory_store`, `memory_recall` | Persistent key-value memory with TTL |
| `knowledge` | `graph_add_node`, `graph_add_edge`, `graph_query`, `graph_search`, `graph_path` | NetworkX/SQLite knowledge graph |
| `vector` | `vector_store`, `vector_search`, `vector_delete`, `vector_collections`, `embed_store`, `embed_search` | Qdrant vector store, semantic search |
| `data` | `db_store_article`, `db_search`, `db_store_image`, `db_list_images`, `db_cache_get`, `db_cache_store`, `get_errors` | PostgreSQL-backed article/image store and cache |
| `planner` | `plan_create_task`, `plan_get_task`, `plan_list_tasks`, `plan_complete_task`, `plan_fail_task`, `plan_delete_task` | Dependency-aware task queue |
| `jobs` | `job_submit`, `job_status`, `job_result`, `job_cancel`, `job_list`, `batch_submit` | Durable async job system with batch dispatch |
| `research` | `researchbox_push`, `researchbox_search` | RSS-backed deep research pipeline |
| `custom_tools` | `create_tool`, `list_custom_tools`, `call_custom_tool`, `delete_custom_tool` | User-defined runtime tools |
| `vision` | `face_recognize` | Face detection and recognition |
| `orchestration` | `orchestrate`, `chat` | Multi-tool orchestration, SSE chat stream |

**Internal-only** (never sent to chat models): `knowledge`, `vector`, `jobs`, `custom_tools`, `planner`, `think`, `system`.

### Module Structure (`docker/mcp/`)

```
app.py                  6,561 lines — FastAPI MCP app, all tool registrations
orchestrator.py         — intent classification, bounded concurrency, resource governance
source_strategy.py      — configurable news source preferences
agents.py               — SSH CLI agent dispatch (Claude, Codex, Gemini), LM Studio routing
gpu_ttl.py              — GPU idle TTL watcher (ComfyUI + LM Studio + vision)
handlers/               — per-category HTTP route handlers
tools/
    web.py              603 lines — SearXNG/DDG/Bing + content extraction
    browser.py          1,294 lines — Playwright automation
    document.py         375 lines — PDF/OCR/ingest
    media.py            — video + TTS
    code.py             252 lines — sandbox execution
    memory.py           — key-value store
    knowledge.py        132 lines — NetworkX graph
    data.py             107 lines — PostgreSQL article/cache
    planner.py          238 lines — task queue
    research.py         260 lines — RSS research
    ssh.py              337 lines — remote command execution
    system.py           308 lines — system monitoring
    git.py              543 lines — Git operations
    monitor.py          613 lines — system metrics
    notify.py           276 lines — notifications
    telegram/           — Telegram bot tools
    iot.py              396 lines — IoT device integration
```

---

## Tool Tier System

Tools are allocated per model based on observed reliability. Sending all 85 tools to every model causes timeouts and hallucinated tool calls.

### Default Tier (7 tools)

| Tool | Key Actions | Description |
|------|-------------|-------------|
| `web` | search, fetch, extract, news, wikipedia, arxiv | Web search via SearXNG/DDG/Bing, page fetching |
| `image` | search, generate, edit, caption, face_detect | Image search (real CDN URLs), ComfyUI generation |
| `browser` | navigate, screenshot, scrape | Headless Chromium |
| `research` | deep, rss_search | Multi-hop research with RSS |
| `code` | python, javascript, bash | Sandboxed execution |
| `document` | ingest, extract, ocr, pdf | Document processing |
| `memory` | store, recall, list, delete | Persistent KV memory |

### Extended Tier (+2 for strong models)

| Tool | Description |
|------|-------------|
| `media` | Video analysis, TTS, object detection |
| `data` | PostgreSQL article storage, cache |

### Per-Model Allocation

| Model | Tools | Count |
|-------|-------|-------|
| gpt-oss-20b, dolphin | Default + Extended | 9 |
| qwen3.5-9b | Default | 7 |
| glm-4.6v-flash | web, image, browser, research, document, data, media | 7 |
| granite-tiny | web, image, browser, code, memory | 5 |
| phi-4-mini | web, browser | 2 |

### Image-Only Routing

Image requests (detected by keyword) route exclusively to the `image` tool — not `web`. The `image` tool queries SearXNG/DDG/Bing Images and returns real CDN URLs with inline base64 thumbnails. This eliminates hallucinated image URLs.

---

## Interfaces

### Web UI

Vanilla JS single-page application (~1,200 lines) served by the Dart/Shelf backend.

**Features:**
- Streaming responses with blinking cursor, token/s counter, elapsed time
- Tool cards with live elapsed timers per tool call
- Image grid (responsive CSS Grid) with lightbox (keyboard: Escape, arrow keys)
- Image dedup via URL normalization (strips CDN size suffixes, query params)
- Model selector with state dots (loaded/unloaded), quant, context length, type (LLM/VLM), tool count
- Reasoning/thinking content in collapsible cards (hidden by default)
- 31 personalities with category-filtered grid
- File uploads: images for vision models, text/code files inline
- Per-user chat isolation via JWT
- Admin panel at `:8247` — user management, IP ban control

**Dart backend (`lib/`):**

| File | Lines | Purpose |
|------|-------|---------|
| `router.dart` | ~960 | Route table, CORS, auth guard, static files |
| `image_handler.dart` | ~1,200 | ComfyUI workflows, HF fallback, async image job system |
| `model_handler.dart` | ~250 | Model listing, warmup, capability caching |
| `personalities.dart` | ~800 | 31 personalities with system prompts |
| `model_profiles.dart` | ~200 | Per-model tool tiers and parameters |
| `sanitizer.dart` | ~130 | Tool result cleaning, image URL extraction |
| `tool_router.dart` | — | Rule-based + Arc GPU tool routing |
| `llm_client.dart` | — | LM Studio HTTP client |
| `mcp_client.dart` | — | MCP JSON-RPC client |
| `database.dart` | — | SQLite conversation storage |

**SSE event types:**

| Event | Description |
|-------|-------------|
| `status` | Model loading indicator |
| `thinking` | Reasoning content (collapsible) |
| `token` | Content tokens (buffered, rendered on `done`) |
| `tool_start` | Tool execution started |
| `tool_result` | Tool execution complete (images collected) |
| `error` | Error message |
| `done` | Stream complete — triggers single markdown render pass |

Tokens accumulate in a text buffer during streaming. The DOM updates once on `done` via a single render pass (marked.js + DOMPurify + highlight.js). This eliminates DOM thrashing during long generations.

### Terminal UI (TUI)

Python/Textual application. 2,200+ Python source files across the project; TUI core is ~13,300 lines.

```bash
aichat [OPTIONS] [MESSAGE]   # interactive TUI or one-shot chat
aichat mcp                   # run MCP server over stdio
aichat repo create           # create and push GitHub repo
```

**Keyboard shortcuts:**

| Key | Action | Key | Action |
|-----|--------|-----|--------|
| F1 | Help | F7 | Sessions |
| F2 | Model picker | F8 | Settings |
| F3 | Search | F9 | New chat |
| F4 | Approval cycle | F10 | Clear |
| F5 | Theme picker | F11 | Cancel |
| F6 | Toggle streaming | F12 | Quit |
| Ctrl+S | Shell toggle | Ctrl+G | Personality |

**TUI source (`src/aichat/`):**

| File | Lines | Purpose |
|------|-------|---------|
| `app.py` | 2,733 | Main Textual application |
| `mcp_server.py` | 2,546 | MCP stdio server implementation |
| `tools/manager.py` | 2,458 | Tool dispatch and lifecycle |
| `tools/browser.py` | 1,952 | Browser tool integration |
| `client.py` | 276 | LM Studio HTTP client |
| `personalities.py` | 457 | Personality definitions |
| `ui/modals.py` | 243 | UI modal dialogs |

Install:
```bash
pip install -e ".[dev]"
```

### Telegram Bot

Located in `docker/mcp/tools/telegram/`. Components:

| File | Purpose |
|------|---------|
| `api.py` | Telegram Bot API client |
| `dispatcher.py` | Message routing and command handling |
| `stream.py` | Streaming response delivery |
| `auth.py` | Per-chat authorization |
| `db.py` | Session and history persistence |
| `poller.py` | Long-polling loop |
| `summary.py` | Conversation summarization |
| `handlers/` | Command and message handlers |

---

## Image Generation

Image generation runs on ComfyUI (WSL2, RTX 3090) via dynamic workflow construction. Both the Dart backend and MCP server can submit workflows.

### Supported Models

| Model | Steps | Resolution | Speed | Notes |
|-------|-------|-----------|-------|-------|
| FLUX Schnell | 4 | 1024×1024 | ~40s | Default, best quality/speed balance |
| FLUX Dev | 25 | 1024×1024 | ~90s | Highest quality |
| SDXL Lightning | 4 | 1024×1024 | ~3s | Fast, good quality |
| SDXL Turbo | 1 | 512×512 | ~3s | Draft quality |

### Generation Flow

1. User submits prompt via Web UI panel or MCP `image_generate` / `image_pipeline` tool
2. Dart backend creates an async job, returns `jobId` immediately
3. Background task builds ComfyUI workflow JSON for the selected model
4. Workflow POSTed to ComfyUI `/prompt`
5. Backend polls `/history/{promptId}` every 500ms (up to 600s timeout)
6. Images fetched from ComfyUI, saved to `/app/pictures/`, returned as download URLs
7. Frontend polls `/api/image/job/{jobId}` every 2s for progress

### Fallback Chain

```
ComfyUI (preferred) → LM Studio /v1/images/generations → error
```

### Known Image Generation Gaps

- `/api/image/generate` requires `COMFYUI_URL` even for cloud backend paths
- Batch `count > 1` has uninitialized child job IDs in local mode; cloud `count` is forwarded but not consumed
- SDXL Lightning img2img/inpaint skips the Lightning UNet merge
- ControlNet hardcodes SD1.5 weights and a fixed SDXL checkpoint instead of respecting the selected model
- Download endpoint (`/api/image/download/<filename>`) is not user-scoped — guessable filenames expose other users' files
- Inpaint browser flow exits before the first real status check and posts preview-scaled canvas dimensions

---

## GPU Resource Management

A background GPU TTL watcher (`docker/mcp/gpu_ttl.py`) auto-unloads idle GPU resources after a configurable timeout.

| System | Monitored Via | Unload Method |
|--------|--------------|---------------|
| ComfyUI (RTX 3090) | `/system_stats` VRAM + `/queue` | `POST /free` |
| LM Studio (RTX 3090) | `/api/v0/models` loaded state | `POST /api/v0/models/unload` |
| aichat-vision (Arc A380) | CLIP + SDXL pipeline state | `POST /unload` |

| Env Var | Default | Description |
|---------|---------|-------------|
| `GPU_IDLE_TTL` | `600` | Seconds of inactivity before unload |
| `GPU_TTL_POLL_INTERVAL` | `60` | Poll frequency (seconds) |
| `GPU_TTL_ENABLED` | `true` | Kill switch |

Safety: checks ComfyUI queue before unload; re-checks idle timestamp immediately before POST; fail-open (never unloads on unreachable endpoint).

GPU TTL state is exposed in the MCP `/health` response under `gpu_ttl`.

---

## Arc A380 Preprocessing

The Intel Arc A380 runs `qwen2.5-3b-instruct` via LM Studio on `:1235`. It serves three preprocessing roles:

**1. Tool Routing (3s timeout)**
Classifies the user message and selects 1–3 tools from the model's allowed set. Falls back to keyword-based rule routing on timeout.

**2. Prompt Compression (5s timeout)**
For small-context models (`condensed` prompt size), compresses personality system prompts from ~2KB to under 500 chars. Cached per personality+model combo in Redis.

**3. Context Compaction (Redis TTL 1h)**
Summarizes long conversations and caches the summary. Subsequent messages reuse the cached summary instead of re-summarizing.

```
User request → Dart server
  ├── Redis: cached context? (sub-ms)
  ├── If miss: Arc A380 compacts conversation (3–5s) → cache Redis
  ├── Arc A380: select tools (3s)
  ├── Arc A380: compress prompt if condensed model (5s, cached)
  └── RTX 3090: main generation with compact context + selected tools
```

Graceful degradation: if Arc or Redis is unreachable, falls back to rule-based routing and uncompressed prompts.

---

## Prerequisites

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Linux (Docker-capable) | Fedora 43 / Ubuntu 24.04 |
| Docker | 24.0+ with Compose v2 | Latest stable |
| Python | 3.12+ | 3.14 |
| Dart SDK | Stable channel | Latest stable |
| LM Studio | Any version | Latest with JIST support |
| GPU (inference) | 8 GB VRAM | NVIDIA RTX 3090 (24 GB) |
| GPU (preprocessing) | Optional | Intel Arc A380 (6 GB) |
| RAM | 16 GB | 64 GB |
| Storage | 20 GB free | NVMe SSD for Docker volumes |

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/Al-Sarraf-Tech/aichat.git
cd aichat

cat > .env << 'EOF'
POSTGRES_PASSWORD=your_secure_password_here
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=your_minio_password_here
JWT_SECRET=$(openssl rand -hex 32)
ADMIN_USER=admin
ADMIN_INITIAL_PASSWORD=your_password_here
LM_STUDIO_URL=http://192.168.50.2:1234
# Optional: Arc A380 for tool routing (leave empty for rule-based routing)
TOOL_ROUTER_URL=http://localhost:1235
# Optional: ComfyUI for image generation
COMFYUI_URL=http://your-comfyui-host:8188
EOF
```

### 2. Start all services

```bash
docker compose -f docker-compose.yml -f docker-compose.ports.yml up -d
```

First run builds all images (~10–15 min). Subsequent starts take under 30 seconds.

### 3. Verify the stack

```bash
make smoke                        # hits /health on every service
curl http://localhost:8096/health  # MCP: {"ok":true}
curl http://localhost:8200/health  # Web: {"ok":true,"service":"dartboard"}
```

### 4. Access the Web UI

Open **http://localhost:8200**. Log in with the `ADMIN_USER` / `ADMIN_INITIAL_PASSWORD` from `.env`.

### 5. Install the TUI (optional)

```bash
pip install -e ".[dev]"
aichat              # interactive TUI
aichat "question"   # one-shot chat
```

### 6. Connect via LM Studio MCP (optional)

Use `lmstudio-mcp.json` as the MCP configuration file. Points to `http://localhost:8096/sse`.

---

## Configuration

### Environment Variables

| Variable | Service | Default | Description |
|----------|---------|---------|-------------|
| `POSTGRES_PASSWORD` | db, auth, data | *required* | PostgreSQL password |
| `JWT_SECRET` | auth | *required* | JWT signing key — use `openssl rand -hex 32` |
| `ADMIN_USER` | auth | `admin` | Admin username (auto-created on first start) |
| `ADMIN_INITIAL_PASSWORD` | auth | — | Initial admin password |
| `LM_STUDIO_URL` | web | `http://192.168.50.2:1234` | Primary LM Studio endpoint |
| `LM_STUDIO_FALLBACK_URL` | web | — | Failover LM Studio URL |
| `TOOL_ROUTER_URL` | web | — | Arc A380 URL; empty = rule-based routing |
| `MCP_URL` | web | `http://aichat-mcp:8096` | MCP server URL |
| `MAX_TOOL_ITERATIONS` | web | `4` | Max tool call loops per request |
| `IMAGE_GEN_BASE_URL` | mcp | `http://192.168.50.2:1234` | LM Studio for MCP image tools |
| `IMAGE_GEN_MODEL` | mcp | *(auto)* | Pin model for MCP; empty = auto-select |
| `COMFYUI_URL` | mcp, web | — | ComfyUI endpoint for FLUX/SDXL generation |
| `VISION_GEN_URL` | mcp, web | *(internal)* | Vision service OpenVINO generate endpoint |
| `MINIO_ROOT_USER` | minio | `minioadmin` | MinIO admin user |
| `MINIO_ROOT_PASSWORD` | minio | *required* | MinIO admin password |
| `GPU_IDLE_TTL` | mcp | `600` | Seconds before idle GPU models are unloaded |
| `GPU_TTL_ENABLED` | mcp | `true` | Enable/disable GPU auto-unload |

### Single-Machine Setup

```bash
LM_STUDIO_URL=http://localhost:1234
LM_STUDIO_FALLBACK_URL=http://localhost:1234
TOOL_ROUTER_URL=    # empty = rule-based routing
```

### Two-Machine Setup (like production)

```bash
LM_STUDIO_URL=http://192.168.50.2:1234    # RTX 3090 machine
TOOL_ROUTER_URL=http://192.168.50.5:1235  # Arc A380 machine (or same host)
```

---

## CI/CD Pipeline

Three workflow files in `.github/workflows/`:

### `ci-aichat.yml`
Generated and governed by the Haskell Orchestrator.

| Job | Steps |
|-----|-------|
| `repo-guard` | Verifies repository ownership before all other jobs |
| `lint` | `ruff check .` |
| `test` | `pytest tests/test_architecture.py` |
| `security` | `gitleaks`, `bandit`, `pip-audit`, `shellcheck`, `trivy`, `semgrep` |
| `build` | `docker compose build` of core services |
| `release` | SHA256SUMS + GitHub release assets on tag push |

### `ci-dart.yml`
Triggers on Dart/web file changes only.

| Job | Steps |
|-----|-------|
| `lint` | `dart format --set-exit-if-changed`, `dart analyze` |
| `test` | `dart test test/dart/` |
| `frontend` | `node -c docker/web/web/app.js` syntax check |

### `orchestrator-scan.yml`
Delegates to `Al-Sarraf-Tech/Haskell-Orchestrator/.github/workflows/orchestrator-scan.yml@v4.0.0` for pre-merge governance.

---

## Development

### Makefile Targets

```bash
# Docker stack
make build          # build all service images
make up             # start full stack
make down           # stop and remove containers
make restart        # down + up
make logs           # follow all service logs
make smoke          # health check all services

# Python
make test           # run pytest suite
make lint           # ruff + mypy
make security-checks  # shellcheck + bandit + safety + semgrep + trivy

# Dart
make dart-get       # install Dart dependencies
make dart-analyze   # run dart analyze
make dart-test      # run Dart tests
make dart-build     # compile native binary
make dart-run       # run web server locally
```

### Project Layout

```
aichat/
├── src/aichat/              # TUI (Python/Textual)
│   ├── app.py               # Main TUI (2,733 lines)
│   ├── mcp_server.py        # MCP stdio server (2,546 lines)
│   ├── tools/               # Tool implementations (manager, browser, LM Studio, etc.)
│   ├── ui/                  # Textual widgets, modals, keybind bar
│   ├── personalities.py     # Personality definitions
│   └── themes.py            # Theme registry
├── lib/                     # Dart web server
│   ├── router.dart          # HTTP API, SSE streaming, tool execution loop
│   ├── image_handler.dart   # ComfyUI image generation
│   ├── model_profiles.dart  # Per-model tool tiers and parameters
│   ├── personalities.dart   # 31 personalities
│   ├── tool_router.dart     # Rule-based + Arc GPU routing
│   ├── llm_client.dart      # LM Studio client
│   ├── mcp_client.dart      # MCP JSON-RPC client
│   └── database.dart        # SQLite conversation storage
├── bin/server.dart           # Dart server entry point
├── docker/
│   ├── mcp/                 # MCP server (FastAPI, 6,561-line app.py, 85 tools)
│   │   ├── app.py
│   │   ├── agents.py        # SSH CLI + LM Studio agent dispatch
│   │   ├── gpu_ttl.py       # GPU idle TTL watcher
│   │   ├── orchestrator.py  # Intent classification, concurrency control
│   │   ├── tools/           # 20 tool modules
│   │   ├── handlers/        # Per-category route handlers
│   │   └── search/          # SearXNG + DDG/Bing parsers
│   ├── web/                 # Web server Dockerfile + app.js/style.css/index.html
│   ├── auth/                # Flask JWT proxy
│   ├── data/                # Data service + PostgreSQL migrations
│   ├── vision/              # OCR, CLIP, YOLOv8n, video, OpenVINO SDXL
│   ├── browser/             # Playwright Chromium
│   ├── docs/                # Document/PDF service
│   ├── sandbox/             # Code execution sandbox
│   ├── jupyter/             # Jupyter kernel
│   ├── searxng/             # SearXNG config
│   ├── inference/           # Arc OpenVINO embeddings
│   └── whatsapp/            # WhatsApp bot
├── tests/                   # Python tests (73 files)
│   └── tools/               # MCP tool unit tests (11 modules)
├── test/                    # Dart tests + Playwright E2E (2 files)
├── docker-compose.yml       # Base stack definition (no host ports)
├── docker-compose.ports.yml # Host port bindings
├── lmstudio-mcp.json        # LM Studio MCP client config
├── pubspec.yaml             # Dart dependencies
├── pyproject.toml           # Python project config (version 0.2.0)
└── Makefile
```

---

## Test Coverage

### Python Tests

| Suite | File | Scope |
|-------|------|-------|
| Smoke | `tests/test_smoke.py` | Service health checks (requires running stack) |
| Architecture | `tests/test_architecture.py` | Structural constraints (CI) |
| Full regression | `tests/test_full_regression.py` | All MCP tools and service endpoints |
| Image pipeline | `tests/test_image_pipeline.py` | Image tool paths |
| Model E2E | `tests/test_model_e2e.py` | All LLM models end-to-end |
| Image rendering E2E | `tests/test_image_rendering_e2e.py` | Rendering pipeline |
| Tool priority | `tests/test_tool_priority.py` | Tool routing logic |
| MCP tool units | `tests/tools/` | Per-module unit tests (11 modules) |
| Browser E2E | `test/test_playwright_e2e.py` | Playwright browser flows |
| Dartboard E2E | `tests/test_dartboard_e2e.py` | Web server E2E |
| + 60 more | `tests/test_*.py` | Streaming, compaction, vision, TUI, SSH, agents, etc. |

73 Python test files total. Run with:

```bash
python3 -m pytest tests/test_smoke.py -v --timeout=60
python3 -m pytest tests/ -v --timeout=120   # full suite (requires running stack)
```

Pytest markers: `smoke`, `regression`, `mega_tools`, `unit`, `integration`, `e2e`.

### Dart Tests

```bash
dart test test/dart/   # database, profiles, sanitizer, helpers, image handler
```

82 unit tests covering Dart backend components.

---

## Known Limitations

| Issue | Status |
|-------|--------|
| Arc A380 tool router requires LM Studio running on `:1235` | Falls back to rule-based routing when unavailable |
| Image download endpoint is not user-scoped | Guessable filenames can expose other users' files |
| Dart backend can be accessed without the Flask auth proxy | `X-Auth-User` header routes fall back to global access |
| Request/SSE buffer caps not fully enforced | Large bodies, attachments, and long SSE lines lack explicit limits |
| Batch generation (`count > 1`) not production-ready | Local: uninitialized child job IDs; cloud: count ignored |
| SDXL Lightning img2img/inpaint skips Lightning UNet merge | — |
| ControlNet hardcodes SD1.5 weights against SDXL checkpoints | — |
| WhatsApp QR must be scanned manually at `:8097` | By design — no credential storage |
| Reasoning models (phi-4) fill `max_tokens` with thinking tokens | Use instruction models for tool-heavy tasks |
| Max 4 images per response | Configurable via `maxImagesPerResponse` in `router.dart` |

---

## CI/CD Governance

This project is governed by the [Haskell Orchestrator](https://github.com/Al-Sarraf-Tech/Haskell-Orchestrator) — a Haskell-based multi-agent CI/CD framework for pre-push validation, code quality enforcement, and release management across the Al-Sarraf-Tech organization.

---

## Disclaimer

All AI inference runs locally. No data is sent to external servers unless you configure external search engines (SearXNG is self-hosted by default) or external LM Studio endpoints. Use at your own risk.
