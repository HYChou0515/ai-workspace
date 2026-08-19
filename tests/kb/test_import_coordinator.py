"""#715: importing a large archive must not hold the HTTP request open.

The synchronous path writes every document before it answers, which is right for
restoring a backup you exported yourself — a person clicks it and waits a few
seconds. It is wrong for a machine pushing a prepared knowledge base: a 207 MB
archive spent ten minutes in it and came back 504. These cover the asynchronous
contract that replaces it for that use: stage the archive, return a run id, do
the writing on a worker.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.import_jobs import ImportCoordinator, ImportRun
from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.index_jobs import IndexJob
from workspace_app.kb.ingest import Ingestor
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection, ContextCard, SourceDoc

MANIFEST_PATH = ".kb-collection/manifest.json"


def _archive(members: dict[str, bytes], cards: list[dict] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in members.items():
            zf.writestr(path, data)
        zf.writestr(
            MANIFEST_PATH,
            json.dumps(
                {"version": 1, "collection": {"name": "archived"}, "context_cards": cards or []},
                ensure_ascii=False,
            ),
        )
    return buf.getvalue()


def _coordinator(spec: SpecStar) -> ImportCoordinator:
    """The real IndexCoordinator, never consumed.

    A hand-written stub here would only ever prove "we called enqueue", which
    survives any change to what enqueueing actually requires. The real one leaves
    IndexJob rows behind, so the assertion is about the queue that exists rather
    than about our own call."""
    ing = Ingestor(
        spec, chunker=FixedTokenChunker(max_tokens=64), embedder=HashEmbedder(dim=EMBED_DIM)
    )
    return ImportCoordinator(
        spec, ingestor=ing, index_coordinator=IndexCoordinator(spec, ing)
    )


def _index_jobs(spec: SpecStar) -> int:
    return spec.get_resource_manager(IndexJob).count_resources()


def _collection(spec: SpecStar, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _docs(spec: SpecStar, cid: str) -> list[SourceDoc]:
    rm = spec.get_resource_manager(SourceDoc)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, SourceDoc)
        out.append(r.data)
    return out


def _cards(spec: SpecStar, cid: str) -> list[ContextCard]:
    rm = spec.get_resource_manager(ContextCard)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ContextCard)
        out.append(r.data)
    return out


def _run_of(spec: SpecStar, run_id: str) -> ImportRun:
    data = spec.get_resource_manager(ImportRun).get(run_id).data
    assert isinstance(data, ImportRun)
    return data


async def test_enqueue_returns_before_any_document_is_written():
    """The whole point: the caller gets an id back while the archive is still only
    staged. If enqueue wrote the documents itself we would have moved the
    ten-minute wait rather than removed it."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta"}),
        mode="overwrite",
        user="u",
    )

    assert run_id
    assert _docs(spec, cid) == []  # nothing written yet — that is the contract
    assert _run_of(spec, run_id).collection_id == cid


async def test_draining_the_queue_lands_the_documents_and_the_cards():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    zip_data = _archive(
        {"a.md": b"alpha", "b.md": b"beta"},
        cards=[{"keys": ["M4"], "title": "M4", "body": "edge", "reference_paths": ["a.md"]}],
    )

    coord.enqueue(collection_id=cid, zip_data=zip_data, mode="overwrite", user="u")
    await coord.aclose()

    assert sorted(d.path for d in _docs(spec, cid)) == ["a.md", "b.md"]
    assert [c.keys for c in _cards(spec, cid)] == [["M4"]]
    assert _index_jobs(spec) == 2  # each restored document is queued for indexing


async def test_the_run_reports_what_landed_and_what_did_not():
    """A caller who cannot watch the request needs the outcome per document, not
    just "finished" — a half-applied import has to be visible as one."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta"}),
        mode="overwrite",
        user="u",
    )
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert run.members == 2  # documents the archive held
    assert run.written == 2  # documents that landed
    assert run.errors == []
    assert run.finished is True


async def test_the_staged_archive_is_released_once_the_import_finishes():
    """A 200 MB blob per import is not something to keep after it is unpacked. It
    is held only while a retry could still need it."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive({"a.md": b"alpha"}), mode="overwrite", user="u"
    )
    await coord.aclose()

    assert _run_of(spec, run_id).archive is None


@pytest.mark.parametrize("mode", ["overwrite", "skip"])
async def test_mode_reaches_the_worker(mode: str):
    """`mode` is chosen by the caller at enqueue time but applied by the worker, so
    it has to survive the trip on the run rather than living in the request."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    spec.get_resource_manager(ContextCard).create(
        ContextCard(collection_id=cid, keys=["M4"], norm_keys=["m4"], title="kept", body="orig")
    )
    coord = _coordinator(spec)

    coord.enqueue(
        collection_id=cid,
        zip_data=_archive({}, cards=[{"keys": ["M4"], "title": "new", "body": "new"}]),
        mode=mode,
        user="u",
    )
    await coord.aclose()

    (card,) = _cards(spec, cid)
    assert card.body == ("new" if mode == "overwrite" else "orig")


async def test_one_unreadable_document_does_not_take_its_batch_down_with_it(monkeypatch):
    """A batch is a scheduling unit, not a transaction. If one member fails, the
    rest of its batch must still land and the run must name the member that did
    not — "batch 3 failed" is not something anyone can act on."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    real = coord._ingestor.store_file

    def flaky(*, collection_id, user, path, data):
        if path == "b.md":
            raise OSError("disk said no")
        return real(collection_id=collection_id, user=user, path=path, data=data)

    monkeypatch.setattr(coord._ingestor, "store_file", flaky)

    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta", "c.md": b"gamma"}),
        mode="overwrite",
        user="u",
    )
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert sorted(d.path for d in _docs(spec, cid)) == ["a.md", "c.md"]
    assert run.written == 2
    assert len(run.errors) == 1
    assert run.errors[0].startswith("b.md: OSError")
    assert run.finished is True  # a partial import still finishes — and says so
