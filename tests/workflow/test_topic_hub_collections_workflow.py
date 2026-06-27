"""The Topic Hub ``→collections`` workflow (#133, §12) — produce → review → commit:
classify uploads into the Hub's collections while **drafting** a markdown definition for
each unknown term (with the surface forms a reader might search as the card's ``keys``,
#182), assemble those drafts into ``context-card.todo.md`` deterministically, gate on a
human (approve / reject / revise), then ingest the docs + author a context card per filled
block. Uncertain drafts are flagged with ``⚠️`` and skipped at commit until a human
resolves them.

Driven through the file-path-loaded ``run()`` with a fake agent turn + fake ingest /
card-author / collection-check capabilities (no LLM, no KB)."""

import re

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import (
    load_preflight_callable,
    load_run_callable,
    validate_workflow_profiles,
)
from workspace_app.workflow.gate import AwaitingHuman, record_decision
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.preflight import Severity, can_run

_OUT_RE = re.compile(r"(plan/\S+?\.json)")


def _run():
    return load_run_callable("topic-hub", "default", "collections")


def _preflight():
    pf = load_preflight_callable("topic-hub", "default", "collections")
    assert pf is not None
    return pf


def _confident(term="M4", definition="The fourth metal layer."):
    return {"term": term, "definition": definition, "confident": True}


def _uncertain(term="M4", definition="possibly the fourth metal layer"):
    return {"term": term, "definition": definition, "confident": False}


def _plan(*terms, collection="Defects", digest="a defect report"):
    return {"collection": collection, "digest": digest, "terms": list(terms)}


def _g():
    """The loaded workflow module's globals — lets us unit-test its deterministic pure
    helpers (the module is loaded by file path for the hyphenated slug, not importable)."""
    return _run().__globals__


def _handle(make_plan=None, landed_ok=True, find_existing=None):
    """A handle whose agent turn drafts a classify plan for the file named in the
    prompt (the plan path is parsed back out of the prompt). ``make_plan(prompt, n)``
    overrides the default single-term confident plan per call. ``find_existing(coll,
    keys, title)`` fakes the #205 find-overwrite-target capability (None ⇒ no existing
    card, so the snapshot is empty)."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    ingested: list = []
    cards: list = []
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        m = _OUT_RE.search(prompt)
        assert m is not None, "the classify prompt always names its plan path"
        out = m.group(1)
        plan = make_plan(prompt, len(calls)) if make_plan else _plan(_confident())
        await wf.write_json(out, plan)
        return "done"

    async def ingest(coll, path):
        ingested.append((coll, path))
        return f"doc:{coll}:{path}"

    async def upsert_card(coll, keys, title, body):
        cards.append((coll, list(keys), title, body))
        return f"card:{title}"

    async def find_card(coll, keys, title):
        return find_existing(coll, keys, title) if find_existing else None

    async def landed(_coll, _path):
        return landed_ok

    wf.drive_turn = drive_turn
    wf._ingest = ingest
    wf._upsert_card = upsert_card
    wf._find_card = find_card
    wf._collection_has = landed
    return wf, ingested, cards, calls


async def _seed(wf: WorkflowHandle) -> None:
    await wf.write("uploads/a.txt", b"the M4 layer delaminated")
    await wf.write("uploads/input.json", b"{}")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')


async def test_collections_workflow_is_discovered_and_coherent():
    validate_workflow_profiles("topic-hub")
    fn = _run()
    assert callable(fn) and fn.__name__ == "run"


async def test_collections_preflight_blocks_without_collections():
    """#283: the user's exact pain — a long run that no-ops because collections aren't
    configured. Pre-flight catches it as a failing REQUIRED check before launch."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("uploads/a.txt", b"x")
    await wf.write("uploads/input.json", b"{}")  # files staged, but no collections.json
    report = await _preflight()(wf, {})
    assert can_run(report) is False
    coll = next(c for c in report.checks if not c.ok)
    assert coll.severity is Severity.REQUIRED and coll.reason


