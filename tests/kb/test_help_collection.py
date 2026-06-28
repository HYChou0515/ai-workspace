"""#230: the platform "Platform Help" collection — boot seed + access lockdown.

The help collection holds usage docs + release notes seeded from packaged
content at every boot (repo = source of truth). It is public to read/search but
locked to its owner (the seeding system user) + superusers for edits, so the AI
on the /help page can answer over it while normal users can't tamper with it.
"""

from pathlib import Path

from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.help_collection import (
    HELP_COLLECTION_NAME,
    ensure_help_collection,
    help_content_dir,
    seed_help_collection,
    seed_help_collection_best_effort,
)
from workspace_app.kb.ingest import Ingestor
from workspace_app.perm.authorize import Actor, authorize
from workspace_app.resources.kb import EMBED_DIM, Collection, SourceDoc


class _FailingEmbedder:
    """An embedder that mimics a dead backend — raises on every embed."""

    dim: int = EMBED_DIM

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedder down")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedder down")


def _docs_in(spec: SpecStar, cid: str) -> list[SourceDoc]:
    rm = spec.get_resource_manager(SourceDoc)
    return [
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, SourceDoc) and r.data.collection_id == cid
    ]


def _collections_named(spec: SpecStar, name: str) -> list[Collection]:
    rm = spec.get_resource_manager(Collection)
    return [
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Collection) and r.data.name == name
    ]


def _write_help(dirpath: Path) -> Path:
    (dirpath / "getting-started.md").write_text("# Getting started\nopen an app to begin")
    (dirpath / "CHANGELOG.md").write_text("# Changelog\n## v1\nthe first release")
    return dirpath


def test_seed_creates_collection_and_ingests_docs(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder, tmp_path: Path
):
    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)

    cid = seed_help_collection(spec, ingestor, user="sys", content_dir=_write_help(tmp_path))

    coll = spec.get_resource_manager(Collection).get(cid).data
    assert isinstance(coll, Collection)
    assert coll.name == HELP_COLLECTION_NAME
    assert {d.path for d in _docs_in(spec, cid)} == {"getting-started.md", "CHANGELOG.md"}


def test_reseeding_is_idempotent(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder, tmp_path: Path
):
    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)
    _write_help(tmp_path)

    first = seed_help_collection(spec, ingestor, content_dir=tmp_path)
    docs_before = {(d.path, d.content.file_id) for d in _docs_in(spec, first)}

    second = seed_help_collection(spec, ingestor, content_dir=tmp_path)

    assert second == first  # same collection, not a duplicate
    assert len(_collections_named(spec, HELP_COLLECTION_NAME)) == 1  # no duplicate
    # identical bytes → no new / changed SourceDocs
    assert {(d.path, d.content.file_id) for d in _docs_in(spec, second)} == docs_before


def test_help_collection_is_public_to_read_but_locked_to_edit(spec: SpecStar):
    cid = ensure_help_collection(spec, owner="system")
    rev = spec.get_resource_manager(Collection).get(cid)
    perm = rev.data.permission
    owner = rev.info.created_by
    assert owner == "system"

    reader = Actor.human("nobody")
    admins = frozenset({"boss"})
    # anyone may read + converse (public visibility)
    assert authorize(reader, "read_content", perm, created_by=owner) is True
    assert authorize(reader, "converse", perm, created_by=owner) is True
    # but a normal user may NOT edit / add / change meta
    assert authorize(reader, "edit_content", perm, created_by=owner) is False
    assert authorize(reader, "add_content", perm, created_by=owner) is False
    assert authorize(reader, "write_meta", perm, created_by=owner) is False
    # the owner (system) and superusers (admins) may edit
    assert authorize(Actor.human("system"), "edit_content", perm, created_by=owner) is True
    assert (
        authorize(Actor.human("boss"), "edit_content", perm, created_by=owner, superusers=admins)
        is True
    )


def test_packaged_content_seeds_by_default(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # The packaged help content ships with the wheel, so the default seed (no
    # content_dir) always has something to ingest — at minimum the changelog.
    files = {p.name for p in help_content_dir().glob("*.md")}
    assert "CHANGELOG.md" in files

    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)
    cid = seed_help_collection(spec, ingestor, user="system")

    assert {d.path for d in _docs_in(spec, cid)} == files


def test_best_effort_seed_ingests_on_happy_path(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)
    cid = seed_help_collection_best_effort(spec, ingestor)
    assert _docs_in(spec, cid)  # docs got ingested


def test_best_effort_seed_survives_a_dead_embedder(spec: SpecStar, chunker: FixedTokenChunker):
    # A dead embedder must NOT block boot: the collection is still created
    # (readable), the failure is swallowed, and an id is returned.
    ingestor = Ingestor(spec, chunker=chunker, embedder=_FailingEmbedder())

    cid = seed_help_collection_best_effort(spec, ingestor)

    coll = spec.get_resource_manager(Collection).get(cid).data
    assert isinstance(coll, Collection)
    assert coll.name == HELP_COLLECTION_NAME
