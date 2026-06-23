"""The Topic Hub ``→collections`` workflow (#133, §12) — produce → review → commit:
classify uploads into the Hub's collections while **drafting** a definition for each
unknown term, assemble those drafts into ``glossary.todo.md`` deterministically, gate on
a human (approve / reject / revise), then ingest the docs + author a context card per
filled glossary entry. Uncertain drafts are flagged with ``⚠️`` and skipped at commit
until a human resolves them.

Driven through the file-path-loaded ``run()`` with a fake agent turn + fake ingest /
card-author / collection-check capabilities (no LLM, no KB)."""

import re

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import load_run_callable, validate_workflow_profiles
from workspace_app.workflow.gate import AwaitingHuman, record_decision
from workspace_app.workflow.handle import WorkflowHandle

_OUT_RE = re.compile(r"(plan/\S+?\.json)")


def _run():
    return load_run_callable("topic-hub", "default", "collections")


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


def _handle(make_plan=None, landed_ok=True):
    """A handle whose agent turn drafts a classify plan for the file named in the
    prompt (the plan path is parsed back out of the prompt). ``make_plan(prompt, n)``
    overrides the default single-term confident plan per call."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    ingested: list = []
    cards: list = []
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        out = _OUT_RE.search(prompt).group(1)
        plan = make_plan(prompt, len(calls)) if make_plan else _plan(_confident())
        await wf.write_json(out, plan)
        return "done"

    async def ingest(coll, path):
        ingested.append((coll, path))
        return f"doc:{coll}:{path}"

    async def upsert_card(coll, keys, title, body):
        cards.append((coll, list(keys), title, body))
        return f"card:{title}"

    async def landed(_coll, _path):
        return landed_ok

    wf.drive_turn = drive_turn
    wf._ingest = ingest
    wf._upsert_card = upsert_card
    wf._collection_has = landed
    return wf, ingested, cards, calls


async def _seed(wf: WorkflowHandle) -> None:
    await wf.write("inputs/a.txt", b"the M4 layer delaminated")
    await wf.write("inputs/input.json", b"{}")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')


async def test_collections_workflow_is_discovered_and_coherent():
    validate_workflow_profiles("topic-hub")
    fn = _run()
    assert callable(fn) and fn.__name__ == "run"


async def test_no_collection_set_short_circuits():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("inputs/a.txt", b"x")
    await wf.write("inputs/input.json", b"{}")
    await wf.write("collections.json", b"[]")  # empty set → nothing to classify into
    assert await run(wf, {}) == {"status": "no_collections"}


async def test_classify_drafts_definitions_then_suspends_committing_nothing():
    """The agent drafts a definition while classifying; the workflow assembles it into
    ``glossary.todo.md`` and suspends at the review gate — committing nothing."""
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    glossary = await wf.read_text("glossary.todo.md")
    assert "## M4" in glossary
    assert "The fourth metal layer." in glossary  # AI-drafted definition, not a blank
    assert ingested == [] and cards == []  # nothing committed before approval


async def test_no_files_short_circuits():
    run = _run()
    wf, _ingested, _cards, _calls = _handle()
    await wf.write("inputs/input.json", b"{}")  # the only file is the (excluded) spec
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')
    assert await run(wf, {}) == {"status": "empty", "files": 0}


async def test_approve_commits_the_drafted_card_and_ingests():
    run = _run()
    wf, ingested, cards, _calls = _handle()  # default confident M4 draft
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 1, "cards": 1}
    assert ingested == [("Defects", "/inputs/a.txt")]
    assert cards == [("Defects", ["M4"], "M4", "The fourth metal layer.")]


async def test_uncertain_draft_is_flagged_and_skipped_until_resolved():
    run = _run()
    wf, _ingested, cards, _calls = _handle(
        lambda _p, _n: _plan(_confident(), _uncertain("reflow", "maybe an oven step"))
    )
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    glossary = await wf.read_text("glossary.todo.md")
    assert "## reflow" in glossary and "⚠️" in glossary  # flagged, not silently dropped
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
    # The human edits the draft in the IDE, replacing the ⚠️ line with a real definition.
    await wf.write("glossary.todo.md", "## reflow\nOven step that melts solder paste.\n")
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
    glossary = await wf.read_text("glossary.todo.md")
    assert "4th metal layer." in glossary and "verbose" not in glossary  # redraft overwrote
    await record_decision(wf, phase="review_1", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 1, "cards": 1}
    assert cards == [("Defects", ["M4"], "M4", "4th metal layer.")]


async def test_gate_summary_points_to_the_ide_with_counts_and_routing():
    run = _run()
    wf, _ingested, _cards, _calls = _handle(
        lambda _p, _n: _plan(_confident("M4"), _uncertain("reflow", "maybe oven"))
    )
    await _seed(wf)
    with pytest.raises(AwaitingHuman) as ei:
        await run(wf, {})
    summary = ei.value.summary
    assert "glossary.todo.md" in summary  # tells the human where to look
    assert "2 term(s)" in summary and "1 still need" in summary  # counts incl. ⚠️
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
    # The human refines the definition in the IDE and re-runs the approved workflow.
    await wf.write("glossary.todo.md", "## M4\nThe fourth metal interconnect layer.\n")
    await run(wf, {})
    assert cards[-1] == ("Defects", ["M4"], "M4", "The fourth metal interconnect layer.")
    assert ingested == [("Defects", "/inputs/a.txt")]  # ingest stayed idempotent


async def test_ingest_that_does_not_land_is_not_counted_but_cards_still_author():
    run = _run()
    wf, ingested, cards, _calls = _handle(landed_ok=False)  # ingest runs but never lands
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 0, "cards": 1}
    assert ingested == [("Defects", "/inputs/a.txt")]  # attempted, just not counted as landed


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


def test_plan_terms_tolerates_bare_strings_and_empty_terms():
    pt = _g()["_plan_terms"]
    plan = {
        "terms": [
            "bare",
            "   ",  # blank string → skipped
            123,  # non-str / non-dict → skipped
            {"term": ""},  # empty term → skipped
            {"term": "x", "definition": "d", "confident": True},
        ]
    }
    assert pt(plan) == [("bare", "", False), ("x", "d", True)]


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


def test_assemble_glossary_marks_uncertain_and_dedups():
    asm = _g()["_assemble_glossary"]
    plans = {
        "/f1": _plan(
            _confident("M4", "metal 4"),
            _uncertain("reflow", "oven"),
            {"term": "bare", "definition": "", "confident": False},
        ),
        "/f2": _plan(_confident("M4", "duplicate ignored")),
    }
    out = asm(plans)
    assert "## M4\nmetal 4\n" in out and out.count("## M4") == 1  # first appearance wins
    assert "## reflow\n⚠️ oven\n" in out  # uncertain → ⚠️ + the guess
    assert "## bare\n⚠️ draft a definition for this term\n" in out  # no draft → ⚠️ prompt


def test_term_collection_first_file_wins_and_skips_missing_collection():
    tc = _g()["_term_collection"]
    plans = {
        "/a": _plan(_confident("t"), collection="A"),
        "/b": _plan(_confident("t"), collection="B"),
    }
    assert tc(plans) == {"t": "A"}
    assert tc({"/a": {"terms": [{"term": "t"}]}}) == {}  # no collection → skipped


def test_parse_glossary_ignores_leading_text_and_warning_lines():
    parse = _g()["_parse_glossary"]
    assert parse("intro before any heading\n## b\nreal\n") == [("b", "real")]
    assert parse("## a\n⚠️ guess\n\n## c\n⚠️ hint\nreal\n") == [("c", "real")]
