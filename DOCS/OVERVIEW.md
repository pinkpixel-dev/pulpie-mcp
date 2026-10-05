# pulpie-mcp Overview

pulpie-mcp is an MCP server that lets agents fetch web pages as clean Markdown, save them to disk, crawl docs sections, and list what they saved. Extraction runs locally with the Pulpie model `feyninc/pulpie-orange-small`.

This document describes how the project works right now. Decisions and their reasoning live in `MEMORY.md`. Release history lives in `CHANGELOG.md`.

## Stack

- Python 3.10+, packaged with hatchling, managed with `uv`
- MCP Python SDK 2.x (`mcp.server.MCPServer`, stdio transport)
- FastAPI and uvicorn for the backend
- `pulpie` (Apache 2.0) with PyTorch and transformers for extraction
- httpx for all HTTP, BeautifulSoup and lxml for HTML prep
- pytest with anyio for tests

## Core Flow

1. The agent's client launches `pulpie-mcp`, which runs `server.py` over stdio. This process never imports torch.
2. A tool calls `client.extract()`, which calls `launcher.ensure_backend()`.
3. `ensure_backend()` checks `GET /health` on `PULPIE_URL`. If nothing answers and the URL is local, it spawns `python -m pulpie_mcp serve` as a detached process that logs to `$PULPIE_HOME/backend.log`, then polls `/health` until `ready` is true.
4. The client sends `POST /extract` with the URL, `allow_private` (from `PULPIE_ALLOW_PRIVATE`), and `include_links`.
5. The backend fetches the page through the guarded httpx client, rewrites relative image and link URLs to absolute ones, collects page links, and runs Pulpie inference in a worker thread behind an asyncio lock.
6. The MCP tool formats the result, or writes it to disk through `storage.py`.

Invariant: only one backend serves a given port. The backend binds its socket before loading the model, so a second copy started in a race fails with "Address already in use" right away and exits.

## Project Structure

```text
src/pulpie_mcp/
  cli.py            entry point: stdio server (default), serve, status, stop
  config.py         environment variables and defaults, read at call time
  server.py         MCPServer with the four tools and the server instructions
  client.py         POST /extract with one retry on connection loss
  launcher.py       health check, detached spawn, ready wait, stop
  netguard.py       public-address check and the guarded httpx client
  storage.py        slugs, frontmatter, file targets, outline, library listing
  crawl.py          sitemap discovery, scoped link crawl, index.md
  backend/app.py    FastAPI app, idle watcher, socket binding, serve()
  backend/extract.py  fetch, HTML prep, ModelHolder (lazy torch import)
tests/
  test_storage.py, test_crawl.py, test_netguard.py   unit tests
  test_integration.py   real backend and model, gated by PULPIE_INTEGRATION=1
```

## Backend API

| Route | Purpose |
|---|---|
| `GET /health` | `ready`, `error`, `model`, `device`, `version`, `pid`, `idle_timeout` |
| `POST /extract` | Body `{url, allow_private, include_links}`. Returns `url` (after redirects), `title`, `markdown`, `links`, `passthrough`, timings |
| `POST /shutdown` | Asks uvicorn to exit |

`/extract` errors: 403 for blocked addresses, 415 for non-HTML content, 502 for fetch failures, 503 when the model failed to load. Responses over 15 MB are rejected.

## Storage

- Global library: `$PULPIE_LIBRARY` or `$PULPIE_HOME/library`, with one folder per host (leading `www.` removed).
- Tool `directory` arguments must be absolute paths.
- File names come from the URL path (`3-library-asyncio-queue.md`). Query strings add a short hash. Names longer than 100 characters are shortened with a hash.
- A page never saves as `index.md`. That name is reserved for crawl indexes, and a root URL saves as `home.md`.
- If the target file already exists with a different `source`, the new file gets a hash suffix. Same `source` overwrites, which is how re-fetching updates a doc.
- `read_frontmatter()` only recognizes files with `generator: pulpie-mcp`, so `list_docs()` ignores the user's own Markdown.
- A crawl will not overwrite a foreign `index.md`. It writes `pulpie-index.md` instead.

## Crawling

- Scope: same host, and the path starts with `path_prefix`. The default prefix is the start URL's path. A path with a file extension uses its parent folder instead.
- Discovery: `robots.txt` Sitemap lines, then `<prefix>sitemap.xml`, then `/sitemap.xml`, following up to 10 sitemap files including sitemap indexes. If no in-scope sitemap URLs exist, it follows links from each page's raw HTML instead.
- Four concurrent workers. Inference is serialized in the backend, so the concurrency mostly overlaps network time.
- Progress goes out through `ctx.report_progress`.

## Environment

| Variable | Default |
|---|---|
| `PULPIE_URL` | `http://127.0.0.1:8787` |
| `PULPIE_HOME` | `~/.pulpie` |
| `PULPIE_LIBRARY` | `$PULPIE_HOME/library` |
| `PULPIE_ALLOW_PRIVATE` | off |
| `PULPIE_IDLE_TIMEOUT` | `1800` (0 disables) |
| `PULPIE_START_TIMEOUT` | `600` |

## Commands

```bash
uv sync
uv run pytest
PULPIE_INTEGRATION=1 uv run pytest tests/test_integration.py
uv run pulpie-mcp serve
uv run pulpie-mcp status
```

## Current Limits

- No JavaScript rendering. Client-rendered pages come back mostly empty.
- Code blocks come out as indented blocks from pulpie's `html2text` step, not fenced blocks.
- Only tested on Linux with CUDA. Windows spawn flags are in place but untested.
- The private-address check resolves DNS before httpx connects, so DNS rebinding could slip past it. That's fine for a local tool, but it isn't a hard security boundary.
