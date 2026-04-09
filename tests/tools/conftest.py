"""
Shared fixtures for tools/ unit tests.

SSHResult is imported from tools._ssh when available. A local fallback
dataclass is provided so the fixture module can be imported even before
_ssh.py exists (e.g. during the "red" TDD phase).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# SSHResult — import from implementation; fall back to local stub while TDD
# ---------------------------------------------------------------------------
try:
    from tools._ssh import SSHResult  # type: ignore[import]
except ImportError:
    @dataclass
    class SSHResult:  # type: ignore[no-redef]
        """Stub used when tools._ssh is not yet implemented."""
        stdout: str = ""
        stderr: str = ""
        returncode: int = 0
        host: str = ""
        elapsed: float = 0.0


# ---------------------------------------------------------------------------
# mock_ssh — pre-configured AsyncMock of SSHExecutor
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_ssh() -> AsyncMock:
    """Return an AsyncMock that quacks like SSHExecutor.

    Pre-configured return values cover the happy-path for most tests.
    Individual tests can override specific attrs or return_value as needed.
    """
    executor = AsyncMock()

    # Default successful result
    _default_result = SSHResult(
        stdout="ok",
        stderr="",
        returncode=0,
        host="amarillo",
        elapsed=0.05,
    )

    executor.run = AsyncMock(return_value=_default_result)
    executor.run_multi = AsyncMock(return_value={"amarillo": _default_result})
    executor.is_host_allowed = MagicMock(return_value=True)

    return executor


# ---------------------------------------------------------------------------
# mock_httpx — AsyncMock httpx.AsyncClient + response for HTTP-based tools
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_httpx() -> AsyncMock:
    """Return an AsyncMock mimicking httpx.AsyncClient.

    response.json.return_value and response.text can be overridden per test.
    The client is also pre-wired as an async context manager.
    """
    response = MagicMock()
    response.status_code = 200
    response.text = "OK"
    response.json = MagicMock(return_value={})
    response.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.put = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)

    # Support `async with httpx.AsyncClient() as client:` usage
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return client
