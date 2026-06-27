from collections.abc import AsyncIterator

from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, SourceDoc
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _client() -> TestClient:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def _client_and_spec() -> tuple[TestClient, SpecStar]:
    """Like `_client()` but also hands back the spec, so a test can seed
    records (e.g. CitationEvents) the HTTP surface has no endpoint for."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app), spec


def _client_with_pipeline() -> TestClient:
    """Issue #39 store-all behaviour only applies in pipeline mode
    (legacy chunker stays text-only). Image / binary upload tests use
    this variant so the Ingestor's parser registry path is exercised."""
    from workspace_app.kb.li_pipeline import build_doc_pipeline

    spec = make_spec()
    embedder = HashEmbedder(dim=EMBED_DIM)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=embedder,
        kb_pipeline=build_doc_pipeline(embedder=embedder),
    )
    return TestClient(app)


def test_create_and_list_collections():
    client = _client()
    created = client.post(
        "/kb/collections", json={"name": "HR", "description": "policies", "icon": "bug"}
    ).json()
    cid = created["resource_id"]
    # create returns the full card shape (empty aggregates for a fresh collection)
    assert created["icon"] == "bug"
    assert created["doc_count"] == 0 and created["size"] == 0 and created["cited"] == 0
    assert created["owner"] and created["updated_at"] > 0

    listed = client.get("/kb/collections").json()
    match = next(c for c in listed if c["resource_id"] == cid)
    assert match["name"] == "HR"
    assert match["description"] == "policies"
    assert match["icon"] == "bug"
    assert {"doc_count", "size", "updated_at", "owner"} <= match.keys()


def test_collection_lists_its_per_wiki_guidance_after_a_patch():
    """#90: the two per-collection wiki guidance fields round-trip through the
    card list, so the FE editor can prefill the current values. A fresh
    collection lists them blank; a native PATCH updates them."""
    client = _client()
    cid = client.post("/kb/collections", json={"name": "kb", "use_wiki": True}).json()[
        "resource_id"
    ]
    fresh = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert fresh["wiki_maintainer_guidance"] == "" and fresh["wiki_reader_guidance"] == ""

    # the FE edits guidance via specstar's native partial update
    r = client.patch(
        f"/collection/{cid}",
        json={
            "wiki_maintainer_guidance": "Organize by zone.",
            "wiki_reader_guidance": "TL;DR first.",
        },
    )
    assert r.status_code < 300, r.text

    got = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert got["wiki_maintainer_guidance"] == "Organize by zone."
    assert got["wiki_reader_guidance"] == "TL;DR first."


def test_collection_card_aggregates_docs_size_and_updated():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"hello", "text/markdown")},
    )
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("b.md", b"worldwide", "text/markdown")},
    )
    card = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert card["doc_count"] == 2
    assert card["size"] == len(b"hello") + len(b"worldwide")  # summed bytes
    assert card["updated_at"] > 0


def test_collection_card_aggregates_doc_token_counts():
    # #88: the card's "≈ N tokens" is the SUM of each ready doc's chunk-based
    # token_count (a CJK-aware estimate of the extracted text), not raw bytes/4.
    client, spec = _client_and_spec()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", "資料科學 報告".encode(), "text/markdown")},
    )
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("b.md", b"hello world foo bar baz", "text/markdown")},
    )
    _drain(client)  # token_count is set only once indexing reaches "ready"

    docs = spec.get_resource_manager(SourceDoc).list_resources((QB["collection_id"] == cid).build())
    expected = sum(d.data.token_count for d in docs)  # ty: ignore[unresolved-attribute]
    assert expected > 0
    card = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert card["tokens"] == expected


def _new_collection(client: TestClient) -> str:
    return client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]


def _drain(client: TestClient) -> None:
    """#82: indexing now runs on a background job-queue consumer (not a
    TestClient-run BackgroundTask), so block until it drains before asserting
    post-index state (status=ready / chunks)."""
    client.app.state.index_coordinator.wait_idle()  # ty: ignore[unresolved-attribute]


def test_upload_document_and_list():
    client = _client()
    cid = _new_collection(client)
    files = {"file": ("guide.md", b"# Guide\none two three", "text/markdown")}
    r = client.post(f"/kb/collections/{cid}/documents", files=files)
    assert r.status_code == 200
    guide_id = encode_doc_id(cid, "guide.md")
    assert r.json()["document_ids"] == [guide_id]
    assert "/" not in guide_id  # specstar ids are slash-free
    assert r.json()["status"] == "indexing"  # embedding runs in the background

    _drain(client)
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    match = next(d for d in docs if d["resource_id"] == guide_id)
    assert match["path"] == "guide.md"
    assert match["content_type"] in ("text/plain", "text/markdown")
    assert match["created_by"] == "default-user"  # specstar audit meta
    assert match["status"] == "ready"  # background index finished (drained above)
    # #87: the content blob id rides the list so the doc IDE can build a
    # sibling image ref's `/source-doc/{id}/blobs/{file_id}` URL without a
    # per-doc render call.
    assert match["file_id"]
    blob = client.get(f"/source-doc/{guide_id}/blobs/{match['file_id']}")
    assert blob.status_code == 200 and blob.content == b"# Guide\none two three"


