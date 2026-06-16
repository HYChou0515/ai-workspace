from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.links import rewrite_md_links

CID = "collection:c1"


def _doc_resolver(*paths: str):
    """Test resolver: text/markdown SourceDocs at `paths` resolve to
    `kb://doc/{rid}` (the FE handles in-app navigation); everything
    else returns None (link untouched)."""
    ids = {encode_doc_id(CID, p): p for p in paths}

    def resolve(rid: str) -> str | None:
        if rid in ids:
            return f"kb://doc/{rid}"
        return None

    return resolve


def _image_resolver(images: dict[str, str]):
    """Test resolver for image SourceDocs: maps path → file_id, emits
    specstar `/blobs/{file_id}` URLs."""
    by_rid = {encode_doc_id(CID, p): fid for p, fid in images.items()}

    def resolve(rid: str) -> str | None:
        fid = by_rid.get(rid)
        return f"/blobs/{fid}" if fid else None

    return resolve


def test_rewrites_relative_links_to_existing_siblings_only():
    out = rewrite_md_links(
        "See [Foo](./foo.md), [ext](https://x.com), [gone](./missing.md).",
        doc_path="docs/index.md",
        collection_id=CID,
        resolve=_doc_resolver("docs/foo.md", "docs/index.md"),
    )
    assert f"[Foo](kb://doc/{encode_doc_id(CID, 'docs/foo.md')})" in out  # sibling → kb:// id
    assert "[ext](https://x.com)" in out  # external untouched
    assert "[gone](./missing.md)" in out  # not in KB → left as-is


def test_extensionless_link_target_resolves_to_existing_md_sibling():
    """Issue #41: when one doc references another by name only (no
    extension), e.g. `[B](./b)`, the rewriter should still match the
    sibling `b.md` so the cross-doc link survives ingestion."""
    out = rewrite_md_links(
        "see [B](./b)",
        doc_path="docs/a.md",
        collection_id=CID,
        resolve=_doc_resolver("docs/b.md"),
    )
    assert f"[B](kb://doc/{encode_doc_id(CID, 'docs/b.md')})" in out


def test_extensionless_link_unchanged_when_no_md_sibling_exists():
    out = rewrite_md_links(
        "see [Missing](./missing)",
        doc_path="docs/a.md",
        collection_id=CID,
        resolve=_doc_resolver("docs/a.md"),  # no `missing.md`
    )
    assert "[Missing](./missing)" in out  # untouched


def test_extensionless_link_with_fragment_preserves_fragment():
    """`[B](./b#section)` → `kb://doc/{rid for b.md}#section`."""
    out = rewrite_md_links(
        "[B](./b#section)",
        doc_path="docs/a.md",
        collection_id=CID,
        resolve=_doc_resolver("docs/b.md"),
    )
    assert f"[B](kb://doc/{encode_doc_id(CID, 'docs/b.md')}#section)" in out


def test_image_link_rewritten_to_specstar_blob_url():
    """Issue #41 follow-up: image cross-refs land on specstar's
    `/blobs/{file_id}` URL — the FE renders `<img src="/blobs/...">`
    natively and specstar serves the bytes with the Content-Type
    stored at upload time (so `<img>` inlines instead of downloading).
    Markdown fragments are NOT appended to blob URLs (binaries don't
    have sections)."""
    out = rewrite_md_links(
        "![pic](./diagram.png#caption)",
        doc_path="docs/a.md",
        collection_id=CID,
        resolve=_image_resolver({"docs/diagram.png": "blob-abc"}),
    )
    assert "![pic](/blobs/blob-abc)" in out


def test_resolves_parent_dirs_keeps_fragments_and_skips_anchors():
    out = rewrite_md_links(
        "[up](../setup.md#install) and [here](#section)",
        doc_path="guide/intro.md",
        collection_id=CID,
        resolve=_doc_resolver("setup.md"),
    )
    # ../setup.md from guide/ → setup.md; #fragment preserved
    assert f"[up](kb://doc/{encode_doc_id(CID, 'setup.md')}#install)" in out
    assert "[here](#section)" in out  # pure anchor → untouched
