"""Headless HTTP backend that keeps one Pulpie model loaded for every client.

Endpoints: GET /health, POST /extract, POST /shutdown. The server exits on
its own after PULPIE_IDLE_TIMEOUT seconds without extract requests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from pulpie_mcp import config
from pulpie_mcp.backend.extract import MODEL_ID, ExtractError, ModelHolder, extract_url

logger = logging.getLogger("pulpie-mcp.backend")


class ExtractRequest(BaseModel):
    url: str
    allow_private: bool = False
    include_links: bool = False


class ExtractResponse(BaseModel):
    url: str
    title: str | None
    markdown: str
    links: list[str]
    passthrough: bool
    fetch_ms: float
    inference_ms: float


class Activity:
    def __init__(self) -> None:
        self.last = time.monotonic()
        self.inflight = 0


def create_app(idle_timeout: int) -> FastAPI:
    model = ModelHolder()
    activity = Activity()

    async def watch_idle(app: FastAPI) -> None:
        await asyncio.to_thread(model.wait_loaded)
        activity.last = time.monotonic()  # loading time is not idle time
        while True:
            await asyncio.sleep(min(15, max(1, idle_timeout)))
            idle_for = time.monotonic() - activity.last
            if activity.inflight == 0 and idle_for >= idle_timeout:
                logger.info("Idle for %ds, shutting down", int(idle_for))
                request_exit(app)
                return

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        threading.Thread(target=model.load, name="pulpie-load", daemon=True).start()
        watcher = asyncio.create_task(watch_idle(app)) if idle_timeout > 0 else None
        yield
        if watcher:
            watcher.cancel()

    app = FastAPI(title="pulpie-mcp backend", version=config.app_version(), lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "ready": model.ready,
            "error": model.error,
            "model": MODEL_ID,
            "device": model.device,
            "version": config.app_version(),
            "pid": os.getpid(),
            "idle_timeout": idle_timeout,
        }

    @app.post("/extract", response_model=ExtractResponse)
    async def extract(req: ExtractRequest):
        activity.inflight += 1
        try:
            result = await extract_url(
                model, req.url, allow_private=req.allow_private, include_links=req.include_links
            )
        except ExtractError as err:
            raise HTTPException(status_code=err.status, detail=str(err)) from err
        finally:
            activity.inflight -= 1
            activity.last = time.monotonic()
        return ExtractResponse(**result.__dict__)

    @app.post("/shutdown")
    async def shutdown():
        request_exit(app)
        return {"status": "stopping"}

    return app


def request_exit(app: FastAPI) -> None:
    server: uvicorn.Server | None = getattr(app.state, "server", None)
    if server is not None:
        server.should_exit = True


def bind_socket(host: str, port: int) -> socket.socket:
    """Bind before loading anything, so a second backend fails fast and cheap."""
    family, kind, proto, _, addr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    sock = socket.socket(family, kind, proto)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(addr)
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def serve(host: str, port: int) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    sock = bind_socket(host, port)
    app = create_app(config.idle_timeout())
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", access_log=False))
    app.state.server = server
    logger.info("pulpie-mcp backend listening on http://%s:%d (pid %d)", host, port, os.getpid())
    server.run(sockets=[sock])
