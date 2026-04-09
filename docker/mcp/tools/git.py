"""
Git MCP tool — full git and GitHub CLI proxy for repos under ~/git/.

Actions:
  status      — multi-repo overview (all ~/git/*/) or single repo git status
  log         — recent commits (default 10)
  diff        — show diff (optional ref comparison, capped at 8000 chars)
  ci          — CI run status via `gh run list`
  trigger_ci  — re-trigger workflow via `gh workflow run`
  prs         — list open PRs
  create_pr   — create PR (requires title + branch; optional base, body)
  merge       — merge PR by number
  push        — push branch to remote
  issues      — list open issues, or create issue if title is provided
  scorecard   — CI health across ALL repos

Security:
  Repo names are validated by _validate_repo(): rejects '..' and '/' characters,
  allows only word chars and hyphens.

Registered with the tool registry at import time via register().
"""
from __future__ import annotations

import re
from typing import Any

from tools import register  # type: ignore[import]
from tools._ssh import SSHExecutor, SSHResult  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Expanded by the remote shell — not this process.
GIT_BASE = "$HOME/git"

# Maximum characters returned from diff output.
_DIFF_MAX_CHARS = 8000

# Allowed repo name pattern: word chars and hyphens only.
_REPO_RE = re.compile(r"^[\w\-]+$")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "name": "git",
    "description": (
        "Full git and GitHub CLI proxy for repos under ~/git/.\n"
        "Actions:\n"
        "  status     — multi-repo overview or single-repo git status\n"
        "  log        — recent commits (default 10)\n"
        "  diff       — show diff (optional ref comparison)\n"
        "  ci         — CI run status via gh run list\n"
        "  trigger_ci — re-trigger workflow via gh workflow run\n"
        "  prs        — list open PRs\n"
        "  create_pr  — create PR (title + branch required)\n"
        "  merge      — merge PR by number\n"
        "  push       — push branch to remote\n"
        "  issues     — list open issues or create issue if title given\n"
        "  scorecard  — CI health across ALL repos"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "log",
                    "diff",
                    "ci",
                    "trigger_ci",
                    "prs",
                    "create_pr",
                    "merge",
                    "push",
                    "issues",
                    "scorecard",
                ],
                "description": "Action to perform.",
            },
            "repo": {
                "type": "string",
                "description": "Repo name under ~/git/ (e.g. 'aichat'). Must match [\\w\\-]+.",
            },
            "branch": {
                "type": "string",
                "description": "Branch name for push/create_pr.",
            },
            "pr_number": {
                "type": "integer",
                "description": "Pull request number for merge.",
            },
            "title": {
                "type": "string",
                "description": "PR title (create_pr) or issue title (issues create).",
            },
            "body": {
                "type": "string",
                "description": "Body text for create_pr or issue creation.",
            },
            "base": {
                "type": "string",
                "description": "Base branch for create_pr (default: main).",
            },
            "limit": {
                "type": "integer",
                "description": "Number of entries to return (log, ci, prs, issues).",
            },
            "workflow": {
                "type": "string",
                "description": "Workflow filename for trigger_ci (e.g. 'ci.yml').",
            },
            "ref": {
                "type": "string",
                "description": "Git ref for diff comparison (e.g. 'HEAD~1').",
            },
        },
        "required": ["action"],
    },
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _text(content: str) -> list[dict[str, Any]]:
    """Wrap *content* in an MCP text content block."""
    return [{"type": "text", "text": content}]


def _validate_repo(repo: str) -> str | None:
    """Validate a repo name and return an error string if invalid, else None.

    Rules:
      - Must not contain '..' (path traversal)
      - Must not contain '/' (absolute or relative path separator)
      - Must match [\\w\\-]+ (alphanumeric, underscore, hyphen only)
    """
    if ".." in repo:
        return f"git: invalid repo name {repo!r} — '..' not allowed"
    if "/" in repo:
        return f"git: invalid repo name {repo!r} — '/' not allowed"
    if not _REPO_RE.fullmatch(repo):
        return f"git: invalid repo name {repo!r} — only [\\w\\-] chars allowed"
    return None


