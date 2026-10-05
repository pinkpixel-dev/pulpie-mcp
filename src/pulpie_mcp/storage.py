"""Writing saved pages to disk and reading the library back.

Every saved page starts with a small frontmatter block (title, source,
fetched) so agents can tell where a file came from and how old it is.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from pulpie_mcp import config

INDEX_NAME = "index.md"
MAX_SLUG = 100
_STRIP_EXT = re.compile(r"\.(html?|php|aspx?|md|mdx|txt)$", re.IGNORECASE)
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class SavedDoc:
    path: Path
    title: str
    source: str
    fetched: str


def resolve_directory(directory: str | None, url: str) -> Path:
    """An explicit absolute directory, or the global library folder for the site."""
    if directory:
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise ValueError(f"directory must be an absolute path, got {directory!r}.")
        return path
    return config.library_dir() / site_folder(url)


def site_folder(url: str) -> str:
    host = (urlparse(url).hostname or "site").lower()
    return host.removeprefix("www.")


def slug_for_url(url: str) -> str:
    parsed = urlparse(url)
    path = _STRIP_EXT.sub("", unquote(parsed.path).strip("/"))
    raw = path or "index"
    if parsed.query:
        raw += "-" + hashlib.sha1(parsed.query.encode()).hexdigest()[:6]
    slug = _NON_SLUG.sub("-", raw.lower()).strip("-") or "index"
    if len(slug) > MAX_SLUG:
        digest = hashlib.sha1(slug.encode()).hexdigest()[:8]
        slug = f"{slug[: MAX_SLUG - 9].rstrip('-')}-{digest}"
    return slug


def clean_filename(filename: str) -> str:
    name = Path(filename).name
    stem = _NON_SLUG.sub("-", Path(name).stem.lower()).strip("-")
    if not stem:
        raise ValueError(f"Invalid filename {filename!r}.")
    return f"{stem}.md"


def target_path(directory: Path, url: str, filename: str | None = None) -> Path:
    """Pick a file path, avoiding clobbering a file saved from a different URL."""
    name = clean_filename(filename) if filename else f"{slug_for_url(url)}.md"
    if name == INDEX_NAME and not filename:
        name = "home.md"
    path = directory / name
    existing = read_frontmatter(path) if path.exists() else None
    if existing is not None and existing.get("source") not in (None, url):
        digest = hashlib.sha1(url.encode()).hexdigest()[:6]
        path = directory / f"{path.stem}-{digest}.md"
    return path


def render(markdown: str, *, title: str, source: str, fetched: str | None = None) -> str:
    fetched = fetched or now_iso()
    front = "\n".join(
        [
            "---",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"source: {json.dumps(source)}",
            f"fetched: {json.dumps(fetched)}",
            "generator: pulpie-mcp",
            "---",
        ]
    )
    return f"{front}\n\n{markdown.strip()}\n"


def write_doc(path: Path, markdown: str, *, title: str, source: str) -> SavedDoc:
    path.parent.mkdir(parents=True, exist_ok=True)
    fetched = now_iso()
    path.write_text(render(markdown, title=title, source=source, fetched=fetched), encoding="utf-8")
    return SavedDoc(path=path, title=title, source=source, fetched=fetched)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_frontmatter(path: Path) -> dict[str, str] | None:
    """Parse the frontmatter pulpie-mcp writes. Returns None for other files."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return None
            data: dict[str, str] = {}
            for _ in range(20):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                value = value.strip()
                try:
                    data[key.strip()] = str(json.loads(value)) if value.startswith('"') else value
                except json.JSONDecodeError:
                    data[key.strip()] = value
    except OSError:
        return None
    return data if data.get("generator") == "pulpie-mcp" else None


def outline(markdown: str, limit: int = 40) -> list[str]:
    """Top-level headings (#, ##, ###), skipping anything inside code fences."""
    items: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            text = match.group(2).rstrip("¶ ").strip()
            items.append("  " * (len(match.group(1)) - 1) + "- " + text)
            if len(items) >= limit:
                break
    return items


def title_from(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2)
    return fallback


def list_docs(directory: Path, query: str | None = None, limit: int = 200) -> list[SavedDoc]:
    if not directory.is_dir():
        return []
    needle = (query or "").lower()
    docs: list[SavedDoc] = []
    for path in sorted(directory.rglob("*.md")):
        if path.name == INDEX_NAME:
            continue
        meta = read_frontmatter(path)
        if meta is None:
            continue
        doc = SavedDoc(path, meta.get("title", path.stem), meta.get("source", ""), meta.get("fetched", ""))
        haystack = f"{doc.title} {doc.source} {path}".lower()
        if needle and needle not in haystack:
            continue
        docs.append(doc)
        if len(docs) >= limit:
            break
    return docs
