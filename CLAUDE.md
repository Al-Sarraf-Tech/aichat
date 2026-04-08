# CLAUDE.md — aichat

Docker-based AI assistant platform. Multi-container Docker Compose stack.

## Quick Reference
```bash
docker compose up -d                # start all services
docker compose logs -f <service>    # tail a specific service
docker compose down                 # stop all
```

## Service Ports
| Service | Port | Purpose |
|---|---|---|
| aichat-db | 5432 | PostgreSQL |
| aichat-database | 8091 | FastAPI REST over Postgres |
| aichat-researchbox | 8092 | RSS/Playwright feed discovery |
| aichat-memory | 8094 | Memory store |
| aichat-toolkit | 8095 | Custom tool runner |
| aichat-mcp | 8096 | MCP HTTP/SSE server |
| aichat-whatsapp | 8097 | WhatsApp bot (QR at :8097) |
| aichat-graph | 8098 | Knowledge graph (SQLite/NetworkX) |
| aichat-vector | 6333 | Qdrant vector DB |
| aichat-video | 8099 | FFmpeg video analysis |
| aichat-ocr | 8100 | Tesseract OCR |
| aichat-docs | 8101 | Document ingestor |
| aichat-planner | 8102 | Task planner |
| aichat-pdf | 8103 | PDF operations |

`aichat-toolkit` mounts `~/.config/aichat/tools` and `~/git` (read-only). `IMAGE_GEN_BASE_URL` points to LM Studio at `host.docker.internal:1234`.

**aiweb service:** Flask-based AI web assistant at `docker/aiweb/`. Build: `docker build -t aiweb .` Run: `docker run -p 5000:5000 aiweb`

## Build

```bash
make build                      # build all service images
docker compose up -d            # start full stack (detached)
```

## Test

```bash
make test                       # run full pytest test suite
make smoke                      # quick health-endpoint check
make dart-test                  # run Dart tests
```

## Lint

```bash
make lint                       # ruff + mypy on docker/**/*.py
make dart-analyze               # dart analyze
make security-checks            # shellcheck/bandit/safety/semgrep/trivy
```

## Python Standards
- Use type hints. Include argument validation and robust error handling.
- Avoid cleverness when simpler code is clearer.
- Use standard library unless external dependency is justified.
- Production-minded, observable, maintainable code.
