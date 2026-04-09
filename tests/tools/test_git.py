"""
Unit tests for tools/git.py — Git/GitHub CLI proxy MCP tool.

Test groups:
  - status: single repo (1), all repos (1)
  - log: returns commits (1), missing repo (1)
  - diff: returns changes (1)
  - ci: returns runs (1)
  - scorecard: aggregates (1)
  - create_pr: success (1), missing title (1)
  - merge: success (1)
  - push: success (1)
  - trigger_ci: success (1)
  - issues: list (1), create (1)
  - repo validation: path traversal (1), absolute path (1)
  - unknown action (1)

Run with:
  cd ~/git/aichat
  python -m pytest tests/tools/test_git.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tools._ssh import SSHResult  # type: ignore[import]


# ===========================================================================
# status — 2 tests
# ===========================================================================

class TestStatusAction:
    """handle() with action='status'."""

    @pytest.mark.asyncio
    async def test_status_single_repo(self, mock_ssh):
        """status with a repo name must run git status in that repo."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="On branch main\nnothing to commit, working tree clean\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle({"action": "status", "repo": "aichat"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "aichat" in result[0]["text"] or "main" in result[0]["text"] or "clean" in result[0]["text"]
        mock_ssh.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_status_all_repos(self, mock_ssh):
        """status without repo must iterate all repos and return an overview."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout=(
                "aichat:\nOn branch main\nnothing to commit\n\n"
                "tmux-picker:\nOn branch feat/v2\n1 file changed\n"
            ),
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.3,
        )
        result = await handle({"action": "status"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert len(result[0]["text"]) > 0
        mock_ssh.run.assert_called_once()


# ===========================================================================
# log — 2 tests
# ===========================================================================

class TestLogAction:
    """handle() with action='log'."""

    @pytest.mark.asyncio
    async def test_log_returns_commits(self, mock_ssh):
        """log must return commit hash and message in output."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="abc1234 feat: add SSH tool\ndef5678 fix: thermal limit\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle({"action": "log", "repo": "aichat"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "abc1234" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_log_missing_repo_returns_error(self, mock_ssh):
        """log without 'repo' must return an error without calling ssh.run."""
        from tools.git import handle  # type: ignore[import]

        result = await handle({"action": "log"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "repo" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# diff — 1 test
# ===========================================================================

class TestDiffAction:
    """handle() with action='diff'."""

    @pytest.mark.asyncio
    async def test_diff_returns_changes(self, mock_ssh):
        """diff must include diff output in the text result."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="diff --git a/foo.py b/foo.py\n+++ b/foo.py\n+new line\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle({"action": "diff", "repo": "aichat"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "diff" in result[0]["text"] or "foo.py" in result[0]["text"]


# ===========================================================================
# ci — 1 test
# ===========================================================================

class TestCiAction:
    """handle() with action='ci'."""

    @pytest.mark.asyncio
    async def test_ci_returns_runs(self, mock_ssh):
        """ci must return workflow run information."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout=(
                "completed  success  CI  push  2024-01-10\n"
                "completed  failure  CI  push  2024-01-09\n"
            ),
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle({"action": "ci", "repo": "aichat"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "success" in result[0]["text"] or "failure" in result[0]["text"] or "CI" in result[0]["text"]


# ===========================================================================
# scorecard — 1 test
# ===========================================================================

class TestScorecardAction:
    """handle() with action='scorecard'."""

    @pytest.mark.asyncio
    async def test_scorecard_aggregates(self, mock_ssh):
        """scorecard must aggregate CI health across repos."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout=(
                "aichat: completed success\n"
                "tmux-picker: completed failure\n"
            ),
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.5,
        )
        result = await handle({"action": "scorecard"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert len(result[0]["text"]) > 0
        mock_ssh.run.assert_called_once()


# ===========================================================================
# create_pr — 2 tests
# ===========================================================================

class TestCreatePrAction:
    """handle() with action='create_pr'."""

    @pytest.mark.asyncio
    async def test_create_pr_success(self, mock_ssh):
        """create_pr with all required args must return a PR URL or confirmation."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="https://github.com/Al-Sarraf-Tech/aichat/pull/42\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.3,
        )
        result = await handle(
            {
                "action": "create_pr",
                "repo": "aichat",
                "title": "feat: add git tool",
                "branch": "feat/mcp-tools-expansion",
                "base": "main",
            },
            mock_ssh,
        )

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "42" in result[0]["text"] or "github.com" in result[0]["text"] or "pull" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_create_pr_missing_title_returns_error(self, mock_ssh):
        """create_pr without 'title' must return an error without calling ssh.run."""
        from tools.git import handle  # type: ignore[import]

        result = await handle(
            {
                "action": "create_pr",
                "repo": "aichat",
                "branch": "feat/mcp-tools-expansion",
            },
            mock_ssh,
        )

        assert result[0]["type"] == "text"
        assert "title" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# merge — 1 test
# ===========================================================================

class TestMergeAction:
    """handle() with action='merge'."""

    @pytest.mark.asyncio
    async def test_merge_success(self, mock_ssh):
        """merge with pr_number must return success confirmation."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="Merging pull request #42 (feat: add git tool)\ninto main\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.4,
        )
        result = await handle(
            {"action": "merge", "repo": "aichat", "pr_number": 42},
            mock_ssh,
        )

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "42" in result[0]["text"] or "merge" in result[0]["text"].lower()


# ===========================================================================
# push — 1 test
# ===========================================================================

class TestPushAction:
    """handle() with action='push'."""

    @pytest.mark.asyncio
    async def test_push_success(self, mock_ssh):
        """push must return push confirmation."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="Branch 'feat/git-tool' set up to track remote branch.\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle(
            {"action": "push", "repo": "aichat", "branch": "feat/git-tool"},
            mock_ssh,
        )

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "feat/git-tool" in result[0]["text"] or "push" in result[0]["text"].lower() or "track" in result[0]["text"].lower()


# ===========================================================================
# trigger_ci — 1 test
# ===========================================================================

class TestTriggerCiAction:
    """handle() with action='trigger_ci'."""

    @pytest.mark.asyncio
    async def test_trigger_ci_success(self, mock_ssh):
        """trigger_ci with workflow name must dispatch workflow run."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle(
            {"action": "trigger_ci", "repo": "aichat", "workflow": "ci.yml"},
            mock_ssh,
        )

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "ci.yml" in result[0]["text"] or "trigger" in result[0]["text"].lower() or "dispatch" in result[0]["text"].lower() or "success" in result[0]["text"].lower()


# ===========================================================================
# issues — 2 tests
# ===========================================================================

class TestIssuesAction:
    """handle() with action='issues'."""

    @pytest.mark.asyncio
    async def test_issues_list(self, mock_ssh):
        """issues without title must list open issues."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="#1\tadd monitoring\topen\n#2\tfix SSH timeout\topen\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.1,
        )
        result = await handle({"action": "issues", "repo": "aichat"}, mock_ssh)

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "#1" in result[0]["text"] or "monitoring" in result[0]["text"] or "open" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_issues_create(self, mock_ssh):
        """issues with title must create a new issue and return its URL."""
        from tools.git import handle  # type: ignore[import]

        mock_ssh.run.return_value = SSHResult(
            stdout="https://github.com/Al-Sarraf-Tech/aichat/issues/3\n",
            stderr="",
            returncode=0,
            host="amarillo",
            elapsed=0.2,
        )
        result = await handle(
            {"action": "issues", "repo": "aichat", "title": "feat: request git tool"},
            mock_ssh,
        )

        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "issues/3" in result[0]["text"] or "github.com" in result[0]["text"] or "3" in result[0]["text"]


# ===========================================================================
# repo validation — 2 tests
# ===========================================================================

class TestRepoValidation:
    """Repo name validation in handle()."""

    @pytest.mark.asyncio
    async def test_repo_path_traversal_rejected(self, mock_ssh):
        """A repo name containing '..' must be rejected without running SSH."""
        from tools.git import handle  # type: ignore[import]

        result = await handle({"action": "log", "repo": "../etc"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "invalid" in result[0]["text"].lower() or "repo" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_repo_absolute_path_rejected(self, mock_ssh):
        """A repo name starting with '/' must be rejected without running SSH."""
        from tools.git import handle  # type: ignore[import]

        result = await handle({"action": "log", "repo": "/etc/passwd"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "invalid" in result[0]["text"].lower() or "repo" in result[0]["text"].lower()
        mock_ssh.run.assert_not_called()


# ===========================================================================
# unknown action — 1 test
# ===========================================================================

class TestUnknownAction:
    """handle() with an unrecognised action."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, mock_ssh):
        """An unrecognised action must return a descriptive error."""
        from tools.git import handle  # type: ignore[import]

        result = await handle({"action": "teleport"}, mock_ssh)

        assert result[0]["type"] == "text"
        assert "teleport" in result[0]["text"] or "unknown" in result[0]["text"].lower()
