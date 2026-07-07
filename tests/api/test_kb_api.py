from collections.abc import AsyncIterator

from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, DocChunk, SourceDoc
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


def test_collection_lists_its_parser_guidance_after_a_patch():
    """#328: the per-collection parser_guidance round-trips through the card list,
    so the findability modal can prefill the editor. Apply writes it via the same
    native PATCH /collection/{id}."""
    client = _client()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    fresh = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert fresh["parser_guidance"] == ""

    r = client.patch(
        f"/collection/{cid}",
        json={"parser_guidance": "If you see a fishbone diagram, emit JSON."},
    )
    assert r.status_code < 300, r.text

    got = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert got["parser_guidance"] == "If you see a fishbone diagram, emit JSON."


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


def test_upload_encrypted_office_is_rejected_with_422_and_stores_nothing():
    """#325: an encrypted .pptx (OLE2 container, not ZIP) is refused at
    upload with a structured 422 — no doc is created, so the FE can show
    'decrypt and re-upload' instead of a cryptic background-index error."""
    client = _client()
    cid = _new_collection(client)
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"the encrypted blob"
    files = {"file": ("deck.pptx", ole2, "application/octet-stream")}
    r = client.post(f"/kb/collections/{cid}/documents", files=files)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["check_id"] == "office_encryption"
    assert detail["reason_code"] == "encrypted_office"
    assert detail["message_key"] == "kb.upload.blocked.unreadable"
    # Nothing persisted.
    assert client.get(f"/kb/collections/{cid}/documents").json()["items"] == []


def test_findability_probe_reports_before_and_after_ranks():
    """#328: POST /kb/findability/probe ranks a doc's content for a question
    (before) and, when a candidate guidance is given, re-parses the doc (dry-run,
    Overlay) and ranks the result (after) — read-only, typed response."""
    client = _client_with_pipeline()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    for name, body in [
        ("a.md", b"solder void root cause analysis report"),
        ("b.md", b"an unrelated note about reflow ovens"),
    ]:
        client.post(
            f"/kb/collections/{cid}/documents", files={"file": (name, body, "text/markdown")}
        )
    _drain(client)

    doc_id = encode_doc_id(cid, "a.md")
    r = client.post(
        "/kb/findability/probe",
        json={"doc_id": doc_id, "question": "solder void", "guidance": "focus on solder void"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["top_k"] == 5
    assert isinstance(data["before"]["passages"], list)
    assert data["after"] is not None  # a candidate guidance was supplied
    # a.md carries the query terms → it surfaces in the ranking.
    assert data["before"]["best_rank"] is not None
    assert data["before"]["passages"][0]["rank"] == data["before"]["best_rank"]


def test_findability_probe_without_guidance_omits_after():
    """No candidate guidance ⇒ the probe only reports current ranks (`after` is
    null) — a pure 'where does this doc land today' read."""
    client = _client_with_pipeline()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"solder void root cause", "text/markdown")},
    )
    _drain(client)
    r = client.post(
        "/kb/findability/probe",
        json={"doc_id": encode_doc_id(cid, "a.md"), "question": "solder void"},
    )
    assert r.status_code == 200
    assert r.json()["after"] is None


def test_findability_probe_k_flows_through_to_top_k():
    """#356: the modal's k (slider) is echoed as the response's top_k — the cutoff
    the FE highlights against."""
    client = _client_with_pipeline()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"solder void root cause", "text/markdown")},
    )
    _drain(client)
    r = client.post(
        "/kb/findability/probe",
        json={"doc_id": encode_doc_id(cid, "a.md"), "question": "solder void", "k": 12},
    )
    assert r.status_code == 200
    assert r.json()["top_k"] == 12


def test_findability_probe_404_for_unknown_doc():
    client = _client_with_pipeline()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    r = client.post(
        "/kb/findability/probe",
        json={"doc_id": encode_doc_id(cid, "nope.md"), "question": "x"},
    )
    assert r.status_code == 404


class _FakeAnswerLlm(ILlm):
    """Streams a canned answer word-by-word and records the prompt it received."""

    def __init__(self, reply: str = "Grounded answer [1].") -> None:
        self.prompts: list[str] = []
        self._reply = reply

    def stream(self, prompt: str):
        self.prompts.append(prompt)
        for tok in self._reply.split(" "):
            yield tok + " ", False