def _shell_quote(value: str) -> str:
    """Single-quote *value* for safe shell interpolation.

    Escapes embedded single quotes using the '"'"' technique so the result
    is safe to pass directly in a shell command string.
    """
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _status(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")

    if repo:
        err = _validate_repo(repo)
        if err:
            return _text(err)
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"echo '=== {repo} ===' && git status --short --branch"
        )
    else:
        # Multi-repo overview: iterate all top-level dirs under ~/git/
        cmd = (
            "for d in $HOME/git/*/; do "
            "  name=$(basename \"$d\"); "
            "  if [ -d \"$d/.git\" ]; then "
            "    echo \"=== $name ===\"; "
            "    git -C \"$d\" status --short --branch 2>&1; "
            "    echo; "
            "  fi; "
            "done"
        )

    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0 and result.stderr:
        return _text(f"git status error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or "(no output)")


async def _log(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git log: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    limit: int = int(args.get("limit", 10))
    cmd = (
        f"cd {GIT_BASE}/{_shell_quote(repo)} && "
        f"git log --oneline -n {limit}"
    )
    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"git log error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or "(no commits)")


async def _diff(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git diff: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    ref: str | None = args.get("ref")
    if ref:
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"git diff {_shell_quote(ref)}"
        )
    else:
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"git diff"
        )

    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"git diff error:\n{result.stderr.strip()}")

    output = result.stdout
    if len(output) > _DIFF_MAX_CHARS:
        output = output[:_DIFF_MAX_CHARS] + f"\n... (truncated at {_DIFF_MAX_CHARS} chars)"
    return _text(output.strip() or "(no diff)")