async def test_collections_preflight_blocks_without_files():
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')
    await wf.write("uploads/input.json", b"{}")  # collections set, but nothing staged
    report = await _preflight()(wf, {})
    assert can_run(report) is False
    files = next(c for c in report.checks if not c.ok)
    assert files.severity is Severity.REQUIRED and files.reason


async def test_collections_preflight_describes_target_collections_when_ready():
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await _seed(wf)
    report = await _preflight()(wf, {})
    assert can_run(report) is True
    assert "Defects" in report.summary  # the concrete target collection name
    assert "1" in report.summary  # the staged file count


async def test_no_collection_set_short_circuits_with_a_human_reason():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("uploads/a.txt", b"x")
    await wf.write("uploads/input.json", b"{}")
    await wf.write("collections.json", b"[]")  # empty set → nothing to classify into
    result = await run(wf, {})
    assert result["status"] == "no_collections"
    # The silent no-op is now self-explaining: a human-readable reason the FE shows.
    assert "知識庫" in result["message"]


async def test_malformed_collections_is_distinguished_from_empty():
    """The exact incident: collections.json is a list of strings, not [{id,name}]
    objects, so _read_collections parses zero — but the file is NOT empty. The run
    must say the file is malformed (a fixable format error), not 'no collections'."""
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("uploads/a.txt", b"x")
    await wf.write("uploads/input.json", b"{}")
    await wf.write("collections.json", b'["collection-id-123"]')  # strings, wrong shape
    result = await run(wf, {})
    assert result["status"] == "malformed_collections"
    assert "格式" in result["message"]


async def test_invalid_json_collections_is_malformed():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("collections.json", b"{ not json")  # unparseable
    result = await run(wf, {})
    assert result["status"] == "malformed_collections"


async def test_absent_collections_file_reports_no_collections():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    # No collections.json at all.
    result = await run(wf, {})
    assert result["status"] == "no_collections"
    assert "知識庫" in result["message"]


async def test_no_files_to_archive_is_a_visible_skip():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')
    # Only the spec file is present → nothing to classify.
    await wf.write("uploads/input.json", b"{}")
    result = await run(wf, {})
    assert result["status"] == "empty"
    assert result["message"]


async def test_classify_drafts_definitions_then_suspends_committing_nothing():
    """The agent drafts a definition while classifying; the workflow assembles it into
    ``context-card.todo.md`` (with a read-only ``.readonly/`` snapshot beside it) and
    suspends at the review gate — committing nothing."""
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    todo = await wf.read_text("context-card.todo.md")
    assert "title: M4" in todo
    assert "The fourth metal layer." in todo  # AI-drafted definition, not a blank
    # the read-only "before" snapshot is written too (empty — nothing exists yet)
    assert await wf.exists(".readonly/context-card.current.md")
    assert ingested == [] and cards == []  # nothing committed before approval


async def test_approve_commits_the_drafted_card_and_ingests():
    run = _run()
    wf, ingested, cards, _calls = _handle()  # default confident M4 draft
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 1, "cards": 1}
    assert ingested == [("Defects", "a.txt")]
    assert cards == [("Defects", ["M4"], "M4", "The fourth metal layer.")]


async def test_uncertain_draft_is_flagged_and_skipped_until_resolved():
    run = _run()
    wf, _ingested, cards, _calls = _handle(
        lambda _p, _n: _plan(_confident(), _uncertain("reflow", "maybe an oven step"))
    )
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    todo = await wf.read_text("context-card.todo.md")
    assert "title: reflow" in todo and "⚠️" in todo  # flagged, not silently dropped
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    # confident M4 → a card; uncertain reflow stays a ⚠️ line → no card.
    assert result["cards"] == 1
    assert [c[2] for c in cards] == ["M4"]


