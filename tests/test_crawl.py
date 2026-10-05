from pulpie_mcp import crawl, storage


def test_normalize_strips_fragments_and_trailing_slashes():
    assert crawl.normalize("HTTPS://Docs.X.dev/guide/#install") == "https://docs.x.dev/guide"
    assert crawl.normalize("https://docs.x.dev/") == "https://docs.x.dev/"
    assert crawl.normalize("https://x.dev/a?b=1#c") == "https://x.dev/a?b=1"


def test_default_prefix():
    assert crawl.default_prefix("https://docs.astral.sh/uv/") == "/uv/"
    assert crawl.default_prefix("https://react.dev/learn") == "/learn/"
    assert crawl.default_prefix("https://docs.python.org/3/library/asyncio.html") == "/3/library/"
    assert crawl.default_prefix("https://docs.x.dev") == "/"


def test_in_scope():
    host, prefix = "docs.x.dev", "/guide/"
    assert crawl.in_scope("https://docs.x.dev/guide/intro", host, prefix)
    assert crawl.in_scope("https://docs.x.dev/guide", host, prefix)
    assert not crawl.in_scope("https://docs.x.dev/blog/post", host, prefix)
    assert not crawl.in_scope("https://other.dev/guide/intro", host, prefix)
    assert not crawl.in_scope("https://docs.x.dev/guide/manual.pdf", host, prefix)
    assert not crawl.in_scope("mailto:hi@x.dev", host, prefix)


def test_parse_sitemap_urlset_and_index():
    urlset = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://x.dev/a</loc></url>
  <url><loc> https://x.dev/b </loc><lastmod>2026-01-01</lastmod></url>
</urlset>"""
    assert crawl.parse_sitemap(urlset) == (["https://x.dev/a", "https://x.dev/b"], [])

    index = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.dev/sitemap-1.xml</loc></sitemap>
</sitemapindex>"""
    assert crawl.parse_sitemap(index) == ([], ["https://x.dev/sitemap-1.xml"])


def test_write_index_respects_foreign_index(tmp_path):
    doc = storage.write_doc(tmp_path / "a.md", "x", title="A", source="https://x.dev/a")
    (tmp_path / "index.md").write_text("# The user's own index\n")
    path = crawl.write_index(tmp_path, "https://x.dev/", [doc])
    assert path.name == "pulpie-index.md"
    assert (tmp_path / "index.md").read_text() == "# The user's own index\n"
    assert "- [A](a.md): https://x.dev/a" in path.read_text()
