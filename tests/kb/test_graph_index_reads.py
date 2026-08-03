"""The vocabulary pass reads the INDEX, not the stored rows.

Paging (see ``test_graph_bounded_reads``) stops the reconcile asking for a whole
table in one statement, but it still asked for every mention's stored blob and
decoded it. That is the expensive half: a listing materialises each row through
the resource store, so the pass paid one decode per mention — and it did that
twice more, because ``_link`` re-fetched each mention just to read the one field
it needed.

Everything the pass actually reads about a mention is a scalar it groups or
displays, so all of it can live in ``indexed_data`` and arrive with the metadata
search. Then the walk is a scan and the blobs are never touched.

Rows written before those fields were indexed do not carry them, and specstar
does not backfill on its own — so the pass reads what the index has and fetches
the blob only for the rows that predate it. That fallback is what makes the
change safe to deploy before the migrate is run, and it costs nothing once it
has been.
"""

from __future__ import annotations

import pytest
from specstar import QB, SpecStar

from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphEntity, GraphMention, mention_id
from workspace_app.resources.kb import Collection

MENTIONS = 12


@pytest.fixture
def blob_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every mention row materialised through the resource store, recorded.

    Counting the reads is the only way to state the property: the pass produces
    exactly the same vocabulary either way, so an assertion about its OUTPUT
    would pass just as happily on the version that decodes every blob.
    """
    from specstar.resource_manager.core import ResourceManager

    seen: list[str] = []
    real_list = ResourceManager.list_resources
    real_get = ResourceManager.get

    def listing(self, query=None, *args, **kwargs):  # noqa: ANN001, ANN202
        rows = real_list(self, query, *args, **kwargs)
        if self.resource_name == "graph-mention":
            for row in rows:
                seen.append(row.info.resource_id)  # ty: ignore[unresolved-attribute]
        return rows

    def one(self, resource_id, *args, **kwargs):  # noqa: ANN001, ANN202
        if self.resource_name == "graph-mention":
            seen.append(resource_id)
        return real_get(self, resource_id, *args, **kwargs)

    monkeypatch.setattr(ResourceManager, "list_resources", listing)
    monkeypatch.setattr(ResourceManager, "get", one)
    return seen


def _corpus(spec: SpecStar) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        for i in range(MENTIONS):
            # Two documents write the same name differently, so the pass has a
            # display name to choose and a kind to resolve — the two things it
            # needs the raw surfaces for.
            surface = f"Reflow Oven {i // 2}" if i % 2 else f"reflow  oven {i // 2}"
            mrm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=f"deck-{i}",
                    surface=surface,
                    norm_surface=norm_surface(surface),
                    kind="機台",
                    norm_kind=norm_surface("機台"),
                    occurrences=i + 1,
                    collection_visibility="public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(f"deck-{i}", surface),
            )
    return cid


def test_the_identical_pass_never_materialises_a_mention(blob_reads: list[str]) -> None:
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)

    link_identical_mentions(spec)

    assert blob_reads == [], (
        f"{len(blob_reads)} mention rows were decoded from the resource store; "
        "everything this pass reads is indexed"
    )


def test_it_still_picks_the_name_the_corpus_wrote_most(blob_reads: list[str]) -> None:
    """Reading the index must not cost the pass its answer — the display name is
    the surface documents used most, which is exactly the field that moved."""
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)

    link_identical_mentions(spec)

    erm = spec.get_resource_manager(GraphEntity)
    assert _names(erm, "Reflow Oven 0") == ["Reflow Oven 0"], (
        "the identity took its name from the wrong surface, or from a normalised key"
    )
    assert _names(erm, "機台") == ["機台"], "the kind lost its display name"


def test_rows_written_before_the_index_are_read_from_their_blob(
    blob_reads: list[str], tmp_path
) -> None:
    """specstar extracts ``indexed_data`` at WRITE time and never backfills, so a
    row written before these fields were indexed carries none of them.

    Reading the index blindly would give such a row an empty surface and zero
    occurrences — a silently wrong display name rather than a loud failure, on
    every mention in the corpus, until somebody remembered to run the migrate.
    So the pass notices and pays for the blob instead: the migrate turns that
    cost off rather than switching correctness on, and the deploy needs no
    ordering against it.

    The pre-index row is written by a spec carrying the OLD registration against
    a shared disk backend — the same shape ``test_doc_status_index`` uses, and
    the only way to produce a row specstar itself considers un-migrated.
    """
    from specstar import BackendBinding, BackendConfig, ConnectionProfile, SpecStar

    backend = BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(tmp_path)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )
    old = SpecStar()
    old.configure(default_user="bob", backend=backend)
    old.add_model(Collection)
    old.add_model(  # the registration before the display fields were indexed
        GraphMention,
        indexed_fields=["collection_id", "source_doc_id", "norm_surface", "norm_kind"],
    )
    cid = old.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    for i, surface in enumerate(("Reflow Oven 0", "reflow  oven 0")):
        old.get_resource_manager(GraphMention).create(
            GraphMention(
                collection_id=cid,
                source_doc_id=f"deck-{i}",
                surface=surface,
                norm_surface=norm_surface(surface),
                kind="機台",
                norm_kind=norm_surface("機台"),
                occurrences=9 if i == 0 else 1,
            ),
            resource_id=mention_id(f"deck-{i}", surface),
        )

    spec = make_spec(default_user=lambda: "bob", backend=backend)
    blob_reads.clear()

    link_identical_mentions(spec)

    assert blob_reads, "a pre-migrate row was read out of an index it is not in"
    erm = spec.get_resource_manager(GraphEntity)
    assert _names(erm, "Reflow Oven 0") == ["Reflow Oven 0"], (
        "a pre-migrate row produced an empty display name instead of a fallback read"
    )


def _names(erm, surface: str) -> list[str]:
    """Display names of the identities holding ``surface``'s key.

    A loop rather than a comprehension: ``r.data`` is typed ``Struct | UnsetType``
    and only an isinstance narrows it, which a comprehension cannot carry.
    """
    out: list[str] = []
    for r in erm.list_resources((QB["norm_keys"].contains(norm_surface(surface))).build()):
        entity = r.data
        assert isinstance(entity, GraphEntity)
        out.append(entity.canonical_name)
    return out