def _upload(client, cid, name, data=b"hello there"):
    return client.post(
        f"/kb/collections/{cid}/documents", files={"file": (name, data, "text/markdown")}
    )


def _set_quality(spec, doc_id, *, score, rationale="", breakdown=None):
    import msgspec

    rm = spec.get_resource_manager(SourceDoc)
    doc = rm.get(doc_id).data
    rm.update(
        doc_id,
        msgspec.structs.replace(
            doc, quality_score=score, quality_rationale=rationale, quality_breakdown=breakdown or {}
        ),
    )


def test_list_documents_exposes_quality_score():
    # #105: the per-doc quality score rides the list row so the FE can draw a
    # quality badge + sort. Un-scored docs report null (neutral).
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    _upload(client, cid, "a.md")
    _drain(client)
    doc_id = encode_doc_id(cid, "a.md")
    row = next(d for d in client.get(f"/kb/collections/{cid}/documents").json()["items"])
    assert row["quality_score"] is None  # un-scored = neutral
    assert row["quality_rationale"] == ""
    _set_quality(spec, doc_id, score=73, rationale="Clear and complete.")
    row = next(d for d in client.get(f"/kb/collections/{cid}/documents").json()["items"])
    assert row["quality_score"] == 73
    assert row["quality_rationale"] == "Clear and complete."


def test_list_documents_can_sort_by_quality_worst_first():
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    for name in ("good.md", "bad.md", "mid.md"):
        _upload(client, cid, name)
    _drain(client)
    _set_quality(spec, encode_doc_id(cid, "good.md"), score=88)
    _set_quality(spec, encode_doc_id(cid, "bad.md"), score=14)
    _set_quality(spec, encode_doc_id(cid, "mid.md"), score=50)

    items = client.get(f"/kb/collections/{cid}/documents?sort=quality").json()["items"]
    scored = [d["path"] for d in items if d["quality_score"] is not None]
    assert scored == ["bad.md", "mid.md", "good.md"]


def test_render_document_exposes_quality_rationale_and_breakdown():
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    _upload(client, cid, "a.md")
    _drain(client)
    doc_id = encode_doc_id(cid, "a.md")
    _set_quality(spec, doc_id, score=42, rationale="Thin and noisy.", breakdown={"noise": 0.7})

    rd = client.get(f"/kb/documents?id={doc_id}").json()
    assert rd["quality_score"] == 42
    assert rd["quality_rationale"] == "Thin and noisy."
    assert rd["quality_breakdown"] == {"noise": 0.7}


def test_list_documents_exposes_unit_progress_for_an_indexing_fanout_doc():
    """#248: a fanned-out doc that is still indexing carries a real done/total
    unit count so the FE can draw a monotonic progress bar (not parse a string)."""
    from specstar.types import Binary

    from workspace_app.kb.index_run import IndexRunStore
    from workspace_app.resources import SourceDoc

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = drm.create(
        SourceDoc(collection_id=cid, path="big.pdf", content=Binary(data=b"x"), status="indexing")
    ).resource_id
    runs = IndexRunStore(spec)
    runs.start(doc_id, cid, total=3, units_total=24)  # 24-page PDF
    runs.mark_done(doc_id, 0, batch_units=8)  # one batch (8 pages) done

    items = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    row = next(d for d in items if d["resource_id"] == doc_id)
    assert (row["units_done"], row["units_total"]) == (8, 24)


def test_list_documents_reports_zero_units_for_a_doc_without_a_fanout_run():
    """A small / single-job doc has no IndexRun → 0/0, so the FE shows no bar."""
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents", files={"file": ("a.md", b"hi there", "text/markdown")}
    )
    _drain(client)
    row = client.get(f"/kb/collections/{cid}/documents").json()["items"][0]
    assert (row["units_done"], row["units_total"]) == (0, 0)


