from pathlib import Path

import pytest

from pulpie_mcp import storage


def test_slug_from_url_path():
    assert storage.slug_for_url("https://docs.python.org/3/library/asyncio-queue.html") == (
        "3-library-asyncio-queue"
    )
    assert storage.slug_for_url("https://docs.astral.sh/uv/getting-started/") == "uv-getting-started"
    assert storage.slug_for_url("https://example.com/") == "index"


def test_slug_keeps_queries_distinct_and_long_paths_short():
    a = storage.slug_for_url("https://x.dev/search?q=one")
    b = storage.slug_for_url("https://x.dev/search?q=two")
    assert a != b and a.startswith("search-")
    long = storage.slug_for_url("https://x.dev/" + "segment/" * 40)
    assert len(long) <= storage.MAX_SLUG


def test_resolve_directory_requires_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("PULPIE_LIBRARY", str(tmp_path / "lib"))
    assert storage.resolve_directory(None, "https://www.example.com/a") == tmp_path / "lib" / "example.com"
    assert storage.resolve_directory(str(tmp_path), "https://x.dev") == tmp_path
    with pytest.raises(ValueError):
        storage.resolve_directory("relative/docs", "https://x.dev")


def test_write_and_read_frontmatter_round_trip(tmp_path):
    path = tmp_path / "page.md"
    title = 'Quotes "and": colons — dashes'
    storage.write_doc(path, "# Hello\n\nBody", title=title, source="https://x.dev/page")
    meta = storage.read_frontmatter(path)
    assert meta is not None
    assert meta["title"] == title
    assert meta["source"] == "https://x.dev/page"
    assert path.read_text().endswith("# Hello\n\nBody\n")


def test_target_path_does_not_clobber_other_sources(tmp_path):
    first = storage.target_path(tmp_path, "https://a.dev/guide")
    storage.write_doc(first, "a", title="A", source="https://a.dev/guide")
    assert storage.target_path(tmp_path, "https://a.dev/guide") == first  # same source: update
    other = storage.target_path(tmp_path, "https://b.dev/guide")
    assert other != first and other.name.startswith("guide-")


def test_target_path_never_uses_index_name_for_pages(tmp_path):
    assert storage.target_path(tmp_path, "https://a.dev/index.html").name == "home.md"
    assert storage.target_path(tmp_path, "https://a.dev/x", filename="My Notes.txt").name == "my-notes.md"


def test_outline_skips_code_fences_and_anchor_marks():
    md = "# Title¶\n\n```bash\n# not a heading\n```\n\n## Section\n#### too deep\n### Sub ##"
    assert storage.outline(md) == ["- Title", "  - Section", "    - Sub"]


def test_list_docs_filters_and_ignores_foreign_files(tmp_path):
    storage.write_doc(tmp_path / "a.md", "x", title="Queues", source="https://d.dev/queue")
    storage.write_doc(tmp_path / "sub" / "b.md", "x", title="Tasks", source="https://d.dev/task")
    (tmp_path / "notes.md").write_text("# my own notes\n")
    (tmp_path / storage.INDEX_NAME).write_text(storage.render("x", title="Index", source="https://d.dev"))

    titles = {d.title for d in storage.list_docs(tmp_path)}
    assert titles == {"Queues", "Tasks"}
    assert [d.title for d in storage.list_docs(tmp_path, "QUEUE")] == ["Queues"]
    assert storage.list_docs(Path("/definitely/not/here")) == []
