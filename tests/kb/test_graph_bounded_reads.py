"""The vocabulary pass must never ask the store for a whole table at once.

specstar's postgres resource store fetches a listing's rows by inlining ONE row
constructor per key into a single statement::

    WHERE (i.resource_id, i.revision_id, i.schema_version) IN ((…),(…),(…), …)

so ``list_resources(QB.all())`` builds SQL whose SIZE is the table's row count.
Measured against Postgres 16: 40k rows is a 937 KB statement, and the answer is
``ERROR: stack depth limit exceeded``. That is not a slow query — it is a query
the database refuses, so the weekly reconcile stops running at all once a corpus
passes the threshold, which is what happened.

An in-memory or disk backend answers the same request happily, so nothing here
would ever fail on its own. The limit is therefore MODELLED: ``_capped`` is a
contract double for a store that refuses an over-large request, exactly as the
real one does. Asserting "we don't call list_resources" instead would be immune
to the regression — the defect is not which method is called, it is how many
rows one call brings back.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from specstar import QB, SpecStar

from workspace_app.kb.graph import link as link_mod
from workspace_app.kb.graph.link import reconcile_vocabulary
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphMention, mention_id
from workspace_app.resources.kb import Collection

# One page the reconcile is allowed to ask for, and the size the fake store
# refuses beyond. They are deliberately close: a pass that pages correctly sits
# under the cap no matter how large the corpus is, and one that does not exceeds
# it as soon as the corpus does — which is the property under test.
PAGE = 10
CAP = 15
ROWS = 60


class _Quiet(ILlm):
    """Answers "nothing belongs together", so the model-backed pass runs its
    reads without proposing anything."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield '{"groups": []}', False


@pytest.fixture
def capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every listing refuse to return more than ``CAP`` rows at once.

    The refusal is on the RESULT, not on the query's ``limit``: the real
    statement grows with the number of keys actually fetched, so a selective
    query with no limit is fine and an unselective one is not. Capping the limit
    instead would flag the harmless single-key lookups this module is full of.
    """
    from specstar.resource_manager.core import ResourceManager

    real = ResourceManager.list_resources

    def refusing(self, query=None, *args, **kwargs):  # noqa: ANN001, ANN202
        rows = real(self, query, *args, **kwargs)
        if len(rows) > CAP:
            raise RuntimeError(
                f"stack depth limit exceeded: {len(rows)} rows asked for in one "
                f"statement on {self.resource_name!r}"
            )
        return rows

    monkeypatch.setattr(ResourceManager, "list_resources", refusing)
    monkeypatch.setattr(link_mod, "_PAGE", PAGE)


def _corpus(spec: SpecStar) -> None:
    """More rows than one request may carry, each a distinct identity, some
    carrying a kind and one carrying a declaration — so every pass has real work
    and reads the tables it reads in production."""
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        for i in range(ROWS):
            surface = f"thing-{i:03d}"
            mrm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=f"deck-{i:03d}",
                    surface=surface,
                    norm_surface=norm_surface(surface),
                    kind="機台" if i % 2 else "產線",
                    norm_kind=norm_surface("機台" if i % 2 else "產線"),
                    occurrences=i + 1,
                    collection_visibility="public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(f"deck-{i:03d}", surface),
            )


def test_reconcile_pages_every_table_it_walks(capped: None) -> None:
    """The whole pass completes against a store that refuses a bulk request."""
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)

    reconcile_vocabulary(spec, llm=_Quiet())

    # It has to have done the work, not merely survived by reading nothing.
    from workspace_app.resources.graph import GraphEntity

    erm = spec.get_resource_manager(GraphEntity)
    names = set()
    for r in erm.list_resources(QB["norm_keys"].contains("thing-000").build()):
        entity = r.data
        assert isinstance(entity, GraphEntity)
        names.add(entity.canonical_name)
    assert names == {"thing-000"}, "the pass ran without building the vocabulary"


def test_absorbing_moves_every_link_even_past_one_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absorption is drained, not paged by offset — and the difference is silent.

    A popular identity carries one link per mention, so that list is corpus-sized
    and has to be bounded like every other. But the loop REWRITES ``entity_id``,
    the field it selects on, so every row it handles leaves the result set. An
    advancing offset would then skip exactly as many links as it had just moved,
    and the leftovers would keep pointing at a tombstone: a half-absorbed
    identity, with nothing raised anywhere.
    """
    from workspace_app.resources.graph import GraphEntity, GraphEntityLink

    monkeypatch.setattr(link_mod, "_PAGE", 3)
    spec = make_spec(default_user=lambda: "bob")
    erm = spec.get_resource_manager(GraphEntity)
    lrm = spec.get_resource_manager(GraphEntityLink)
    host = erm.create(GraphEntity(canonical_name="host", norm_keys=["host"])).resource_id
    other = erm.create(GraphEntity(canonical_name="other", norm_keys=["other"])).resource_id
    for i in range(10):  # comfortably more than one page
        lrm.create(GraphEntityLink(entity_id=other, mention_id=f"m{i}", basis="identical"))

    link_mod._absorb(spec, host, other, evidence="the deck said so")

    moved = lrm.list_resources((QB["entity_id"] == host).build())
    left = lrm.list_resources((QB["entity_id"] == other).build())
    assert len(moved) == 10, f"only {len(moved)} of 10 links were absorbed"
    assert not left, "links were left pointing at the tombstone"


def test_the_double_would_catch_the_regression(capped: None) -> None:
    """The guard is only worth having if it fires — this is the shape the fix
    removed, asserted directly so a future refactor cannot quietly restore it."""
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)
    mrm = spec.get_resource_manager(GraphMention)

    with pytest.raises(RuntimeError, match="stack depth limit exceeded"):
        mrm.list_resources(QB.all().build())