async def test_human_resolves_warning_in_ide_then_approve_authors_card():
    run = _run()
    wf, _ingested, cards, _calls = _handle(
        lambda _p, _n: _plan(_uncertain("reflow", "maybe an oven step"))
    )
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    # The human edits the proposed card in the diff, replacing the ⚠️ line with a real def.
    await wf.write(
        "context-card.todo.md",
        "<!-- card -->\ntitle: reflow\ncollection: Defects\nkeys: reflow\n\n"
        "Oven step that melts solder paste.\n",
    )
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result["cards"] == 1
    assert cards == [("Defects", ["reflow"], "reflow", "Oven step that melts solder paste.")]


async def test_reject_commits_nothing():
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="reject")
    result = await run(wf, {})
    assert result == {"status": "rejected", "files": 1}
    assert ingested == [] and cards == []


async def test_revise_redrafts_with_feedback_then_approve_commits():
    """`revise` feeds the human's note back into a fresh produce round that regenerates
    the drafts (overwriting), and opens a new gate phase."""
    seen_feedback: list[str] = []

    def make_plan(prompt, _n):
        if "shorter" in prompt:  # the revise round carries the reviewer's feedback
            seen_feedback.append(prompt)
            return _plan(_confident("M4", "4th metal layer."))
        return _plan(_confident("M4", "The fourth metal layer, a long verbose definition."))

    run = _run()
    wf, _ingested, cards, _calls = _handle(make_plan)
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})  # round 0 → gate "review"
    await record_decision(wf, phase="review", choice="revise", input="make it shorter")
    with pytest.raises(AwaitingHuman):
        await run(wf, {})  # revise → round 1 redraft → gate "review_1"
    assert seen_feedback  # feedback reached the re-draft prompt
    todo = await wf.read_text("context-card.todo.md")
    assert "4th metal layer." in todo and "verbose" not in todo  # redraft overwrote
    await record_decision(wf, phase="review_1", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 1, "cards": 1}
    assert cards == [("Defects", ["M4"], "M4", "4th metal layer.")]


async def test_gate_summary_points_to_the_diff_with_counts_and_routing():
    run = _run()
    wf, _ingested, _cards, _calls = _handle(
        lambda _p, _n: _plan(_confident("M4"), _uncertain("reflow", "maybe oven"))
    )
    await _seed(wf)
    with pytest.raises(AwaitingHuman) as ei:
        await run(wf, {})
    summary = ei.value.summary
    assert "查看變更" in summary  # tells the human to open the diff
    assert "2 card(s)" in summary and "1 still need" in summary  # counts incl. ⚠️
    assert "→ Defects" in summary  # routing


async def test_rerun_with_an_edited_definition_upserts_the_card_again():
    """#111: refining a definition and re-running re-fires the card commit (the receipt
    folds the body) so the card upserts to the new text; ingest replays idempotently."""
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="approve")
    await run(wf, {})
    assert cards == [("Defects", ["M4"], "M4", "The fourth metal layer.")]
    # The human refines the definition in the diff and re-runs the approved workflow.
    await wf.write(
        "context-card.todo.md",
        "<!-- card -->\ntitle: M4\ncollection: Defects\nkeys: M4\n\n"
        "The fourth metal interconnect layer.\n",
    )
    await run(wf, {})
    assert cards[-1] == ("Defects", ["M4"], "M4", "The fourth metal interconnect layer.")
    assert ingested == [("Defects", "a.txt")]  # ingest stayed idempotent


async def test_ingest_that_does_not_land_is_not_counted_but_cards_still_author():
    run = _run()
    wf, ingested, cards, _calls = _handle(landed_ok=False)  # ingest runs but never lands
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 0, "cards": 1}
    assert ingested == [("Defects", "a.txt")]  # attempted, just not counted as landed


# --- deterministic helpers (unit-tested directly via the loaded module's globals) ---