def test_move_document_rekeys_and_preserves_content():
    # #87: a doc's id encodes its path, so rename/move re-keys — the doc moves
    # to a new id at the new path, same bytes, then re-indexes.
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"# A\nbody", "text/markdown")},
    )
    old_id = encode_doc_id(cid, "a.md")
    _drain(client)

    r = client.post(f"/kb/documents/move?id={old_id}&to=b.md")
    assert r.status_code == 200
    new_id = encode_doc_id(cid, "b.md")
    assert r.json() == {"moved_from": old_id, "moved_to": new_id}

    assert client.get(f"/source-doc/{old_id}").status_code == 404  # old id gone
    _drain(client)
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    moved = next(d for d in docs if d["resource_id"] == new_id)
    assert moved["path"] == "b.md"
    assert moved["status"] == "ready"  # the re-index ran
    blob = client.get(f"/source-doc/{new_id}/blobs/{moved['file_id']}")
    assert blob.content == b"# A\nbody"  # content carried over


def test_same_path_from_two_users_is_one_shared_doc_last_write_wins():
    # The collection is a shared drive: a path is ONE doc whoever uploads it, so
    # a second user at the same path overwrites the content; created_by stays the
    # original uploader, updated_by tracks the latest.
    who = {"u": "alice"}

    def user() -> str:
        return who["u"]

    spec = make_spec(default_user=user)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=user,
    )
    client = TestClient(app)
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("spec.md", b"# Spec\nalice wrote this", "text/markdown")},
    )
    who["u"] = "bob"  # a different user writes the SAME path
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("spec.md", b"# Spec\nbob overwrote it", "text/markdown")},
    )

    doc_id = encode_doc_id(cid, "spec.md")
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    matches = [d for d in docs if d["path"] == "spec.md"]
    assert len(matches) == 1  # ONE shared doc, not one-per-user
    assert matches[0]["resource_id"] == doc_id
    assert matches[0]["created_by"] == "alice"  # original owner kept (resource creator)
    blob = client.get(f"/source-doc/{doc_id}/blobs/{matches[0]['file_id']}")
    assert blob.content == b"# Spec\nbob overwrote it"  # last write wins (bob's content)


def test_move_document_preserves_the_original_creator():
    # #83 reasoning: a mechanical move by another user must not erase the real
    # uploader. alice uploads, bob moves → created_by stays alice.
    who = {"u": "alice"}

    def user() -> str:
        return who["u"]

    spec = make_spec(default_user=user)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=user,
    )
    client = TestClient(app)
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"# A", "text/markdown")},
    )
    old_id = encode_doc_id(cid, "a.md")
    client.app.state.index_coordinator.wait_idle()  # ty: ignore[unresolved-attribute]

    who["u"] = "bob"  # a DIFFERENT user does the move
    r = client.post(f"/kb/documents/move?id={old_id}&to=b.md")
    new_id = encode_doc_id(cid, "b.md")  # path-keyed; the move preserves the creator
    assert r.json()["moved_to"] == new_id
    client.app.state.index_coordinator.wait_idle()  # ty: ignore[unresolved-attribute]
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    moved = next(d for d in docs if d["resource_id"] == new_id)
    assert moved["created_by"] == "alice"  # preserved despite bob doing the move


def test_move_document_rejects_a_name_collision():
    client = _client()
    cid = _new_collection(client)
    for name, body in (("a.md", b"# A\nalpha"), ("b.md", b"# B\nbeta")):
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (name, body, "text/markdown")},
        )
    old_id = encode_doc_id(cid, "a.md")
    _drain(client)
    r = client.post(f"/kb/documents/move?id={old_id}&to=b.md")  # b.md already exists
    assert r.status_code == 409


def test_move_document_missing_is_404():
    client = _client()
    cid = _new_collection(client)
    missing = encode_doc_id(cid, "nope.md")
    assert client.post(f"/kb/documents/move?id={missing}&to=x.md").status_code == 404


def test_move_document_to_the_same_path_is_a_noop():
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"# A\nbody", "text/markdown")},
    )
    old_id = encode_doc_id(cid, "a.md")
    _drain(client)
    r = client.post(f"/kb/documents/move?id={old_id}&to=a.md")
    assert r.status_code == 200
    assert r.json() == {"moved_from": old_id, "moved_to": old_id}
    assert client.get(f"/source-doc/{old_id}").status_code == 200  # untouched


def test_move_document_canonicalizes_the_target_path():
    # The move target is canonicalised before it re-keys, so a leading-slash
    # (or other surface noise) destination lands at the SAME relative path the
    # rest of the system stores — no "/b.md" twin of "b.md".
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"# A\nbody", "text/markdown")},
    )
    old_id = encode_doc_id(cid, "a.md")
    _drain(client)

    r = client.post(f"/kb/documents/move?id={old_id}&to=/sub/b.md")
    assert r.status_code == 200
    new_id = encode_doc_id(cid, "sub/b.md")  # canonical, leading slash gone
    assert r.json()["moved_to"] == new_id
    _drain(client)
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    moved = next(d for d in docs if d["resource_id"] == new_id)
    assert moved["path"] == "sub/b.md"


