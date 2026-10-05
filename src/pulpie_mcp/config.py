"""Settings read from environment variables, with sensible defaults.

Values are read at call time so a test or a spawned backend always sees
the current environment.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_URL = "http://127.0.0.1:8787"
DEFAULT_IDLE_TIMEOUT = 1800  # seconds
DEFAULT_START_TIMEOUT = 600  # seconds, first run downloads the model

_TRUTHY = {"1", "true", "yes", "on"}


def app_version() -> str:
    try:
        return version("pulpie-mcp")
    except PackageNotFoundError:
        return "0.0.0"


def backend_url() -> str:
    return os.environ.get("PULPIE_URL", DEFAULT_URL).rstrip("/")


def backend_host_port() -> tuple[str, int]:
    parsed = urlparse(backend_url())
    return parsed.hostname or "127.0.0.1", parsed.port or 8787


def is_local_backend() -> bool:
    host, _ = backend_host_port()
    return host in {"127.0.0.1", "localhost", "::1"}


def home_dir() -> Path:
    return Path(os.environ.get("PULPIE_HOME") or Path.home() / ".pulpie").expanduser()


def library_dir() -> Path:
    custom = os.environ.get("PULPIE_LIBRARY")
    return Path(custom).expanduser() if custom else home_dir() / "library"


def search_db_path() -> Path:
    return home_dir() / "search.db"


def log_path() -> Path:
    return home_dir() / "backend.log"


def allow_private() -> bool:
    return os.environ.get("PULPIE_ALLOW_PRIVATE", "").strip().lower() in _TRUTHY


def idle_timeout() -> int:
    """Seconds without requests before the backend exits. 0 disables it."""
    return _int_env("PULPIE_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT)


def start_timeout() -> int:
    return _int_env("PULPIE_START_TIMEOUT", DEFAULT_START_TIMEOUT)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(0, int(raw)) if raw else default
    except ValueError:
        return default