async def _ci(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git ci: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    limit: int = int(args.get("limit", 10))
    cmd = (
        f"cd {GIT_BASE}/{_shell_quote(repo)} && "
        f"gh run list --limit {limit}"
    )
    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"gh run list error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or "(no runs)")


async def _trigger_ci(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git trigger_ci: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    workflow: str | None = args.get("workflow")
    if not workflow:
        return _text("git trigger_ci: 'workflow' is required")

    ref: str = args.get("ref", "main")
    cmd = (
        f"cd {GIT_BASE}/{_shell_quote(repo)} && "
        f"gh workflow run {_shell_quote(workflow)} --ref {_shell_quote(ref)}"
    )
    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"gh workflow run error:\n{result.stderr.strip()}")
    return _text(
        result.stdout.strip()
        or f"Triggered workflow {workflow!r} on ref {ref!r} in repo {repo!r}"
    )


async def _prs(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    limit: int = int(args.get("limit", 10))

    if repo:
        err = _validate_repo(repo)
        if err:
            return _text(err)
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"gh pr list --limit {limit}"
        )
    else:
        # List PRs across all repos
        cmd = (
            f"for d in $HOME/git/*/; do "
            f"  name=$(basename \"$d\"); "
            f"  if [ -d \"$d/.git\" ]; then "
            f"    prs=$(gh -R . pr list --limit {limit} 2>/dev/null); "
            f"    if [ -n \"$prs\" ]; then "
            f"      echo \"=== $name ===\"; echo \"$prs\"; echo; "
            f"    fi; "
            f"  fi; "
            f"done"
        )

    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"gh pr list error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or "(no open PRs)")


async def _create_pr(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git create_pr: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    title: str | None = args.get("title")
    if not title:
        return _text("git create_pr: 'title' is required")

    branch: str | None = args.get("branch")
    if not branch:
        return _text("git create_pr: 'branch' is required")

    base: str = args.get("base", "main")
    body: str = args.get("body", "")

    cmd_parts = [
        f"cd {GIT_BASE}/{_shell_quote(repo)}",
        f"gh pr create --title {_shell_quote(title)} --head {_shell_quote(branch)} --base {_shell_quote(base)}",
    ]
    if body:
        cmd_parts[-1] += f" --body {_shell_quote(body)}"
    else:
        cmd_parts[-1] += " --body ''"

    cmd = " && ".join(cmd_parts)
    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"gh pr create error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or "Pull request created")


async def _merge(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git merge: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    pr_number: int | None = args.get("pr_number")
    if pr_number is None:
        return _text("git merge: 'pr_number' is required")

    cmd = (
        f"cd {GIT_BASE}/{_shell_quote(repo)} && "
        f"gh pr merge {int(pr_number)} --merge"
    )
    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"gh pr merge error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or f"PR #{pr_number} merged")


async def _push(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git push: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    branch: str | None = args.get("branch")
    if branch:
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"git push origin {_shell_quote(branch)}"
        )
    else:
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"git push"
        )

    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"git push error:\n{result.stderr.strip()}")

    out = result.stdout.strip() or result.stderr.strip()
    return _text(out or f"Pushed {('branch ' + repr(branch)) if branch else 'current branch'} in repo {repo!r}")


async def _issues(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    repo: str | None = args.get("repo")
    if not repo:
        return _text("git issues: 'repo' is required")

    err = _validate_repo(repo)
    if err:
        return _text(err)

    title: str | None = args.get("title")
    if title:
        # Create issue
        body: str = args.get("body", "")
        cmd_parts = [
            f"cd {GIT_BASE}/{_shell_quote(repo)}",
            f"gh issue create --title {_shell_quote(title)}",
        ]
        if body:
            cmd_parts[-1] += f" --body {_shell_quote(body)}"
        else:
            cmd_parts[-1] += " --body ''"
        cmd = " && ".join(cmd_parts)
    else:
        # List issues
        limit: int = int(args.get("limit", 20))
        cmd = (
            f"cd {GIT_BASE}/{_shell_quote(repo)} && "
            f"gh issue list --limit {limit}"
        )

    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0:
        return _text(f"gh issue error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or ("Issue created" if title else "(no open issues)"))


async def _scorecard(args: dict[str, Any], ssh: SSHExecutor) -> list[dict[str, Any]]:
    """Aggregate CI health across all repos under ~/git/."""
    cmd = (
        "for d in $HOME/git/*/; do "
        "  name=$(basename \"$d\"); "
        "  if [ -d \"$d/.git\" ]; then "
        "    run=$(gh -C \"$d\" run list --limit 1 --json status,conclusion "
        "          2>/dev/null | python3 -c \""
        "import sys,json; "
        "runs=json.load(sys.stdin); "
        "r=runs[0] if runs else {}; "
        "print(r.get('status','?'), r.get('conclusion','?'))"
        "\" 2>/dev/null || echo 'unknown unknown'); "
        "    echo \"$name: $run\"; "
        "  fi; "
        "done"
    )
    result: SSHResult = await ssh.run("amarillo", cmd)
    if result.returncode != 0 and not result.stdout.strip():
        return _text(f"git scorecard error:\n{result.stderr.strip()}")
    return _text(result.stdout.strip() or "(no repos found)")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def handle(
    args: dict[str, Any],
    ssh: SSHExecutor | None = None,
) -> list[dict[str, Any]]:
    """Dispatch to the appropriate git action handler.

    Args:
        args: MCP tool input arguments.
        ssh:  SSHExecutor instance; if None a default one is created.
              Pass a mock in tests.

    Returns:
        list of MCP content blocks (always at least one text block).
    """
    if ssh is None:
        ssh = SSHExecutor()

    action: str | None = args.get("action")
    if action is None:
        return _text("git: 'action' is required")

    if action == "status":
        return await _status(args, ssh)
    if action == "log":
        return await _log(args, ssh)
    if action == "diff":
        return await _diff(args, ssh)
    if action == "ci":
        return await _ci(args, ssh)
    if action == "trigger_ci":
        return await _trigger_ci(args, ssh)
    if action == "prs":
        return await _prs(args, ssh)
    if action == "create_pr":
        return await _create_pr(args, ssh)
    if action == "merge":
        return await _merge(args, ssh)
    if action == "push":
        return await _push(args, ssh)
    if action == "issues":
        return await _issues(args, ssh)
    if action == "scorecard":
        return await _scorecard(args, ssh)

    return _text(f"git: unknown action '{action}'")


# ---------------------------------------------------------------------------
# Register with the tool registry
# ---------------------------------------------------------------------------

register(SCHEMA, handle)