def test_reindex_collection_rebuilds_all_docs():
    client = _client()
    cid = _new_collection(client)
    for name in ("a.md", "b.md"):
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (name, b"# one two three four", "text/markdown")},
        )

    r = client.post(f"/kb/collections/{cid}/reindex")
    assert r.status_code == 200
    assert r.json()["reindexed"] == 2
    assert r.json()["status"] == "indexing"

    _drain(client)  # background re-index consumer → docs ready, chunks rebuilt
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    assert len(docs) == 2
    assert all(d["status"] == "ready" for d in docs)
    assert all(d["chunks"] > 0 for d in docs)


def test_reindex_collection_only_failed_requeues_only_error_docs():
    """`?only=failed` re-indexes ONLY docs stuck in `error`, leaving healthy
    `ready` docs untouched — issue #223: recover a collection after a transient
    embedder outage without paying to re-embed every doc that already indexed."""
    import msgspec

    from workspace_app.resources.kb import SourceDoc

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    for name in ("good.md", "bad.md"):
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (name, b"# one two three four", "text/markdown")},
        )
    _drain(client)  # both land ready

    # Force one doc into the failed state, exactly as a real embedding error would.
    rm = spec.get_resource_manager(SourceDoc)
    bad_id = encode_doc_id(cid, "bad.md")
    bad = rm.get(bad_id).data
    assert isinstance(bad, SourceDoc)
    rm.update(bad_id, msgspec.structs.replace(bad, status="error", status_detail="boom"))

    r = client.post(f"/kb/collections/{cid}/reindex", params={"only": "failed"})
    assert r.status_code == 200
    assert r.json()["reindexed"] == 1  # only the failed doc was queued, not both

    _drain(client)
    docs = {d["path"]: d for d in client.get(f"/kb/collections/{cid}/documents").json()["items"]}
    assert docs["bad.md"]["status"] == "ready"  # recovered out of error
    assert docs["good.md"]["status"] == "ready"  # never re-queued, still healthy


def test_reindex_collection_rejects_unknown_only_value():
    """`only` is a closed vocabulary: anything but `failed` (or absent) is a 400,
    so a typo can't silently fall back to re-indexing the whole collection."""
    client = _client()
    cid = _new_collection(client)
    r = client.post(f"/kb/collections/{cid}/reindex", params={"only": "ready"})
    assert r.status_code == 400


def test_list_documents_is_paged_via_specstar_qb_offset_limit():
    """Documents inside a collection are paged through specstar's
    `QB[...].offset(offset).limit(limit)` query, sorted by the IMMUTABLE
    `created_time` descending (with `resource_id` as tiebreak) so paging stays
    stable even while rows re-index — see
    `test_paging_stays_consistent_when_a_doc_is_reindexed_mid_paging` (#184).
    `total` is the FULL filtered count; `has_more` is the convenience
    offset+len<total flag.

    Locks the API shape so we don't accidentally fall back to a fetch-all
    response.
    """
    import time

    client = _client()
    cid = _new_collection(client)
    # Upload 5 docs in a fixed order. specstar stamps `created_time` at birth,
    # so the latest upload is the page's first item (sort desc); for docs never
    # re-indexed this equals upload order.
    paths = ["a.md", "b.md", "c.md", "d.md", "e.md"]
    for p in paths:
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (p, b"# tiny", "text/markdown")},
        )
        # Ensure a strictly monotonic updated_time even on systems where the
        # write clock has < 1 ms resolution — the sort needs distinct keys.
        time.sleep(0.005)

    # Total + first page (newest first, e/d).
    page1 = client.get(f"/kb/collections/{cid}/documents?offset=0&limit=2").json()
    assert page1["total"] == 5
    assert page1["offset"] == 0
    assert page1["limit"] == 2
    assert page1["has_more"] is True
    assert [d["path"] for d in page1["items"]] == ["e.md", "d.md"]

    # Middle page.
    page2 = client.get(f"/kb/collections/{cid}/documents?offset=2&limit=2").json()
    assert [d["path"] for d in page2["items"]] == ["c.md", "b.md"]
    assert page2["has_more"] is True

    # Tail page.
    page3 = client.get(f"/kb/collections/{cid}/documents?offset=4&limit=2").json()
    assert [d["path"] for d in page3["items"]] == ["a.md"]
    assert page3["has_more"] is False

    # Default (no query string) returns up to `limit=50` newest-first.
    default = client.get(f"/kb/collections/{cid}/documents").json()
    assert default["total"] == 5
    assert default["offset"] == 0
    assert default["limit"] == 50
    assert default["has_more"] is False
    assert [d["path"] for d in default["items"]] == [
        "e.md",
        "d.md",
        "c.md",
        "b.md",
        "a.md",
    ]