class _BoomLlm(ILlm):
    def stream(self, prompt: str):
        raise RuntimeError("boom")
        yield  # pragma: no cover


def _client_with_answer_llm(llm: ILlm) -> TestClient:
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
        answer_llm=llm,
    )
    return TestClient(app)


def _sse_answer_text(raw: str) -> str:
    """Join the streamed `message_delta` texts back into the full answer (each
    chunk arrives as its own SSE frame)."""
    import json

    out: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        ev = json.loads(line[len("data:") :].strip())
        if ev.get("type") == "message_delta" and not ev.get("reasoning"):
            out.append(ev["text"])
    return "".join(out)


def _seed_doc(client: TestClient, body: bytes) -> str:
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", body, "text/markdown")},
    )
    _drain(client)
    return encode_doc_id(cid, "a.md")


def test_findability_answer_streams_a_grounded_answer():
    """#356: POST /kb/findability/answer streams the answer the question gets from
    ONLY this doc's top-k passages — and those passages reach the LLM prompt."""
    llm = _FakeAnswerLlm("Voids form from flux outgassing [1].")
    client = _client_with_answer_llm(llm)
    doc_id = _seed_doc(client, b"solder void root cause: flux outgassing during reflow")

    r = client.post(
        "/kb/findability/answer",
        json={"doc_id": doc_id, "question": "why do voids form?", "k": 5},
    )
    assert r.status_code == 200
    body = r.text
    assert "message_delta" in body and "done" in body  # streamed live
    assert "Voids form from flux outgassing" in _sse_answer_text(body)
    # the doc's own passage text was handed to the answerer (fixed context).
    assert llm.prompts and "flux outgassing during reflow" in llm.prompts[0]


def test_findability_answer_after_guidance_reparses_then_answers():
    """A candidate guidance ⇒ the doc is re-parsed (dry-run Overlay) before the
    top-k is taken — the After box's answer."""
    llm = _FakeAnswerLlm("After answer [1].")
    client = _client_with_answer_llm(llm)
    doc_id = _seed_doc(client, b"solder void root cause analysis")

    r = client.post(
        "/kb/findability/answer",
        json={"doc_id": doc_id, "question": "voids?", "k": 5, "guidance": "focus on voids"},
    )
    assert r.status_code == 200
    assert "After answer" in _sse_answer_text(r.text) and "done" in r.text


def test_findability_answer_surfaces_llm_error_in_the_stream():
    """A failure during answering is surfaced as an `error` event in the stream,
    not a 500 — the modal shows it inline."""
    client = _client_with_answer_llm(_BoomLlm())
    doc_id = _seed_doc(client, b"anything")

    r = client.post(
        "/kb/findability/answer",
        json={"doc_id": doc_id, "question": "q", "k": 5},
    )
    assert r.status_code == 200
    body = r.text
    assert "error" in body and "boom" in body


def test_document_guidance_write_and_render_roundtrip():
    """#356: POST /kb/documents/guidance persists a doc's per-doc override and the
    rendered doc echoes it (the modal prefills its editor from this); empty clears."""
    client = _client_with_pipeline()
    doc_id = _seed_doc(client, b"solder void root cause")

    # default: no override
    assert client.get(f"/kb/documents?id={doc_id}").json()["parser_guidance_override"] == ""

    # save a per-doc override
    r = client.post(
        f"/kb/documents/guidance?id={doc_id}", json={"guidance": "treat tables as JSON"}
    )
    assert r.status_code == 200
    assert r.json()["parser_guidance_override"] == "treat tables as JSON"
    assert (
        client.get(f"/kb/documents?id={doc_id}").json()["parser_guidance_override"]
        == "treat tables as JSON"
    )

    # clear it
    client.post(f"/kb/documents/guidance?id={doc_id}", json={"guidance": ""})
    assert client.get(f"/kb/documents?id={doc_id}").json()["parser_guidance_override"] == ""


def test_document_guidance_404_for_unknown_doc():
    client = _client_with_pipeline()
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    r = client.post(
        f"/kb/documents/guidance?id={encode_doc_id(cid, 'nope.md')}", json={"guidance": "x"}
    )
    assert r.status_code == 404


def test_findability_answer_404_for_unknown_doc():
    client = _client_with_answer_llm(_FakeAnswerLlm())
    cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    r = client.post(
        "/kb/findability/answer",
        json={"doc_id": encode_doc_id(cid, "nope.md"), "question": "x"},
    )
    assert r.status_code == 404


