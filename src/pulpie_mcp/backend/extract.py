"""Fetch a page and turn its main content into Markdown with Pulpie.

torch and pulpie are imported lazily inside the loader so the rest of the
package (the MCP client side) never pays for them.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin

import httpx
from bs4 import BeautifulSoup

from pulpie_mcp.netguard import BlockedAddressError, make_client

logger = logging.getLogger("pulpie-mcp.backend")

MODEL_NAME = "orange-small"
MODEL_ID = "feyninc/pulpie-orange-small"
MAX_BYTES = 15 * 1024 * 1024
TEXT_TYPES = ("text/markdown", "text/x-markdown", "text/plain")
HTML_TYPES = ("text/html", "application/xhtml+xml")


class ExtractError(Exception):
    """An expected failure with a message that is safe to show the agent."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class Extraction:
    url: str
    title: str | None
    markdown: str
    links: list[str] = field(default_factory=list)
    passthrough: bool = False
    fetch_ms: float = 0.0
    inference_ms: float = 0.0


class ModelHolder:
    """Loads the Pulpie extractor once and serializes inference."""

    def __init__(self) -> None:
        self._extractor: Any = None
        self._error: str | None = None
        self._loaded = threading.Event()
        self._lock = asyncio.Lock()
        self.device = "unknown"

    @property
    def ready(self) -> bool:
        return self._extractor is not None

    def wait_loaded(self) -> None:
        """Block until loading has finished, whether it worked or not."""
        self._loaded.wait()

    @property
    def error(self) -> str | None:
        return self._error

    def load(self) -> None:
        try:
            from pulpie import Extractor

            logger.info("Loading %s ...", MODEL_ID)
            self._extractor = Extractor(model=MODEL_NAME)
            self.device = str(self._extractor.device)
            logger.info("Model ready on %s", self.device)
        except Exception as exc:  # noqa: BLE001 - surfaced through /health
            logger.exception("Model failed to load")
            self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self._loaded.set()

    async def extract(self, html: str) -> str:
        await asyncio.to_thread(self._loaded.wait)
        if self._extractor is None:
            raise ExtractError(f"Pulpie model failed to load: {self._error}", status=503)
        async with self._lock:
            result = await asyncio.to_thread(self._extractor.extract, html)
        return result.markdown


def prepare_html(html: str, base_url: str) -> tuple[str, str | None, list[str]]:
    """Make image and link URLs absolute, and collect title and page links."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 - lxml can choke on odd markup
        soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else None

    for img in soup.find_all("img"):
        src = img.get("src")
        if src and not src.startswith(("data:", "blob:", "javascript:")):
            img["src"] = urljoin(base_url, src)
        data_src = img.get("data-src")
        if data_src and not data_src.startswith(("data:", "blob:")):
            img["data-src"] = urljoin(base_url, data_src)
            if not img.get("src"):
                img["src"] = img["data-src"]

    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        a["href"] = absolute
        clean = urldefrag(absolute)[0]
        if clean.startswith(("http://", "https://")) and clean not in seen:
            seen.add(clean)
            links.append(clean)

    return str(soup), title, links


async def fetch(url: str, *, allow_private: bool) -> tuple[str, str, str]:
    """Return (final_url, content_type, text) for a URL."""
    try:
        async with make_client(allow_private=allow_private) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ExtractError(f"Page is larger than {MAX_BYTES // (1024 * 1024)} MB.")
                    chunks.append(chunk)
                encoding = resp.encoding or "utf-8"
                text = b"".join(chunks).decode(encoding, errors="replace")
                return str(resp.url), content_type, text
    except BlockedAddressError as err:
        raise ExtractError(str(err), status=403) from err
    except httpx.HTTPStatusError as err:
        raise ExtractError(
            f"Fetching {url} failed: HTTP {err.response.status_code} {err.response.reason_phrase}"
        ) from err
    except httpx.RequestError as err:
        raise ExtractError(f"Network error fetching {url}: {err}") from err


async def extract_url(
    model: ModelHolder, url: str, *, allow_private: bool, include_links: bool
) -> Extraction:
    start = time.perf_counter()
    final_url, content_type, body = await fetch(url, allow_private=allow_private)
    fetch_ms = (time.perf_counter() - start) * 1000

    if content_type in TEXT_TYPES:
        # Already Markdown or plain text (llms.txt, raw .md files): keep as is.
        return Extraction(final_url, None, body.strip(), passthrough=True, fetch_ms=fetch_ms)

    if content_type and content_type not in HTML_TYPES:
        raise ExtractError(f"{final_url} returned {content_type}, not an HTML page.", status=415)

    html, title, links = prepare_html(body, final_url)
    infer_start = time.perf_counter()
    markdown = await model.extract(html)
    inference_ms = (time.perf_counter() - infer_start) * 1000

    return Extraction(
        url=final_url,
        title=title,
        markdown=markdown.strip(),
        links=links if include_links else [],
        fetch_ms=fetch_ms,
        inference_ms=inference_ms,
    )