def test_paging_stays_consistent_when_a_doc_is_reindexed_mid_paging():
    """During indexing a re-ingest/re-index bumps a doc's `updated_time`. If the
    page were sorted by `updated_time`, a doc bumped *between* the FE's offset
    fetches would jump to the front and slide the window, so the fetch-all loop
    would double-count one row and drop another. Sorting by the IMMUTABLE
    `created_time` (+ `resource_id` tiebreak) pins the window, so paging the
    collection in slices yields every doc exactly once even while rows churn. (#184)
    """
    import time

    client = _client()
    cid = _new_collection(client)
    paths = ["a.md", "b.md", "c.md", "d.md", "e.md"]
    for p in paths:
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (p, b"# tiny", "text/markdown")},
        )
        time.sleep(0.005)  # strictly monotonic created_time for a deterministic order

    # Fetch the first slice, THEN re-ingest an OLDER doc (its `updated_time` jumps
    # to newest, mimicking indexing finishing on "a.md" between offset fetches).
    page1 = client.get(f"/kb/collections/{cid}/documents?offset=0&limit=2").json()
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"# tiny v2", "text/markdown")},
    )
    page2 = client.get(f"/kb/collections/{cid}/documents?offset=2&limit=2").json()
    page3 = client.get(f"/kb/collections/{cid}/documents?offset=4&limit=2").json()

    seen = [d["path"] for pg in (page1, page2, page3) for d in pg["items"]]
    assert len(seen) == len(set(seen)), f"duplicate rows across pages: {seen}"
    assert sorted(seen) == sorted(paths), f"a doc was dropped/duplicated: {seen}"


def test_folder_upload_preserves_relative_path():
    # a folder upload sends each file with its relative path as the filename;
    # the doc id + path preserve that structure (handled like an archive member)
    client = _client()
    cid = _new_collection(client)
    files = {"file": ("manuals/reflow/guide.md", b"# Guide\nzone three", "text/markdown")}
    r = client.post(f"/kb/collections/{cid}/documents", files=files)
    assert r.json()["document_ids"] == [encode_doc_id(cid, "manuals/reflow/guide.md")]
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    assert any(d["path"] == "manuals/reflow/guide.md" for d in docs)


def test_render_document_rewrites_crossrefs_and_returns_markdown():
    import io
    import zipfile

    client = _client()
    cid = _new_collection(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.md", "See [Foo](./foo.md) and [Gone](./gone.md).")
        z.writestr("foo.md", "# Foo\nbody")
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("docs.zip", buf.getvalue(), "application/zip")},
    )

    body = client.get("/kb/documents", params={"id": encode_doc_id(cid, "index.md")}).json()
    assert body["filename"] == "index.md"
    foo_id = encode_doc_id(cid, "foo.md")
    assert f"kb://doc/{foo_id}" in body["markdown"]  # existing sibling → rewritten
    assert "[Gone](./gone.md)" in body["markdown"]  # missing → left as-is


def test_render_image_doc_returns_parsed_text_as_markdown():
    """#114: an image SourceDoc carries the VLM-parsed markdown on `text`.
    The viewer must surface that text (alongside the image the FE loads from
    the blob), not an empty body."""
    import msgspec
    from specstar.types import Binary

    from workspace_app.resources.kb import SourceDoc

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("diagram.png", b"placeholder", "text/markdown")},
    )
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "diagram.png")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    rm.update(
        doc_id,
        msgspec.structs.replace(
            doc,
            content=Binary(data=b"\x89PNG\r\n", content_type="image/png"),
            text="# Diagram\nalpha beta gamma",
        ),
    )

    body = client.get("/kb/documents", params={"id": doc_id}).json()
    assert body["content_type"] == "image/png"
    assert "alpha beta gamma" in body["markdown"]


def test_render_document_via_raw_url_with_percent_encoded_division_slash():
    """The FE composes the URL as `/kb/documents?id=` + encodeURIComponent(doc_id).
    A real doc_id contains U+2215 (`∕`, division slash) wherever the natural
    key's ASCII `/` used to be; `encodeURIComponent` turns each into
    `%E2%88%95`. FastAPI's Query() must round-trip that back through to
    `rm.get(<...∕...>)`. Issue #34: clicking a reference card 404s because
    something in the chain dropped the encoding. This test pins the
    end-to-end behaviour: TestClient hitting a raw URL with the encoded
    bytes must reach the document."""
    from urllib.parse import quote

    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"hello", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "guide.md")
    # encodeURIComponent equivalent — encode every char that's not the
    # JS unreserved set. The ∕ in doc_id MUST come out as %E2%88%95.
    encoded = quote(doc_id, safe="")
    assert "%E2%88%95" in encoded.upper()  # sanity: ∕ encoded, not left raw
    r = client.get(f"/kb/documents?id={encoded}")
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "guide.md"