def test_upload_checks_endpoint_lists_browser_runnable_hints():
    """#325: the FE fetches these to pre-block encrypted Office files in the
    browser. Server-only checks (PDF) are not listed."""
    client = _client()
    hints = client.get("/kb/upload-checks").json()
    by_id = {h["id"]: h for h in hints}
    assert "pdf_encryption" not in by_id  # server-only, no browser rule
    office = by_id["office_encryption"]
    assert set(office["extensions"]) == {".pptx", ".xlsx", ".docx"}
    assert office["forbid_magic_hex"] == ["d0cf11e0a1b11ae1"]
    assert office["message_key"] == "kb.upload.blocked.unreadable"


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
    # quality badge + sort. Un-scored docs report null (neutral). #395: the
    # rationale intentionally does NOT ride the row any more — it is shown only
    # for the opened doc, whose `render_document` response carries it (covered
    # by test_render_document_exposes_quality_rationale_and_breakdown).
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    _upload(client, cid, "a.md")
    _drain(client)
    doc_id = encode_doc_id(cid, "a.md")
    row = next(d for d in client.get(f"/kb/collections/{cid}/documents").json()["items"])
    assert row["quality_score"] is None  # un-scored = neutral
    _set_quality(spec, doc_id, score=73, rationale="Clear and complete.")
    row = next(d for d in client.get(f"/kb/collections/{cid}/documents").json()["items"])
    assert row["quality_score"] == 73
    assert "quality_rationale" not in row


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


def test_render_document_returns_structured_text_verbatim_without_link_rewrite():
    """#361: structured docs (csv / json / …) come back as verbatim text — the
    FE builds the tree/grid client-side — and a markdown-link-looking value is
    NOT rewritten (the viewer renders a tree/grid, not markdown)."""
    from specstar.types import Binary

    from workspace_app.resources import SourceDoc

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    drm = spec.get_resource_manager(SourceDoc)
    # Created directly (not via _upload) so the assertion tests render_document's
    # per-type projection, NOT ingestion: the fixture's text-only ingest + the
    # libmagic mime sniff vary by env, but a structured doc must always render
    # verbatim. A value that looks like a markdown link must survive byte-for-byte.
    cases = [
        ("data.csv", b"path,note\nx,[docs](kb://doc/other)\n", "text/csv"),
        ("config.json", b'{"see": "[docs](kb://doc/other)"}', "application/json"),
    ]
    for path, body, ct in cases:
        doc_id = encode_doc_id(cid, path)
        drm.create(
            SourceDoc(
                collection_id=cid,
                path=path,
                content=Binary(data=body, content_type=ct),
                status="ready",
            ),
            resource_id=doc_id,
        )
        rd = client.get(f"/kb/documents?id={doc_id}").json()
        assert rd["markdown"] == body.decode()


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
    # Distinct bodies so both are real chunky docs (not #104 aliases) — the point
    # here is that reindex rebuilds EVERY doc, so each must own chunks.
    for name in ("a.md", "b.md"):
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (name, f"# {name} one two three four".encode(), "text/markdown")},
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


def test_deleting_one_holder_of_shared_content_keeps_it_at_the_surviving_path():
    # #104: two paths share ONE content chunk set. Deleting one path must NOT
    # delete the content — a collection-scoped refcount leaves the shared set for
    # the surviving sibling, which serves it by file_id (no re-home). Both paths
    # surface the SAME content-addressed chunks through the chunk view.
    client = _client()
    cid = _new_collection(client)
    body = b"# Deck\nalpha beta gamma delta epsilon zeta"
    for name in ("wk1/report.md", "wk2/report.md"):
        client.post(
            f"/kb/collections/{cid}/documents", files={"file": (name, body, "text/markdown")}
        )
    _drain(client)
    a, b = encode_doc_id(cid, "wk1/report.md"), encode_doc_id(cid, "wk2/report.md")

    def _chunk_ids(did):
        return [
            c["chunk_id"] for c in client.get("/kb/documents/chunks", params={"id": did}).json()
        ]

    # both paths surface the same shared content chunk set (content-addressed)
    assert _chunk_ids(a) and _chunk_ids(a) == _chunk_ids(b)

    assert client.delete("/kb/documents", params={"id": a}).status_code == 200

    assert client.get("/kb/documents", params={"id": a}).status_code == 404  # deleted path gone
    assert _chunk_ids(b)  # the surviving path still serves the shared content (no re-home)


