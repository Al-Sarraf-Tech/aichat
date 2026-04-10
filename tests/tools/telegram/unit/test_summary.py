"""Tests for telegram.summary — summary builder."""
from __future__ import annotations

from tools.telegram.models import TaskState
from tools.telegram.summary import build_summary


class TestBuildSummary:
    def test_full_summary(self):
        ts = TaskState(task_id="abc12345", description="fix logging", repo="aichat")
        ts.started_at = 0
        result = build_summary(
            task_state=ts,
            final_text="Fixed the logging issue in main.py",
            edited_files=["/home/user/git/aichat/main.py", "/home/user/git/aichat/utils.py"],
            last_bash_output="[main abc1234] fix: logging\n 2 files changed",
            returncode=0,
        )
        assert "Done" in result
        assert "main.py" in result
        assert "utils.py" in result
        assert "abc1234" in result

    def test_failed_summary(self):
        ts = TaskState(task_id="abc12345", description="break things", repo="aichat")
        result = build_summary(
            task_state=ts, final_text="error occurred",
            edited_files=[], last_bash_output="", returncode=1,
        )
        assert "Failed (exit 1)" in result

    def test_no_files_no_commit(self):
        ts = TaskState(task_id="abc12345", description="read-only check")
        result = build_summary(
            task_state=ts, final_text="All looks good",
            edited_files=[], last_bash_output="", returncode=0,
        )
        assert "Done" in result
        assert "Files:" not in result
        assert "Commit:" not in result