def test_render_document_with_raw_unencoded_division_slash_in_url():
    """Issue #34 hypothesis: the FE leaves the ∕ raw in the URL and
    the BE silently 404s because of UTF-8/path handling. This test
    confirms BE behaviour either way: it should either accept the raw
    ∕ (httpx encodes it transparently) or expose a clear 404 that the
    FE can surface."""
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"hello", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "guide.md")
    # Pass raw ∕ verbatim (no encoding); TestClient/httpx may auto-encode.
    r = client.get(f"/kb/documents?id={doc_id}")
    assert r.status_code == 200, r.text


def test_render_missing_document_404s():
    client = _client()
    cid = _new_collection(client)
    missing = encode_doc_id(cid, "nope.md")
    assert client.get("/kb/documents", params={"id": missing}).status_code == 404


def test_render_document_carries_metadata_for_the_drawer():
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"# Guide\none two three four", "text/markdown")},
    )
    _drain(client)
    doc_id = encode_doc_id(cid, "guide.md")
    body = client.get("/kb/documents", params={"id": doc_id}).json()
    # the drawer header (meta strip) + download + actions need these
    assert body["document_id"] == doc_id
    assert body["file_id"]  # blob hash → GET /blobs/{file_id}
    assert body["content_type"] in ("text/plain", "text/markdown")
    assert body["size"] > 0
    assert body["chunks"] > 0
    assert body["cited"] == 0
    assert body["created_by"] == "default-user"
    assert body["status"] == "ready"
    assert body["updated_at"] > 0


_MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def test_parse_error_surfaces_status_detail_in_listing_and_render():
    """Issue #39 Q10/Q11: a parser exception flips the doc to
    status=error AND carries a one-line summary in `status_detail`, on
    both the per-collection listing row and the rendered-doc payload —
    the FE shows it next to the status chip so the operator doesn't
    have to grep server logs."""
    client = _client_with_pipeline()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("broken.json", b'{"oops": ', "application/json")},
    )
    _drain(client)
    rows = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    (row,) = [r for r in rows if r["path"] == "broken.json"]
    assert row["status"] == "error"
    assert "invalid JSON" in row["status_detail"]

    doc_id = encode_doc_id(cid, "broken.json")
    body = client.get("/kb/documents", params={"id": doc_id}).json()
    assert body["status"] == "error"
    assert "invalid JSON" in body["status_detail"]


def test_image_upload_is_stored_as_a_renderable_sourcedoc():
    """Issue #41 follow-up: images uploaded alongside markdown docs
    (cross-references like `![pic](./pic.png)`) must round-trip — the
    KB layer used to reject them at ingest. Now they're STORED as
    SourceDocs (so the link rewriter can resolve them) and SKIPPED at
    index time (no parser claims them → chunks=0, status=ready).

    Pipeline mode required: legacy chunker is text-only and would
    skip the image at store time — see ``_client_with_pipeline``."""
    client = _client_with_pipeline()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("diagram.png", _MIN_PNG, "image/png")},
    )
    _drain(client)
    doc_id = encode_doc_id(cid, "diagram.png")
    body = client.get("/kb/documents", params={"id": doc_id}).json()
    assert body["status"] == "ready"
    assert body["chunks"] == 0
    assert body["content_type"] == "image/png"


def test_render_binary_doc_returns_empty_markdown_not_mojibake():
    """Bug (user report 2026-06-06): opening an image SourceDoc in the
    viewer showed the PNG bytes utf-8-decoded into garbage. Binary
    docs (image / pdf / office) must return `markdown: ""` — the FE
    renders an `<img>` from `file_id` for images and a download notice
    for other binaries. Text-like mimes keep the decoded body."""
    client = _client_with_pipeline()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("shot.png", _MIN_PNG, "image/png")},
    )
    doc_id = encode_doc_id(cid, "shot.png")
    body = client.get("/kb/documents", params={"id": doc_id}).json()
    assert body["content_type"] == "image/png"
    assert body["markdown"] == ""  # no decoded bytes shipped to the FE
    assert body["file_id"]  # the FE's <img src=/blobs/{file_id}> handle

    # Text docs keep their body — the whitelist must not over-trigger.
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("note.md", b"# Hello\n\nbody text", "text/markdown")},
    )
    md_id = encode_doc_id(cid, "note.md")
    md_body = client.get("/kb/documents", params={"id": md_id}).json()
    assert "Hello" in md_body["markdown"]


