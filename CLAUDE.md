# CLAUDE.md — aichat

Docker-based AI assistant platform. 16-container Docker Compose stack with Dart/Shelf backend, vanilla JS frontend, Python MCP tool server, and multiple microservices.

## Quick Reference
```bash
cd ~/git/aichat && docker compose -f docker-compose.yml -f docker-compose.ports.yml up -d
docker compose logs -f <service>    # tail a specific service
docker compose down                 # stop all
```

## Architecture

```
Frontend (vanilla JS) → aichat-auth (JWT proxy) → aichat-web (Dart/Shelf)
                                                      ↓
                                                   aichat-mcp (Python FastAPI, 25 MCP tools)
                                                      ↓
                    ┌──────────────┬──────────────┬──────────────┐
                aichat-data    aichat-vision  aichat-docs    aichat-sandbox
                (Postgres/     (Video/OCR/    (PDF/ingest)   (Code exec)
                 memory/graph/  CLIP/detect)
                 planner/jobs)
```

Chat routing: frontend → Dart router → MCP `chat` tool (SSE) → agents.py → SSH CLI (Claude/Codex/Gemini) or LM Studio HTTP (Qwen/local)

## Service Ports (current)
| Service | Port | Purpose |
|---|---|---|
| aichat-db | 5432 | PostgreSQL 16 |
| aichat-data | 8091 | Consolidated: postgres REST + memory + graph + planner + jobs + research |
| aichat-mcp | 8096 | MCP HTTP/SSE server (25 tools, 44 modular + inline) |
| aichat-vision | 8099 | Video + OCR + CLIP + object detection, Intel Arc GPU |
| aichat-docs | 8101 | Document ingest + PDF operations |
| aichat-sandbox | 8095 | Custom tool runner |
| aichat-searxng | 8080 | SearXNG meta-search (internal) |
| aichat-web | 8200 | Dart/Shelf web server (via auth proxy) |
| aichat-auth | 8200/8247 | JWT auth proxy + admin panel |
| aichat-whatsapp | 8097 | WhatsApp bot |
| aichat-vector | 6333 | Qdrant vector DB |
| aichat-redis | 6379 | Valkey — compaction cache |
| aichat-minio | 9001/9002 | S3-compatible object store |
| aichat-inference | 8105 | Intel Arc OpenVINO embeddings |
| aichat-browser | 8104 | Headless Chromium (Playwright) |
| aichat-jupyter | 8098 | Jupyter code execution |

DB host port is 5435 (not 5432) via docker-compose.ports.yml.

## Module Structure

### Dart Backend (lib/)
| File | Lines | Purpose |
|---|---|---|
| router.dart | ~960 | Slim orchestrator: routing table, CORS, auth guard, static files |
| image_handler.dart | ~1,200 | Image gen: ComfyUI workflows, HF fallback, async job system |
| model_handler.dart | ~250 | Model listing, warmup/validation, capability caching |
| sanitizer.dart | ~130 | Tool result cleaning, image URL extraction, arg inference |
| router_helpers.dart | ~90 | Shared HTTP helpers: JSON response, SSE events, CORS headers |
| model_profiles.dart | ~200 | Per-model optimization profiles (temperature, tools, reasoning) |
| personalities.dart | ~800 | 31 chat personalities with system prompts |

### MCP Tool Modules (docker/mcp/tools/)
| Module | Handlers | Purpose |
|---|---|---|
| _helpers.py | (shared) | Service URLs, text/json helpers, resolve_image_path |
| _imaging.py | (shared) | ImageRenderer singleton, PIL utilities |
| memory.py | 2 | Key-value store/recall |
| knowledge.py | 5 | Graph database (NetworkX/SQLite) |
| data.py | 6 | Article storage, cache, image registry, error log |
| media.py | 3 | Video info/frames/transcode |
| document.py | 9 | OCR, PDF read/edit/merge/split, doc ingestion |
| code.py | 3 | Python/JavaScript/Jupyter execution |
| planner.py | 10 | Task CRUD, job status/result/list |
| custom_tools.py | 6 | User-defined tools, researchbox proxy |
| ssh.py | 1 | Remote command execution |
| monitor.py | 1 | System monitoring |
| git.py | 1 | Git operations |
| + 4 more | 4 | notify, iot, log, telegram |

## Build

```bash
make build                      # build all service images
docker compose -f docker-compose.yml -f docker-compose.ports.yml up -d
```

## Test

```bash
dart test test/dart/                                                          # 82 unit tests (db+profiles+sanitizer+helpers+image)
python3 -m pytest tests/test_smoke.py -v --timeout=60                         # 19 service health
python3 -m pytest tests/test_full_regression.py -v --timeout=60               # 96 regression
python3 -m pytest tests/test_image_pipeline.py -v --timeout=90               # 184 image tools
python3 -m pytest tests/test_model_e2e.py -v --timeout=120                   # 70 model E2E (all LLMs)
python3 -m pytest tests/test_image_rendering_e2e.py -v --timeout=90          # 17 image rendering
python3 -m pytest tests/test_tool_priority.py -v --timeout=30                # 18 tool routing
python3 -m pytest tests/tools/ -v --timeout=60                               # 177 MCP tool unit tests
python3 -m pytest test/test_playwright_e2e.py -v --timeout=300               # 14 browser E2E
python3 -m pytest tests/test_dartboard_e2e.py -v --timeout=180               # 13 dartboard E2E
```

## Lint

```bash
make lint                       # ruff + mypy on docker/**/*.py
dart analyze lib/               # Dart static analysis
make security-checks            # shellcheck/bandit/safety/semgrep/trivy
```

## Python Standards
- Use type hints. Include argument validation and robust error handling.
- Avoid cleverness when simpler code is clearer.
- Use standard library unless external dependency is justified.
- Production-minded, observable, maintainable code.

## Port Policy
NEVER use 8080/8000/8888/3000/5000/9000 as host ports. NO host ports in base docker-compose.yml — ports live in docker-compose.ports.yml only.
