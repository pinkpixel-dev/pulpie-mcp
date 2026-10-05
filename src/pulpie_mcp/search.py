"""Full-text search over saved docs, backed by a SQLite FTS5 index.

Pages are split into sections at #, ## and ### headings. The index syncs
lazily: every search rescans the folder by mtime and size, reindexes what
changed, and drops what was deleted, so files saved by any tool or edited
by hand are always searchable without hooks.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pulpie_mcp import config, storage

SCHEMA_VERSION = 1
MAX_CHUNK = 2000
MAX_TERMS = 16
PER_FILE = 3
SKIP_NAMES = {storage.INDEX_NAME, "pulpie-index.md"}
_TERM = re.compile(r"\w+", re.UNICODE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass
class Chunk:
    heading: str
    body: str


@dataclass
class Hit:
    title: str
    heading: str
    source: str
    path: Path
    excerpt: str


def split_sections(markdown: str) -> list[Chunk]:
    """Split Markdown at #-### headings outside code fences, keeping a breadcrumb."""
    chunks: list[Chunk] = []
    trail: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            heading = " > ".join(text for _, text in trail)
            chunks.extend(Chunk(heading, part) for part in _pieces(body))
        lines.clear()

    for line in markdown.splitlines():
        if storage._FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else storage._HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            trail = [(lvl, text) for lvl, text in trail if lvl < level]
            trail.append((level, match.group(2).rstrip("¶ ").strip()))
        else:
            lines.append(line)
    flush()
    return chunks


def _pieces(body: str) -> list[str]:
    """Break a long section at paragraph boundaries so each piece fits MAX_CHUNK."""
    if len(body) <= MAX_CHUNK:
        return [body]
    pieces: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n", body):
        if current and len(current) + len(para) + 2 > MAX_CHUNK:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
        while len(current) > MAX_CHUNK:  # one huge paragraph or code block
            pieces.append(current[:MAX_CHUNK])
            current = current[MAX_CHUNK:]
    if current.strip():
        pieces.append(current)
    return pieces


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = db_path or config.search_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        conn.executescript(
            f"""
            BEGIN IMMEDIATE;
            DROP TABLE IF EXISTS files;
            DROP TABLE IF EXISTS chunks;
            CREATE TABLE files (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER);
            CREATE VIRTUAL TABLE chunks USING fts5(
                title, heading, body,
                path UNINDEXED, source UNINDEXED, site UNINDEXED,
                tokenize = 'porter unicode61'
            );
            PRAGMA user_version = {SCHEMA_VERSION};
            COMMIT;
            """
        )
    return conn


def sync(conn: sqlite3.Connection, root: Path) -> int:
    """Bring the index for everything under root up to date. Returns files reindexed."""
    prefix = f"{root}/"
    known = {
        path: (mtime, size)
        for path, mtime, size in conn.execute(
            "SELECT path, mtime_ns, size FROM files WHERE substr(path, 1, ?) = ?",
            (len(prefix), prefix),
        )
    }
    changed: list[tuple[Path, int, int]] = []
    seen: set[str] = set()
    for path in root.rglob("*.md") if root.is_dir() else ():
        if path.name in SKIP_NAMES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path)
        seen.add(key)
        if known.get(key) != (stat.st_mtime_ns, stat.st_size):
            changed.append((path, stat.st_mtime_ns, stat.st_size))
    gone = [key for key in known if key not in seen]
    if not changed and not gone:
        return 0

    conn.execute("BEGIN IMMEDIATE")
    try:
        for key in gone:
            _drop(conn, key)
        for path, mtime, size in changed:
            _drop(conn, str(path))
            _index(conn, path)
            # Foreign files are recorded too, so they aren't reread on every search.
            conn.execute("INSERT INTO files VALUES (?, ?, ?)", (str(path), mtime, size))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(changed)


def _drop(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM chunks WHERE path = ?", (key,))
    conn.execute("DELETE FROM files WHERE path = ?", (key,))


def _index(conn: sqlite3.Connection, path: Path) -> None:
    meta = storage.read_frontmatter(path)
    if meta is None:
        return  # not saved by pulpie
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    title = meta.get("title", path.stem)
    source = meta.get("source", "")
    site = storage.site_folder(source) if source else ""
    conn.executemany(
        "INSERT INTO chunks (title, heading, body, path, source, site) VALUES (?, ?, ?, ?, ?, ?)",
        [(title, c.heading, c.body, str(path), source, site) for c in split_sections(strip_frontmatter(text))],
    )


def match_expression(query: str, *, any_term: bool = False) -> str | None:
    """Quote each word so punctuation like `useEffect()` or `a.b` can't break FTS5 syntax.

    A camelCase word also matches its split form, so `readOnlyHint` finds `read_only_hint`.
    """
    terms = list(dict.fromkeys(_TERM.findall(query)))[:MAX_TERMS]
    if not terms:
        return None
    return (" OR " if any_term else " AND ").join(_term(t) for t in terms)


def _term(word: str) -> str:
    split = _CAMEL.sub(" ", word)
    return f'"{word}"' if split == word else f'("{word}" OR "{split}")'


def search(
    query: str,
    root: Path,
    *,
    site: str | None = None,
    limit: int = 8,
    db_path: Path | None = None,
) -> tuple[list[Hit], bool]:
    """Sync root, then return the best matching sections and whether they match every word.

    Tries all words first, then falls back to sections that match any of them.
    """
    root = root.expanduser().resolve()
    with closing(connect(db_path)) as conn:
        sync(conn, root)
        for any_term in (False, True):
            expr = match_expression(query, any_term=any_term)
            if expr is None:
                break
            hits = _query(conn, expr, root, site, limit)
            if hits:
                return hits, not any_term
    return [], False


def _query(conn: sqlite3.Connection, expr: str, root: Path, site: str | None, limit: int) -> list[Hit]:
    prefix = f"{root}/"
    sql = (
        "SELECT title, heading, body, path, source FROM chunks "
        "WHERE chunks MATCH ? AND substr(path, 1, ?) = ?"
    )
    params: list[object] = [expr, len(prefix), prefix]
    if site:
        sql += " AND site = ?"
        params.append(site.lower().removeprefix("www."))
    sql += " ORDER BY bm25(chunks, 5.0, 3.0, 1.0) LIMIT ?"
    params.append(limit * PER_FILE * 2)

    hits: list[Hit] = []
    per_file: dict[str, int] = {}
    for title, heading, body, path, source in conn.execute(sql, params):
        if per_file.get(path, 0) >= PER_FILE:
            continue
        per_file[path] = per_file.get(path, 0) + 1
        hits.append(Hit(title, heading, source, Path(path), body))
        if len(hits) >= limit:
            break
    return hits