def test_render_document_rewrites_image_link_to_specstar_blob_url():
    """The cross-doc image link `![pic](./diagram.png)` in a markdown
    sibling renders as a specstar `/blobs/{file_id}` URL, so the FE's
    `<img>` tag inlines via the existing blob endpoint (which marks
    the Content-Type) — no new endpoint needed.

    Pipeline mode required: legacy chunker would skip the PNG."""
    client = _client_with_pipeline()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("diagram.png", _MIN_PNG, "image/png")},
    )
    md = b"see ![pic](./diagram.png)"
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", md, "text/markdown")},
    )
    img_doc_id = encode_doc_id(cid, "diagram.png")
    img_body = client.get("/kb/documents", params={"id": img_doc_id}).json()
    file_id = img_body["file_id"]

    md_doc_id = encode_doc_id(cid, "guide.md")
    rendered = client.get("/kb/documents", params={"id": md_doc_id}).json()
    assert f"![pic](/blobs/{file_id})" in rendered["markdown"]


def test_reindex_single_document():
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"# Guide\none two three four", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "guide.md")
    r = client.post("/kb/documents/reindex", params={"id": doc_id})
    assert r.status_code == 200
    assert r.json()["reindexed"] == 1
    _drain(client)
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    match = next(d for d in docs if d["resource_id"] == doc_id)
    assert match["status"] == "ready" and match["chunks"] > 0


def test_delete_document_removes_doc_and_its_chunks():
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"# Guide\none two three four", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "guide.md")
    _drain(client)
    assert client.get("/kb/documents/chunks", params={"id": doc_id}).json()  # chunks exist

    r = client.delete("/kb/documents", params={"id": doc_id})
    assert r.status_code == 200

    assert client.get(f"/kb/collections/{cid}/documents").json()["items"] == []  # doc gone
    assert client.get("/kb/documents", params={"id": doc_id}).status_code == 404
    assert client.get("/kb/documents/chunks", params={"id": doc_id}).json() == []  # cascade


def test_delete_routes_through_the_wiki_unfold_hook():
    """#43: deleting a doc calls the wiki coordinator's un-fold hook BEFORE the
    row is gone, so a deleted source can be scrubbed from the wiki."""
    client = _client()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"# Guide\none two three", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "guide.md")
    _drain(client)

    calls: list[str] = []
    coord = client.app.state.wiki_coordinator  # ty: ignore[unresolved-attribute]
    original = coord.on_doc_deleted

    async def spy(did: str) -> None:
        calls.append(did)
        await original(did)  # still runs the real (gated) hook

    coord.on_doc_deleted = spy
    assert client.delete("/kb/documents", params={"id": doc_id}).status_code == 200
    assert calls == [doc_id]  # the delete route asked the wiki to un-fold this doc


def test_reindex_missing_document_404s():
    client = _client()
    cid = _new_collection(client)
    missing = encode_doc_id(cid, "nope.md")
    assert client.post("/kb/documents/reindex", params={"id": missing}).status_code == 404


def test_delete_missing_document_404s():
    client = _client()
    cid = _new_collection(client)
    missing = encode_doc_id(cid, "nope.md")
    assert client.delete("/kb/documents", params={"id": missing}).status_code == 404


def test_render_document_exposes_preview_file_id():
    """PPTX preview pipeline: when a parser handed back a preview blob
    (soffice-converted PDF), the rendered doc carries its file_id so
    the FE can iframe `/blobs/{preview_file_id}`. Docs without a
    preview ship an empty string."""
    import msgspec
    from specstar.types import Binary

    from workspace_app.kb.li_pipeline import build_doc_pipeline
    from workspace_app.resources.kb import SourceDoc

    spec = make_spec()
    embedder = HashEmbedder(dim=EMBED_DIM)
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=_Runner(),
            kb_embedder=embedder,
            kb_pipeline=build_doc_pipeline(embedder=embedder),
        )
    )
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("note.md", b"# n", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "note.md")
    body = client.get("/kb/documents", params={"id": doc_id}).json()
    assert body["preview_file_id"] == ""

    # Simulate the Ingestor having persisted a parser preview.
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(doc_id).data
    drm.update(
        doc_id,
        msgspec.structs.replace(
            doc, preview=Binary(data=b"%PDF-preview", content_type="application/pdf")
        ),
    )
    body = client.get("/kb/documents", params={"id": doc_id}).json()
    assert body["preview_file_id"]