async def test_read_collections_tolerates_absent_malformed_and_odd_entries():
    rc = _g()["_read_collections"]
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    assert await rc(wf) == []  # absent file
    await wf.write("collections.json", b"{not json")
    assert await rc(wf) == []  # malformed JSON
    await wf.write("collections.json", b'"hello"')
    assert await rc(wf) == []  # valid JSON but not a list
    # name wins, id is the fallback, non-dicts and name-less dicts are skipped
    await wf.write("collections.json", b'[{"name": "A"}, {"id": "B"}, 123, {"x": "y"}]')
    assert await rc(wf) == ["A", "B"]


def test_plan_terms_normalises_old_and_new_shapes():
    """#182: ``terms`` carry ``keys`` now. ``_plan_terms`` yields (title, keys, definition,
    confident) and stays tolerant of the pre-#182 ``{term}`` shape, bare strings, and a
    ``{title}`` with no keys (keys fall back to the title)."""
    pt = _g()["_plan_terms"]
    plan = {
        "terms": [
            "bare",  # bare string → title + sole key = the string, uncertain
            "   ",  # blank string → skipped
            123,  # non-str / non-dict → skipped
            {"term": ""},  # empty term → skipped
            {"term": "x", "definition": "d", "confident": True},  # pre-#182 shape
            {"title": "Metal 4", "keys": ["M4", "Metal 4"], "definition": "4th", "confident": True},
            {"title": "CMP"},  # title with no keys → keys fall back to [title]
        ]
    }
    assert pt(plan) == [
        ("bare", ["bare"], "", False),
        ("x", ["x"], "d", True),
        ("Metal 4", ["M4", "Metal 4"], "4th", True),
        ("CMP", ["CMP"], "", False),
    ]


async def test_classify_check_rejects_bad_plan_shapes():
    check = _g()["_classify_check"]("plan/x.json", ["Defects"])
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    assert not (await check(wf, None)).ok  # not written
    await wf.write_json("plan/x.json", ["nope"])
    assert not (await check(wf, None)).ok  # not a dict
    await wf.write_json("plan/x.json", {"collection": "Other", "terms": []})
    assert not (await check(wf, None)).ok  # collection not allowed
    await wf.write_json("plan/x.json", {"collection": "Defects", "terms": "x"})
    assert not (await check(wf, None)).ok  # terms not a list
    await wf.write_json("plan/x.json", {"collection": "Defects", "terms": []})
    assert (await check(wf, None)).ok  # valid


def test_proposed_cards_dedups_marks_uncertain_and_routes():
    pc = _g()["_proposed_cards"]
    plans = {
        "/f1": _plan(
            _confident("M4", "metal 4"),
            _uncertain("reflow", "oven"),
            {"term": "bare", "definition": "", "confident": False},
        ),
        "/f2": _plan(_confident("M4", "duplicate ignored")),
    }
    cards = pc(plans)
    assert [c["title"] for c in cards] == ["M4", "reflow", "bare"]  # order, deduped
    by_title = {c["title"]: c for c in cards}
    assert by_title["M4"]["body"] == "metal 4"  # first appearance wins
    assert by_title["M4"]["keys"] == ["M4"] and by_title["M4"]["collection"] == "Defects"
    assert by_title["reflow"]["body"] == "⚠️ oven"  # uncertain → ⚠️ + the guess
    assert by_title["bare"]["body"] == "⚠️ draft a definition for this term"  # no draft


def test_classify_prompt_explains_key_search_and_asks_for_markdown_body():
    """#182/#183: the prompt must tell the AI HOW keys get searched (exact normalised
    membership) so it provides real aliases as separate keys rather than a sentence, and
    ask for a markdown body. (Behaviour is verified live; this locks the guidance in place.)"""
    prompt = _g()["_classify_prompt"]("f.txt", "plan/out.json", ["Defects"], "")
    low = prompt.lower()
    assert "exact" in low  # exact-membership lookup semantics are communicated
    assert "alias" in low or "surface form" in low  # ask for aliases, not one form
    assert "markdown" in low  # body is markdown (#183)
    assert '"keys"' in prompt  # the requested JSON schema carries a keys list


