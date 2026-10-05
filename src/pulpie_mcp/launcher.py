"""Finds the backend, starting it in the background when it is not running.

Several MCP sessions may race to start it. That is fine: the backend binds
its port before loading the model, so extra copies exit immediately and
every session ends up talking to the one that won.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import Any

import httpx

from pulpie_mcp import config


class BackendError(Exception):
    """The backend could not be reached or started."""


async def health(timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{config.backend_url()}/health")
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def spawn_backend() -> subprocess.Popen:
    host, port = config.backend_host_port()
    log = config.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True
    with open(log, "ab") as log_file:
        return subprocess.Popen(
            [sys.executable, "-m", "pulpie_mcp", "serve", "--host", host, "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **kwargs,
        )


def _log_tail(lines: int = 15) -> str:
    try:
        text = config.log_path().read_text(errors="replace").splitlines()
        return "\n".join(text[-lines:])
    except OSError:
        return "(no backend log)"


async def ensure_backend() -> dict[str, Any]:
    """Return backend health once the model is ready, starting it if needed."""
    info = await health()
    if info is None:
        if not config.is_local_backend():
            raise BackendError(
                f"No pulpie backend at {config.backend_url()}. Remote backends are not "
                "auto-started; start it there with `pulpie-mcp serve`."
            )
        proc = spawn_backend()
    else:
        proc = None

    deadline = time.monotonic() + config.start_timeout()
    exited_at: float | None = None
    while time.monotonic() < deadline:
        info = info or await health()
        if info:
            if info.get("error"):
                raise BackendError(f"Pulpie model failed to load: {info['error']}")
            if info.get("ready"):
                return info
        if proc is not None and proc.poll() is not None:
            # Lost the start race, or crashed. Give a winner a moment to answer.
            exited_at = exited_at or time.monotonic()
            if time.monotonic() - exited_at > 20 and info is None:
                raise BackendError(
                    f"The pulpie backend exited (code {proc.returncode}). "
                    f"Last log lines from {config.log_path()}:\n{_log_tail()}"
                )
        info = None
        await asyncio.sleep(0.5)

    raise BackendError(
        f"The pulpie backend did not become ready within {config.start_timeout()}s. "
        f"Check {config.log_path()}."
    )


async def stop_backend() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{config.backend_url()}/shutdown")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
