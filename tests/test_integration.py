"""Runs the real stack: auto-started backend, the Pulpie model, live web pages.

Skipped unless PULPIE_INTEGRATION=1, since it needs network access and the
model download on first run.
"""

import os
import socket

import httpx
import pytest
from mcp import Client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("PULPIE_INTEGRATION") != "1", reason="set PULPIE_INTEGRATION=1"),
]

PAGE = "https://docs.python.org/3/library/asyncio-queue.html"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("PULPIE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PULPIE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("PULPIE_IDLE_TIMEOUT", "60")
    yield tmp_path
    try:
        httpx.post(f"http://127.0.0.1:{port}/shutdown", timeout=5)
    except httpx.HTTPError:
        pass


@pytest.mark.anyio
async def test_tools_end_to_end(isolated):
    from pulpie_mcp.server import mcp

    project = isolated / "project" / "DOCS" / "reference"
    async with Client(mcp) as client:
        fetched = await client.call_tool("fetch_markdown", {"url": PAGE, "max_chars": 2000})
        assert not fetched.is_error, fetched.content[0].text
        text = fetched.content[0].text
        assert text.startswith("# ") and "asyncio" in text and "Truncated" in text

        saved = await client.call_tool("save_markdown", {"url": PAGE, "directory": str(project)})
        assert not saved.is_error, saved.content[0].text
        path = project / "3-library-asyncio-queue.md"
        assert path.exists() and "source: \"" + PAGE + "\"" in path.read_text()

        blocked = await client.call_tool("fetch_markdown", {"url": "http://127.0.0.1:9/"})
        assert blocked.is_error and "PULPIE_ALLOW_PRIVATE" in blocked.content[0].text

        listed = await client.call_tool("list_library", {"directory": str(project)})
        assert str(path) in listed.content[0].text
