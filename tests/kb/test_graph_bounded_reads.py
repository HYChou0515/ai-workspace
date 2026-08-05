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
    monkeypatch.setattr(link_mod, "PAGE", PAGE)


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


def test_a_merge_moves_every_mention_even_past_one_page() -> None:
    """A popular identity carries one link per mention, so a merge touches a
    corpus-sized list. It used to be applied by hand, by a loop that rewrote the
    very field it selected on — the hazard then was an offset skipping exactly
    the rows it had just moved, leaving a half-absorbed identity and raising
    nothing. The merge is now DERIVED instead of applied, which removes that
    hazard by construction; this holds the outcome the old loop existed for.
    """
    from workspace_app.kb.graph.link import reconcile_vocabulary
    from workspace_app.kb.graph.review import accept_proposal, entity_page
    from workspace_app.resources.graph import GraphEntity, GraphMention, entity_id, mention_id
    from workspace_app.resources.kb import Collection

    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    for i in range(10):  # comfortably more than one page
        for surface in ("host", "other"):
            with mrm.using("bob"):
                mrm.create(
                    GraphMention(
                        collection_id=cid,
                        source_doc_id=f"deck-{i}",
                        surface=surface,
                        norm_surface=surface,
                        occurrences=1,
                        collection_visibility="public",
                        collection_created_by="bob",
                        doc_visibility="public",
                    ),
                    resource_id=mention_id(f"deck-{i}", surface),
                )
    reconcile_vocabulary(spec, llm=None)

    accept_proposal(spec, entity_id("host"), entity_id("other"), by="amy")

    page = entity_page(spec, entity_id("host"), as_user="bob")
    assert len(page.mentions) == 20, f"only {len(page.mentions)} of 20 mentions landed on the merge"
    erm = spec.get_resource_manager(GraphEntity)
    gone = erm.get(entity_id("other")).data
    assert isinstance(gone, GraphEntity)
    assert gone.merged_into == entity_id("host")


def test_the_double_would_catch_the_regression(capped: None) -> None:
    """The guard is only worth having if it fires — this is the shape the fix
    removed, asserted directly so a future refactor cannot quietly restore it."""
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)
    mrm = spec.get_resource_manager(GraphMention)

    with pytest.raises(RuntimeError, match="stack depth limit exceeded"):
        mrm.list_resources(QB.all().build())


def test_walk_rows_returns_everything_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paging is only correct if the caller still sees every row.

    The failure mode it replaces was loud (the database refused the statement);
    the failure mode it could introduce is silent — a walk that stops at the
    first page returns a prefix and every caller treats it as the whole table.
    """
    from workspace_app.kb.graph import paging as paging_mod
    from workspace_app.kb.graph.paging import walk_rows

    # `paging`, not `link`: `walk_rows` moved, and patching the name it used to
    # live under leaves the real PAGE at 500, so a 60-row corpus fits in one page
    # and the test passes whether or not anything pages at all.
    monkeypatch.setattr(paging_mod, "PAGE", 7)
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)
    mrm = spec.get_resource_manager(GraphMention)

    walked = [r.info.resource_id for r in walk_rows(mrm)]

    assert len(walked) == ROWS, f"the walk stopped early: {len(walked)} of {ROWS}"
    assert len(set(walked)) == ROWS, "a page boundary repeated rows"


def test_a_did_you_mean_list_is_bounded_and_skips_the_unrelated() -> None:
    """The near-miss hint walks the whole vocabulary on every lookup that MISSES,
    which is the common case for a name the corpus does not know — so it pages
    like the rest, stops at the cap, and never offers a name that shares nothing
    with what was asked."""
    from workspace_app.kb.graph.lookup import entity_card
    from workspace_app.resources.graph import GraphEntity

    spec = make_spec(default_user=lambda: "bob")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    erm = spec.get_resource_manager(GraphEntity)
    for i in range(8):  # more near misses than the hint is allowed to print
        erm.create(
            GraphEntity(
                canonical_name=f"reflow oven {i}",
                norm_keys=[f"reflow oven {i}"],
                collection_ids=[cid],
            )
        )
    erm.create(
        GraphEntity(canonical_name="錫膏", norm_keys=["錫膏"], collection_ids=[cid])
    )  # shares nothing with the asked name

    card = entity_card(spec, "reflow oven", as_user="bob")

    assert "Entity not found" in card
    assert "錫膏" not in card, "an unrelated name was offered as a near miss"
    assert card.count("reflow oven") <= 6, "the hint printed more candidates than its cap"


def test_walk_rows_never_asks_for_more_than_one_page(
    capped: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard for the failure #689 exists to prevent, on the walk that is left.

    The reconcile moved to specstar's own `iter_all`, which the result cap never
    sees, so nothing was left holding `walk_rows` to its contract: replacing its
    body with a single unbounded `list_resources` kept every test in this package
    green. Under the cap, that rewrite is exactly what raises.
    """
    from workspace_app.kb.graph import paging as paging_mod
    from workspace_app.kb.graph.paging import walk_rows

    monkeypatch.setattr(paging_mod, "PAGE", PAGE)
    spec = make_spec(default_user=lambda: "bob")
    _corpus(spec)
    mrm = spec.get_resource_manager(GraphMention)

    assert len(list(walk_rows(mrm))) == ROWS
