# Workspace

Per-user file storage for scripts, images, documents, and other assets.

## Structure

```
workspace/
  <username>/          ← auto-created on first access
    scripts/
    images/
    documents/
    ...
```

Each user gets an isolated subdirectory. Users cannot access other users' files.

## Access

Use the `workspace` MCP tool:

```
workspace(action="list", user="jalsarraf")
workspace(action="write", user="jalsarraf", path="scripts/deploy.sh", content="#!/bin/bash\n...")
workspace(action="read", user="jalsarraf", path="scripts/deploy.sh")
workspace(action="delete", user="jalsarraf", path="scripts/old.sh")
workspace(action="mkdir", user="jalsarraf", path="images/renders")
workspace(action="info", user="jalsarraf")
```

## Security

- Path traversal protection — all paths canonicalized and verified
- User isolation enforced at the MCP handler level
- Filenames sanitized (no `..`, no absolute paths, no null bytes)
- Mounted as a Docker volume at `/workspace` in the MCP container
