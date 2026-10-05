"""Crawl a documentation site into a folder of Markdown files.

Page discovery tries the sitemap first (robots.txt, then /sitemap.xml) and
falls back to following links from the raw HTML. Only pages on the same host
and under the path prefix are crawled.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import httpx

from pulpie_mcp import client, config, storage
from pulpie_mcp.netguard import BlockedAddressError, make_client

CONCURRENCY = 4
MAX_SITEMAPS = 10
SKIP_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".zip", ".gz",
    ".tar", ".tgz", ".mp4", ".mp3", ".webm", ".css", ".js", ".json", ".xml", ".woff",
    ".woff2", ".ttf", ".rss", ".atom",
}

Progress = Callable[[int, int, str], Awaitable[None]]


@dataclass
class CrawlResult:
    directory: Path
    index_path: Path | None
    saved: list[storage.SavedDoc] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    used_sitemap: bool = False


def normalize(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def default_prefix(url: str) -> str:
    path = urlparse(url).path or "/"
    if path.endswith("/"):
        return path
    if PurePosixPath(path).suffix:
        return path.rsplit("/", 1)[0] + "/"
    return path + "/"


def in_scope(url: str, host: str, prefix: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() != host:
        return False
    if PurePosixPath(parsed.path).suffix.lower() in SKIP_EXTENSIONS:
        return False
    path = parsed.path or "/"
    return path.startswith(prefix) or path + "/" == prefix


def parse_sitemap(xml_bytes: bytes) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls) from a sitemap or sitemap index."""
    root = ET.fromstring(xml_bytes)
    locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
    if root.tag.endswith("sitemapindex"):
        return [], locs
    return locs, []


async def sitemap_urls(start_url: str, prefix: str = "/") -> list[str]:
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []
    pages: list[str] = []
    async with make_client(allow_private=config.allow_private(), timeout=15) as http:
        try:
            robots = await http.get(f"{origin}/robots.txt")
            if robots.status_code == 200:
                for line in robots.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        candidates.append(urljoin(origin, line.split(":", 1)[1].strip()))
        except (httpx.HTTPError, BlockedAddressError):
            pass
        if prefix != "/":
            candidates.append(f"{origin}{prefix}sitemap.xml")  # MkDocs and friends
        candidates.append(f"{origin}/sitemap.xml")

        visited: set[str] = set()
        while candidates and len(visited) < MAX_SITEMAPS:
            sitemap = candidates.pop(0)
            if sitemap in visited:
                continue
            visited.add(sitemap)
            try:
                resp = await http.get(sitemap)
                if resp.status_code != 200:
                    continue
                found, children = parse_sitemap(resp.content)
            except (httpx.HTTPError, BlockedAddressError, ET.ParseError):
                continue
            pages.extend(found)
            candidates.extend(children)
    return pages


async def crawl(
    start_url: str,
    directory: Path,
    *,
    max_pages: int,
    path_prefix: str | None = None,
    progress: Progress | None = None,
) -> CrawlResult:
    start = normalize(start_url)
    host = (urlparse(start).hostname or "").lower()
    prefix = path_prefix or default_prefix(start_url)
    if not prefix.startswith("/"):
        prefix = "/" + prefix

    result = CrawlResult(directory=directory, index_path=None)
    seen: set[str] = set()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def enqueue(url: str) -> None:
        url = normalize(url)
        if url in seen or len(seen) >= max_pages or not in_scope(url, host, prefix):
            return
        seen.add(url)
        queue.put_nowait(url)

    seen.add(start)
    queue.put_nowait(start)
    sitemap = [u for u in await sitemap_urls(start, prefix) if in_scope(normalize(u), host, prefix)]
    result.used_sitemap = bool(sitemap)
    for url in sitemap:
        enqueue(url)

    saved_sources: set[str] = set()
    done = 0

    async def worker() -> None:
        nonlocal done
        while True:
            url = await queue.get()
            try:
                page = await client.extract(url, include_links=not result.used_sitemap)
                final = normalize(page.url)
                if final not in saved_sources and page.markdown.strip():
                    saved_sources.add(final)
                    title = page.title or storage.title_from(page.markdown, final)
                    path = storage.target_path(directory, page.url)
                    result.saved.append(
                        storage.write_doc(path, page.markdown, title=title, source=page.url)
                    )
                for link in page.links:
                    enqueue(link)
            except Exception as err:  # noqa: BLE001 - one bad page must not stall the queue
                result.failed.append((url, str(err) or type(err).__name__))
            finally:
                done += 1
                if progress:
                    await progress(done, len(seen), url)
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]
    try:
        await queue.join()
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    if result.saved:
        result.index_path = write_index(directory, start_url, result.saved)
    return result


def write_index(directory: Path, start_url: str, docs: list[storage.SavedDoc]) -> Path:
    path = directory / storage.INDEX_NAME
    if path.exists() and storage.read_frontmatter(path) is None:
        path = directory / "pulpie-index.md"  # never overwrite someone else's index.md
    ordered = sorted(docs, key=lambda d: d.source)
    lines = [f"# {storage.site_folder(start_url)} docs", "", f"{len(ordered)} pages crawled from {start_url}", ""]
    for doc in ordered:
        rel = doc.path.relative_to(directory).as_posix()
        lines.append(f"- [{doc.title}]({rel}): {doc.source}")
    title = f"Index of {storage.site_folder(start_url)}"
    path.write_text(storage.render("\n".join(lines), title=title, source=start_url), encoding="utf-8")
    return path