def test_proposed_cards_uses_ai_aliases_as_keys():
    """#182: when the classify plan gives a term several surface forms, the proposed card
    carries ALL of them as keys (so each alias can be found by exact lookup) under the AI's
    chosen display title — not just the term collapsed to a single key."""
    pc = _g()["_proposed_cards"]
    plans = {
        "/f1": _plan(
            {
                "title": "Metal 4",
                "keys": ["M4", "Metal 4", "第四層金屬"],
                "definition": "the fourth metal layer",
                "confident": True,
            },
        )
    }
    cards = pc(plans)
    assert len(cards) == 1
    assert cards[0]["title"] == "Metal 4"
    assert cards[0]["keys"] == ["M4", "Metal 4", "第四層金屬"]
    assert cards[0]["body"] == "the fourth metal layer"


def test_proposed_cards_merges_cross_file_aliases_by_normalised_key():
    """#182: the same concept seen in two files under overlapping-but-different surface forms
    folds into ONE card (deduped by NORMALISED key), unioning the new aliases — not two
    near-duplicates. Case/width differences normalise onto the same key (so ``m4`` ≡ ``M4``);
    first appearance wins for title + body; an already-present alias isn't re-added."""
    pc = _g()["_proposed_cards"]
    plans = {
        "/f1": _plan(
            {
                "title": "Metal 4",
                "keys": ["M4", "Metal 4"],
                "definition": "4th layer",
                "confident": True,
            }
        ),
        "/f2": _plan(
            {
                "title": "dup",
                "keys": ["m4", "第四層金屬"],
                "definition": "ignored",
                "confident": True,
            }
        ),
    }
    cards = pc(plans)
    assert len(cards) == 1  # "m4" normalises onto "M4" → same card, not a duplicate
    assert cards[0]["title"] == "Metal 4" and cards[0]["body"] == "4th layer"  # first wins
    assert cards[0]["keys"] == [
        "M4",
        "Metal 4",
        "第四層金屬",
    ]  # new alias unioned, dup not re-added


def test_render_cards_emits_diffable_blocks_with_keys_and_collection():
    render = _g()["_render_cards"]
    out = render(
        [{"title": "M4", "collection": "Defects", "keys": ["M4", "Metal 4"], "body": "the 4th"}]
    )
    assert "<!-- card -->" in out  # sentinel boundary, not a ## heading (#183)
    assert "title: M4" in out
    assert "collection: Defects" in out
    assert "keys: M4, Metal 4" in out  # keys in-file so a narrowing shows in the diff
    assert "the 4th" in out
    assert render([]) == ""  # empty snapshot = every proposed card is new


def test_card_body_keeps_markdown_headings_through_render_and_parse():
    """#183: a card body is free markdown — including ``##`` sub-headings. The render →
    parse round-trip must NOT mistake a heading inside the body for a new card."""
    g = _g()
    render, parse = g["_render_cards"], g["_parse_cards"]
    body = "Intro paragraph.\n\n## Usage\n- signal routing\n- power\n\n## Notes\nsee M40."
    text = render(
        [{"title": "M4", "collection": "Defects", "keys": ["M4", "Metal 4"], "body": body}]
    )
    cards = parse(text)
    assert len(cards) == 1  # the ## headings in the body did not start new cards
    assert cards[0]["title"] == "M4"
    assert cards[0]["keys"] == ["M4", "Metal 4"]
    assert cards[0]["body"] == body  # full markdown body preserved verbatim