def test_render_document_reports_the_content_chunk_count_for_a_dedup_alias():
    # #104: a dedup alias owns 0 chunks (it shares the canonical's content set), so
    # counting by source_doc_id shows 0 — but the document LIST already counts by
    # content. render_document must agree, or the same doc reads N in the list and
    # 0 in its detail view. Both must report the shared content's chunk count.
    client = _client()
    cid = _new_collection(client)
    body = b"# Deck\nalpha beta gamma delta epsilon zeta eta theta"
    for name in ("wk1/report.md", "wk2/report.md"):
        client.post(
            f"/kb/collections/{cid}/documents", files={"file": (name, body, "text/markdown")}
        )
    _drain(client)
    canon, alias = encode_doc_id(cid, "wk1/report.md"), encode_doc_id(cid, "wk2/report.md")

    n = client.get("/kb/documents", params={"id": canon}).json()["chunks"]
    assert n > 0
    # the alias's detail view reports the SAME content chunk count, not its own 0
    assert client.get("/kb/documents", params={"id": alias}).json()["chunks"] == n


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
    # Distinct bodies per path: #104 dedups byte-identical content into ONE chunk
    # set, but these tests want two docs that each independently chunk.
    for path in ("a.md", "b.md"):
        body = f"# h {path}\none two three four five six seven".encode()
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": (path, body, "text/markdown")},
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


def test_list_documents_serves_rows_from_metas_never_the_data_blobs(monkeypatch):
    # #395: the documents list is a METAS-ONLY read — one indexed search whose
    # `indexed_data` already carries every rendered field. WHY this guard: the
    # rows look identical if a refactor re-introduces `list_resources`, but
    # that path fetches every row's full data blob (the multi-KB extracted
    # `text` included) one SELECT at a time — which IS the #395 slowness. So we
    # pin the MECHANISM: the SourceDoc manager may only be reached through
    # `search_resources` / `count_resources`; any data-blob read blows up. The
    # same applies to the per-doc `IndexRunStore.get` point-reads the old row
    # loop made for indexing docs (Batch A replaced them with one
    # collection-scoped metas search).
    from workspace_app.kb.index_run import IndexRunStore

    client, spec = _client_and_spec()
    cid = _upload_two_chunky_docs(client)

    drm = spec.get_resource_manager(SourceDoc)
    searches = 0
    real_search = drm.search_resources

    def _spy(*a, **k):
        nonlocal searches
        searches += 1
        return real_search(*a, **k)

    def _boom(*a, **k):
        raise AssertionError("the documents list touched the data table")

    monkeypatch.setattr(drm, "search_resources", _spy)
    for blob_read in ("list_resources", "get_partial", "get_resource_revision", "get"):
        monkeypatch.setattr(drm, blob_read, _boom)
    monkeypatch.setattr(IndexRunStore, "get", _boom)

    items = client.get(f"/kb/collections/{cid}/documents").json()["items"]

    assert searches >= 1  # served from the meta index ...
    by_path = {d["path"]: d for d in items}
    assert set(by_path) == {"a.md", "b.md"}  # ... with the rows still whole:
    for row in by_path.values():
        assert row["status"] == "ready"
        assert row["content_type"].startswith("text/")  # the sniffed MIME rides the index
        assert row["file_id"]  # sibling-image/download URLs build from the row (#87)
        assert row["size"] > 0
        assert row["chunks"] > 0


