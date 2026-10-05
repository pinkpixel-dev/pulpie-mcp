# Changelog

## 0.1.0 - October 5, 2026

First release.

### 🧰 Tools

- `fetch_markdown` reads a page and returns its main content as Markdown, cut off at `max_chars`
- `save_markdown` saves a page as a `.md` file with `title`, `source`, and `fetched` frontmatter, and returns the path plus a heading outline
- `crawl_docs` saves a docs section as a folder of `.md` files and an `index.md`, using the sitemap when there is one and following links when there isn't
- `list_library` lists saved docs with optional search

### ⚙️ Backend

- Local HTTP backend keeps one Pulpie model (`feyninc/pulpie-orange-small`) loaded for every agent session
- Starts automatically on first use and shuts down after 30 minutes idle
- Blocks private and local addresses by default, including on redirects (`PULPIE_ALLOW_PRIVATE=1` to allow them)
- Passes `text/markdown` and `text/plain` responses through unchanged

### 🖥️ CLI

- `pulpie-mcp`, `pulpie-mcp serve`, `pulpie-mcp status`, and `pulpie-mcp stop`