def test_parse_cards_reads_metadata_routes_and_skips_warning_only():
    parse = _g()["_parse_cards"]
    text = "<!-- card -->\ntitle: M4\ncollection: Defects\nkeys: M4, Metal 4\n\nthe 4th metal\n"
    assert parse(text) == [
        {"collection": "Defects", "keys": ["M4", "Metal 4"], "title": "M4", "body": "the 4th metal"}
    ]
    # leading text before any card sentinel ignored; a ⚠️-only block authors no card
    assert parse("intro\n<!-- card -->\ntitle: a\n⚠️ guess\n") == []
    # a minimal hand-typed block: keys fall back to the title, collection stays blank
    assert parse("<!-- card -->\ntitle: reflow\n\noven step\n") == [
        {"collection": "", "keys": ["reflow"], "title": "reflow", "body": "oven step"}
    ]


# --- #205: the read-only "before" snapshot + collection-from-block routing ---


def _existing(keys, title, body, ambiguity=1):
    return lambda _coll, _ks, _t: (
        {"keys": keys, "title": title, "body": body, "ambiguity": ambiguity}
    )


async def test_review_snapshot_shows_the_card_an_overwrite_would_replace():
    """#205: when a term already names a card, the read-only snapshot carries that card's
    CURRENT keys/title/body — so the diff shows before vs after and a silent key-narrowing
    (multi-key existing → single-key proposal) is visible, not hidden in a body-only view."""
    run = _run()
    wf, _ingested, _cards, _calls = _handle(
        find_existing=_existing(["M4", "Metal 4"], "Metal 4 layer", "the existing def")
    )
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    todo = await wf.read_text("context-card.todo.md")
    current = await wf.read_text(".readonly/context-card.current.md")
    # proposed (todo) narrows to a single key + retitles to the term...
    assert "title: M4" in todo and "keys: M4\n" in todo
    # ...while the read-only snapshot keeps the real card the upsert would overwrite.
    assert "title: Metal 4 layer" in current
    assert "keys: M4, Metal 4" in current
    assert "the existing def" in current


async def test_review_snapshot_is_empty_when_every_card_is_new():
    run = _run()
    wf, _ingested, _cards, _calls = _handle()  # default find → None
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    assert await wf.read_text(".readonly/context-card.current.md") == ""


async def test_ambiguous_overwrite_is_noted_in_the_summary():
    run = _run()
    wf, _ingested, _cards, _calls = _handle(find_existing=_existing(["M4"], "M4", "x", ambiguity=3))
    await _seed(wf)
    with pytest.raises(AwaitingHuman) as ei:
        await run(wf, {})
    assert "match more than one existing card" in ei.value.summary


async def test_commit_routes_by_the_block_collection_not_the_title():
    """#205: commit reads each card's collection from its block, so editing the title in
    the diff can't misroute the card — the human can also re-route it to another collection."""
    run = _run()
    wf, _ingested, cards, _calls = _handle()
    await wf.write("uploads/a.txt", b"x")
    await wf.write("uploads/input.json", b"{}")
    await wf.write("collections.json", b'[{"name": "Defects"}, {"name": "Notes"}]')
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    # The human renames the title and routes the card to Notes in the diff.
    await wf.write(
        "context-card.todo.md",
        "<!-- card -->\ntitle: renamed\ncollection: Notes\nkeys: M4\n\nbody\n",
    )
    await record_decision(wf, phase="review", choice="approve")
    await run(wf, {})
    assert cards == [("Notes", ["M4"], "renamed", "body")]


async def test_commit_rejects_a_block_routed_to_an_unknown_collection():
    run = _run()
    wf, _ingested, _cards, _calls = _handle()
    await _seed(wf)  # only "Defects"
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await wf.write(
        "context-card.todo.md",
        "<!-- card -->\ntitle: M4\ncollection: Nope\nkeys: M4\n\nbody\n",
    )
    await record_decision(wf, phase="review", choice="approve")
    with pytest.raises(ValueError, match="not one of the Hub"):
        await run(wf, {})