def test_list_documents_shows_pre_migrate_rows_as_ready(tmp_path):
    # #395: a doc written before the v6 backfill has no `status` in its
    # indexed_data. The list surfaces it as "ready" (the overwhelmingly common
    # terminal state for old rows) rather than crashing or hiding it — the
    # operator closes the window with POST /source-doc/migrate/execute.
    from specstar import BackendBinding, BackendConfig, ConnectionProfile
    from specstar.types import Binary, IndexableField

    from workspace_app.resources.kb import Collection

    backend = BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(tmp_path)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )
    # Pre-#395 registration: the v5-era index set (no status/status_detail/...).
    old = SpecStar()
    old.configure(default_user="u", backend=backend)
    old.add_model(Collection)
    old.add_model(
        SourceDoc,
        indexed_fields=[
            "collection_id",
            IndexableField("content.size", int, index_key="content_size"),
            IndexableField("path", str),
        ],
    )
    cid = old.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    old.get_resource_manager(SourceDoc).create(
        SourceDoc(
            collection_id=cid,
            path="old.md",
            content=Binary(data=b"# old", content_type="text/markdown"),
            status="error",  # stored on the blob, but invisible to the meta index
            status_detail="boom",
        )
    )

    app = create_app(
        spec=make_spec(backend=backend),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    row = TestClient(app).get(f"/kb/collections/{cid}/documents").json()["items"][0]
    assert row["path"] == "old.md"
    assert row["status"] == "ready"  # the documented pre-backfill window
    assert row["status_detail"] == ""


def test_list_documents_accepts_a_fetch_all_sized_limit():
    # #395: the doc IDE fetches the whole collection in ONE request; the old
    # `le=500` cap forced ⌈N/200⌉ serial round-trips.
    client = _client()
    cid = _new_collection(client)
    r = client.get(f"/kb/collections/{cid}/documents?limit=2000")
    assert r.status_code == 200


def test_documents_status_reports_counts_progress_and_change_stamp():
    # #395: the FE's indexing poll ticks this few-hundred-byte summary instead
    # of refetching the whole document list every 1.5s: per-status counts (the
    # "did anything flip?" signal), the in-flight runs' unit progress (merged
    # into rows client-side, no list refetch per tick), and a change stamp.
    from specstar.types import Binary

    from workspace_app.kb.index_run import IndexRunStore

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    _upload(client, cid, "a.md")
    _drain(client)  # → one "ready" doc

    drm = spec.get_resource_manager(SourceDoc)
    doc_id = drm.create(
        SourceDoc(collection_id=cid, path="big.pdf", content=Binary(data=b"x"), status="indexing")
    ).resource_id
    runs = IndexRunStore(spec)
    runs.start(doc_id, cid, total=3, units_total=24)
    runs.mark_done(doc_id, 0, batch_units=8)

    s = client.get(f"/kb/collections/{cid}/documents/status").json()
    assert s["total"] == 2
    assert s["counts"] == {"ready": 1, "indexing": 1}
    assert s["runs"] == {doc_id: {"units_done": 8, "units_total": 24}}
    assert s["latest_ms"] > 0


def test_documents_status_of_an_empty_collection_is_all_zeroes():
    client = _client()
    cid = _new_collection(client)
    s = client.get(f"/kb/collections/{cid}/documents/status").json()
    assert s == {"total": 0, "counts": {}, "runs": {}, "latest_ms": 0}


def test_indexed_data_narrowing_degrades_missing_values():
    # #395: rows persisted before an index existed miss keys (or the whole
    # JSONB column) — every reader degrades field-by-field instead of crashing.
    from workspace_app.api.kb_routes import _indexed_of, _opt_int

    class _PreColumnMeta:  # indexed_data predates the meta store's column
        indexed_data = None

    assert _indexed_of(_PreColumnMeta()) == {}
    assert _indexed_of(object()) == {}  # UNSET attribute
    assert _opt_int(7) == 7
    assert _opt_int(None) is None
    assert _opt_int("7") is None
    assert _opt_int(True) is None  # bool is an int subclass — not a count


def test_documents_status_is_a_metas_only_aggregate(monkeypatch):
    # #395: same mechanism pin as the list — the poll target must stay a
    # GROUP BY push-down + metas search; a data-blob read (or a
    # materialise-then-tally loop, which would never call exp_aggregate_by)
    # blows up / fails the positive assertion.
    client, spec = _client_and_spec()
    cid = _upload_two_chunky_docs(client)

    drm = spec.get_resource_manager(SourceDoc)
    agg_calls = 0
    real = drm.exp_aggregate_by  # ty: ignore[unresolved-attribute]

    def _spy(*a, **k):
        nonlocal agg_calls
        agg_calls += 1
        return real(*a, **k)

    def _boom(*a, **k):
        raise AssertionError("documents/status touched the data table")

    monkeypatch.setattr(drm, "exp_aggregate_by", _spy)
    for blob_read in ("list_resources", "get_partial", "get_resource_revision", "get"):
        monkeypatch.setattr(drm, blob_read, _boom)

    s = client.get(f"/kb/collections/{cid}/documents/status").json()
    assert agg_calls == 1
    assert s["counts"] == {"ready": 2}
    assert s["total"] == 2


def _chunk_ids(spec: SpecStar, doc_id: str) -> set[str]:
    rm = spec.get_resource_manager(DocChunk)
    return {
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())
    }