def test_collection_retrieval_flags_default_and_roundtrip():
    """#50: a collection chooses its retrieval pipeline(s) — chunk-RAG
    (use_rag) and/or the LLM wiki (use_wiki), two independent toggles.
    Defaults: use_rag=True (every existing collection keeps working),
    use_wiki=False. Both round-trip through create + list."""
    client = _client()
    # Defaults when unspecified (back-compat).
    d = client.post("/kb/collections", json={"name": "default-modes"}).json()
    assert d["use_rag"] is True
    assert d["use_wiki"] is False

    # Explicit both-on.
    w = client.post(
        "/kb/collections", json={"name": "wiki-on", "use_rag": True, "use_wiki": True}
    ).json()
    assert w["use_rag"] is True and w["use_wiki"] is True
    listed = client.get("/kb/collections").json()
    match = next(c for c in listed if c["resource_id"] == w["resource_id"])
    assert match["use_wiki"] is True


def test_list_documents_surfaces_scoped_cited_counts():
    # B1: list_documents counts citations for THIS page's docs (a scoped
    # `document_id IN` aggregate), not a global group-by. A doc cited twice
    # shows cited=2; an uncited sibling shows 0.
    from workspace_app.kb.cited import record_citations
    from workspace_app.resources.kb import Citation

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    for path in ("cited.md", "lonely.md"):
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (path, b"# h\none two three four", "text/markdown")},
        )
    _drain(client)

    doc_id = encode_doc_id(cid, "cited.md")
    record_citations(
        spec,
        [
            Citation(
                marker=m,
                collection_id=cid,
                document_id=doc_id,
                filename="cited.md",
                start=0,
                end=1,
                source_chunk_ids=[f"{doc_id}#0"],
            )
            for m in (1, 2)
        ],
        origin_kind="kb_chat",
        origin_id="chat",
        cited_by="u",
    )

    items = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    by_path = {d["path"]: d["cited"] for d in items}
    assert by_path == {"cited.md": 2, "lonely.md": 0}


def _upload_two_chunky_docs(client) -> str:
    cid = _new_collection(client)
    for path in ("a.md", "b.md"):
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (path, b"# h\none two three four five six seven", "text/markdown")},
        )
    _drain(client)
    return cid


def test_list_documents_reports_chunk_counts_per_doc():
    # The listing surfaces each doc's chunk count; a multi-chunk doc reports its
    # real total (cross-checked against a direct per-doc count of DocChunk).
    from workspace_app.resources.kb import DocChunk

    client, spec = _client_and_spec()
    cid = _upload_two_chunky_docs(client)
    chrm = spec.get_resource_manager(DocChunk)
    truth = {
        path: chrm.count_resources((QB["source_doc_id"] == encode_doc_id(cid, path)).build())
        for path in ("a.md", "b.md")
    }
    assert all(v > 0 for v in truth.values())  # the docs really did chunk
    items = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    assert {d["path"]: d["chunks"] for d in items} == truth


def test_list_documents_counts_chunks_via_aggregate_pushdown_not_materialisation(monkeypatch):
    # #103: the documents list tallies each doc's chunks through a scoped
    # `Count` GROUP BY push-down (`doc_chunks_for_ids` → `exp_aggregate_by`),
    # which the store answers as a single COUNT — NOT by streaming every chunk
    # row into Python to add 1. WHY this guard: the counts are identical either
    # way, so a plain behaviour test stays green if a refactor re-introduces the
    # old `DocChunk.search_resources(...)` materialisation loop — but that loop
    # IS the #103 slowness (it loads each chunk's body: text + two embedding
    # vectors). So we pin the MECHANISM: the listing must reach DocChunk through
    # `exp_aggregate_by` (a loop would never call it), and the counts stay right.
    from workspace_app.resources.kb import DocChunk

    client, spec = _client_and_spec()
    cid = _upload_two_chunky_docs(client)

    chrm = spec.get_resource_manager(DocChunk)
    agg_calls = 0
    real = chrm.exp_aggregate_by  # ty: ignore[unresolved-attribute]

    def _spy(*a, **k):
        nonlocal agg_calls
        agg_calls += 1
        return real(*a, **k)

    monkeypatch.setattr(chrm, "exp_aggregate_by", _spy)
    items = client.get(f"/kb/collections/{cid}/documents").json()["items"]

    truth = {
        path: chrm.count_resources((QB["source_doc_id"] == encode_doc_id(cid, path)).build())
        for path in ("a.md", "b.md")
    }
    assert {d["path"]: d["chunks"] for d in items} == truth  # counts correct ...
    assert agg_calls >= 1  # ... reached via the GROUP BY push-down, not a loop.
