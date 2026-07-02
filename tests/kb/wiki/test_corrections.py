"""#397 — the builder-immune corrections page (append) + submit convergence.

These cover the deterministic page I/O (no LLM): a user's wiki correction lands
as a faithful entry under /corrections/<target-slug>.md, repeated corrections to
the same page merge, and untargeted ones fall to general.md.
"""

from __future__ import annotations

from workspace_app.kb.wiki.corrections import append_correction_page
from workspace_app.kb.wiki.store import CORRECTIONS_DIR, WikiFileStore, correction_page_path
from workspace_app.resources import make_spec


async def test_correction_lands_on_the_target_page_file():
    spec = make_spec(default_user="u")
    store = WikiFileStore(spec)
    path = await append_correction_page(
        store,
        collection_id="c",
        target_page="/entities/foo.md",
        instruction="Foo was founded in 1998, not 1989.",
        actor="alice",
        has_reference=False,
    )
    assert path == correction_page_path("/entities/foo.md")
    assert path.startswith(CORRECTIONS_DIR)
    page = (await store.read("c", path)).decode()
    assert "Foo was founded in 1998, not 1989." in page  # the corrected fact, verbatim
    assert "/entities/foo.md" in page  # which page it's about
    assert "alice" in page  # who reported it


async def test_repeated_corrections_to_the_same_page_merge():
    spec = make_spec(default_user="u")
    store = WikiFileStore(spec)
    await append_correction_page(
        store, collection_id="c", target_page="/x.md", instruction="first fix", actor="a"
    )
    path = await append_correction_page(
        store, collection_id="c", target_page="/x.md", instruction="second fix", actor="a"
    )
    page = (await store.read("c", path)).decode()
    assert "first fix" in page and "second fix" in page  # both survive on one file


async def test_untargeted_correction_falls_to_general():
    spec = make_spec(default_user="u")
    store = WikiFileStore(spec)
    path = await append_correction_page(
        store, collection_id="c", target_page="", instruction="something is off", actor="a"
    )
    assert path == CORRECTIONS_DIR + "general.md"


async def test_reference_note_is_recorded_but_not_the_reference_text():
    # #397 Q9: the immune page records the corrected fact + a note that a reference
    # backed it — never the reference's full text (that stays a transient job input).
    spec = make_spec(default_user="u")
    store = WikiFileStore(spec)
    path = await append_correction_page(
        store,
        collection_id="c",
        target_page="/x.md",
        instruction="the corrected fact",
        actor="a",
        has_reference=True,
    )
    page = (await store.read("c", path)).decode()
    assert "the corrected fact" in page
    assert "reference" in page.lower()  # a note that a reference was provided