def test_upload_same_bytes_new_path_is_deduped_without_a_reindex():
    # #390 + #104: once content is indexed, the SAME bytes at a DIFFERENT path are
    # handled synchronously on the request — but #104 now DEDUPS instead of copying
    # a second chunk set. The new doc is "ready" as an ALIAS (0 own chunks, text
    # carried) BEFORE any background consumer runs; the canonical keeps the chunks.
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    body = b"# Doc\nalpha beta gamma delta epsilon"
    client.post(f"/kb/collections/{cid}/documents", files={"file": ("a.md", body, "text/markdown")})
    _drain(client)  # first index completes → its result is cached

    client.post(f"/kb/collections/{cid}/documents", files={"file": ("b.md", body, "text/markdown")})
    # NO _drain: the dedup fast-path aliased on the request thread.
    doc1_id, doc2_id = encode_doc_id(cid, "a.md"), encode_doc_id(cid, "b.md")
    doc2 = spec.get_resource_manager(SourceDoc).get(doc2_id).data
    assert isinstance(doc2, SourceDoc)
    assert doc2.status == "ready"  # ready without a background index
    assert doc2.text == body.decode()  # alias carries the extracted text
    assert _chunk_ids(spec, doc2_id) == set()  # deduped: no duplicate chunk set
    assert _chunk_ids(spec, doc1_id)  # the canonical still owns the shared chunks


def test_move_document_is_served_from_cache_without_a_reindex():
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    body = b"# A\nmovable body one two three"
    client.post(f"/kb/collections/{cid}/documents", files={"file": ("a.md", body, "text/markdown")})
    old_id = encode_doc_id(cid, "a.md")
    _drain(client)  # index + cache

    r = client.post(f"/kb/documents/move?id={old_id}&to=b.md")
    assert r.status_code == 200
    new_id = encode_doc_id(cid, "b.md")
    # NO _drain: the move reused the cache synchronously.
    doc = spec.get_resource_manager(SourceDoc).get(new_id).data
    assert isinstance(doc, SourceDoc)
    assert doc.status == "ready"
    assert _chunk_ids(spec, new_id)


def test_upload_of_new_content_still_enqueues_a_real_index():
    # Cache MISS path is unaffected: brand-new bytes go through the queue and are
    # only "ready" after the background consumer drains.
    client, spec = _client_and_spec()
    cid = _new_collection(client)
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("fresh.md", b"never seen before content", "text/markdown")},
    )
    doc_id = encode_doc_id(cid, "fresh.md")
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "indexing"
    _drain(client)
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "ready"


def test_reindex_document_invalidates_the_cache_then_repopulates():
    # #390: reindex is the "force recompute" path — it drops the cached result so
    # the rebuild misses, then the recompute repopulates it.
    from workspace_app.kb.index_cache import IndexCacheStore

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    body = b"reindexable body one two three four"
    client.post(f"/kb/collections/{cid}/documents", files={"file": ("a.md", body, "text/markdown")})
    _drain(client)
    doc_id = encode_doc_id(cid, "a.md")
    ingestor = client.app.state.index_coordinator._ingestor  # noqa: SLF001  # ty: ignore[unresolved-attribute]
    key = ingestor.cache_key(doc_id)
    store = IndexCacheStore(spec)
    assert store.get(key) is not None  # cached after the first index

    r = client.post(f"/kb/documents/reindex?id={doc_id}")
    assert r.status_code == 200
    assert store.get(key) is None  # force path dropped the entry (recompute enqueued)

    _drain(client)
    assert store.get(key) is not None  # recompute repopulated it


def test_reindex_collection_invalidates_the_cache():
    from workspace_app.kb.index_cache import IndexCacheStore

    client, spec = _client_and_spec()
    cid = _new_collection(client)
    body = b"collection reindex body alpha beta gamma"
    client.post(f"/kb/collections/{cid}/documents", files={"file": ("a.md", body, "text/markdown")})
    _drain(client)
    doc_id = encode_doc_id(cid, "a.md")
    ingestor = client.app.state.index_coordinator._ingestor  # noqa: SLF001  # ty: ignore[unresolved-attribute]
    key = ingestor.cache_key(doc_id)
    store = IndexCacheStore(spec)
    assert store.get(key) is not None

    r = client.post(f"/kb/collections/{cid}/reindex")
    assert r.status_code == 200
    assert store.get(key) is None  # collection reindex dropped it too
