from workspace_app.kb.links import rewrite_md_links

CID, USER = "collection:c1", "alice"


def _exists(*paths: str):
    ids = {f"{CID}/{USER}/{p}" for p in paths}
    return ids.__contains__


def test_rewrites_relative_links_to_existing_siblings_only():
    out = rewrite_md_links(
        "See [Foo](./foo.md), [ext](https://x.com), [gone](./missing.md).",
        doc_path="docs/index.md",
        collection_id=CID,
        user=USER,
        exists=_exists("docs/foo.md", "docs/index.md"),
    )
    assert "[Foo](kb://doc/collection:c1/alice/docs/foo.md)" in out  # sibling → kb:// id
    assert "[ext](https://x.com)" in out  # external untouched
    assert "[gone](./missing.md)" in out  # not in KB → left as-is


def test_resolves_parent_dirs_keeps_fragments_and_skips_anchors():
    out = rewrite_md_links(
        "[up](../setup.md#install) and [here](#section)",
        doc_path="guide/intro.md",
        collection_id=CID,
        user=USER,
        exists=_exists("setup.md"),
    )
    # ../setup.md from guide/ → setup.md; #fragment preserved
    assert "[up](kb://doc/collection:c1/alice/setup.md#install)" in out
    assert "[here](#section)" in out  # pure anchor → untouched
