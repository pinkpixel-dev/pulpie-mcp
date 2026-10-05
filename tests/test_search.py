import os

from pulpie_mcp import search, storage

GUIDE = """# Hooks

Intro text about hooks.

## useEffect

Runs after render.

```js
# not a heading
useEffect(() => {}, [])
```

### Cleanup

Return a function to clean up subscriptions.

## Annotations

Set read_only_hint when a tool never changes anything.
"""


def save(folder, name, markdown, source):
    return storage.write_doc(folder / name, markdown, title=name.removesuffix(".md").title(), source=source)


def run(query, root, db, **kw):
    return search.search(query, root, db_path=db, **kw)


def test_split_sections_builds_breadcrumbs_and_skips_fences():
    chunks = search.split_sections(GUIDE)
    heads = [c.heading for c in chunks]
    assert heads == ["Hooks", "Hooks > useEffect", "Hooks > useEffect > Cleanup", "Hooks > Annotations"]
    assert "# not a heading" in chunks[1].body


def test_long_sections_split_at_paragraphs():
    body = "\n\n".join(f"Paragraph {n} " + "word " * 100 for n in range(20))
    chunks = search.split_sections(f"# Big\n\n{body}")
    assert len(chunks) > 1
    assert all(len(c.body) <= search.MAX_CHUNK for c in chunks)
    assert chunks[1].body.startswith("Paragraph")


def test_finds_section_and_handles_punctuation(tmp_path):
    lib, db = tmp_path / "lib", tmp_path / "s.db"
    save(lib / "react.dev", "hooks.md", GUIDE, "https://react.dev/hooks")
    hits, exact = run("useEffect() cleanup", lib, db)
    assert exact
    assert hits[0].heading == "Hooks > useEffect > Cleanup"
    assert hits[0].source == "https://react.dev/hooks"
    assert run('"; DROP TABLE chunks; --', lib, db) == ([], False)


def test_camel_case_query_matches_snake_case_docs(tmp_path):
    lib, db = tmp_path / "lib", tmp_path / "s.db"
    save(lib, "hooks.md", GUIDE, "https://react.dev/hooks")
    hits, exact = run("readOnlyHint", lib, db)
    assert exact and hits[0].heading == "Hooks > Annotations"


def test_falls_back_to_partial_matches(tmp_path):
    lib, db = tmp_path / "lib", tmp_path / "s.db"
    save(lib, "hooks.md", GUIDE, "https://react.dev/hooks")
    hits, exact = run("subscriptions zebra", lib, db)
    assert not exact and hits[0].heading.endswith("Cleanup")


def test_sync_picks_up_edits_and_deletes(tmp_path):
    lib, db = tmp_path / "lib", tmp_path / "s.db"
    doc = save(lib, "page.md", "# Page\n\nalpha", "https://x.dev/page")
    assert run("alpha", lib, db)[0]

    doc.path.write_text(storage.render("# Page\n\nbravo", title="Page", source="https://x.dev/page"))
    os.utime(doc.path, ns=(1, 1))  # make sure mtime differs even on coarse filesystems
    assert run("alpha", lib, db) == ([], False)
    assert run("bravo", lib, db)[0]

    doc.path.unlink()
    assert run("bravo", lib, db) == ([], False)


def test_ignores_foreign_files_indexes_and_other_roots(tmp_path):
    lib, other, db = tmp_path / "lib", tmp_path / "project", tmp_path / "s.db"
    lib.mkdir()
    (lib / "notes.md").write_text("# Mine\n\nsecretword\n")
    (lib / storage.INDEX_NAME).write_text(storage.render("secretword", title="Index", source="https://x.dev"))
    save(other, "page.md", "# P\n\nsecretword", "https://x.dev/p")

    assert run("secretword", lib, db) == ([], False)
    hits, _ = run("secretword", other, db)
    assert [h.path.name for h in hits] == ["page.md"]


def test_site_filter_and_per_file_cap(tmp_path):
    lib, db = tmp_path / "lib", tmp_path / "s.db"
    many = "\n\n".join(f"## Part {n}\n\nshared term" for n in range(10))
    save(lib / "a.dev", "many.md", f"# Many\n\n{many}", "https://a.dev/many")
    save(lib / "b.dev", "one.md", "# One\n\nshared term", "https://www.b.dev/one")

    hits, _ = run("shared", lib, db, limit=10)
    assert sum(h.path.name == "many.md" for h in hits) == search.PER_FILE
    hits, _ = run("shared", lib, db, site="www.b.dev")
    assert [h.path.name for h in hits] == ["one.md"]
