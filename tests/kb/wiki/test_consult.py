"""#537: the `ask_wiki` consultant — one tool call over however many wikis a
turn scopes, with every draft's `[n]` folded onto one shared source list.

A fake runner stands in for the wiki reader (which is covered in test_reader.py);
what's proven here is the fold: which collections are consulted, in what order,
and whether the merged markers still point at the right documents.
"""

from __future__ import annotations

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta
from workspace_app.kb.wiki.consult import make_wiki_consultant, wiki_backed
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import RetrievedPassage


def _passage(doc: str) -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c",
        document_id=doc,
        filename=f"{doc}.md",
        start=0,
        end=1,
        source_chunk_ids=[],
        text=f"text of {doc}",
        score=0.0,
    )


class _Reader:
    """Answers as the wiki reader would: registers a source passage, cites it [1]."""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self.answers = answers or {}
        self.seen: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext):
        cid = ctx.investigation_id or ""
        self.seen.append(cid)
        text = self.answers.get(cid, f"{cid} says so [1].")
        if text:
            ctx.kb_passages.append(_passage(f"src-{cid}"))
        yield MessageDelta(text=text)


def _collection(spec, name: str, *, use_wiki: bool) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, use_wiki=use_wiki))
        .resource_id
    )


def test_a_scope_with_no_wiki_gets_no_consultant_at_all():
    # Not an empty consultant — the caller leaves `run_wiki_reader` unset so the
    # tool can say "there is no wiki here" instead of returning a hollow answer.
    spec = make_spec(default_user="u")
    cid = _collection(spec, "docs only", use_wiki=False)
    assert make_wiki_consultant(_Reader(), spec, [cid]) is None


def test_unknown_collection_ids_are_skipped_not_fatal():
    spec = make_spec(default_user="u")
    assert wiki_backed(spec, ["ghost"]) == []


async def test_one_wiki_answers_without_attribution_noise():
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Process", use_wiki=True)
    consult = make_wiki_consultant(_Reader(), spec, [cid])
    assert consult is not None

    answer, passages = await consult("what is zone 3?", None)
    assert answer == f"{cid} says so [1]."  # no "From the … wiki:" header
    assert [p.document_id for p in passages] == [f"src-{cid}"]


async def test_several_wikis_are_folded_onto_one_renumbered_source_list():
    spec = make_spec(default_user="u")
    a = _collection(spec, "Process", use_wiki=True)
    b = _collection(spec, "Equipment", use_wiki=True)
    skipped = _collection(spec, "Raw docs", use_wiki=False)
    consult = make_wiki_consultant(_Reader(), spec, [a, skipped, b])
    assert consult is not None

    answer, passages = await consult("q", None)

    # Only the wiki-backed collections were read, in the caller's order.
    assert [p.document_id for p in passages] == [f"src-{a}", f"src-{b}"]
    # The second draft's [1] moved to [2] — otherwise it would cite the FIRST
    # wiki's source while claiming to quote the second's.
    assert "[1]" in answer and "[2]" in answer
    assert "From the Process wiki:" in answer and "From the Equipment wiki:" in answer


async def test_a_wiki_with_nothing_to_say_is_dropped_from_the_fold():
    spec = make_spec(default_user="u")
    a = _collection(spec, "Process", use_wiki=True)
    b = _collection(spec, "Equipment", use_wiki=True)
    consult = make_wiki_consultant(_Reader({a: ""}), spec, [a, b])
    assert consult is not None

    answer, passages = await consult("q", None)
    assert [p.document_id for p in passages] == [f"src-{b}"]
    # The silent wiki contributes nothing — not an empty section the caller would
    # have to reason about, and not a marker offset for sources that don't exist.
    assert "Process" not in answer
    assert "From the Equipment wiki:" in answer
    assert "[1]" in answer  # Equipment's own source, not shifted past a phantom


async def test_when_no_wiki_has_anything_the_caller_is_told_plainly():
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Process", use_wiki=True)
    consult = make_wiki_consultant(_Reader({cid: ""}), spec, [cid])
    assert consult is not None

    answer, passages = await consult("q", None)
    assert answer == "The wiki has nothing on this."
    assert passages == []


async def test_collection_reader_guidance_reaches_that_wikis_reader_only():
    """#90: a collection's read-side guidance shapes ITS reader. A fold spanning
    collections has no single owner, so nothing collection-specific may leak into
    the other wiki's draft."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(Collection)
    a = rm.create(
        Collection(name="A", use_wiki=True, wiki_reader_guidance="Answer with a TL;DR.")
    ).resource_id
    b = rm.create(Collection(name="B", use_wiki=True)).resource_id

    prompts: dict[str, str] = {}

    class _Capture(_Reader):
        async def run(self, prompt: str, ctx: AgentToolContext):
            cfg = ctx.agent_config
            prompts[ctx.investigation_id or ""] = cfg.system_prompt if cfg else ""
            async for ev in super().run(prompt, ctx):
                yield ev

    consult = make_wiki_consultant(_Capture(), spec, [a, b])
    assert consult is not None
    await consult("q", None)

    assert "Answer with a TL;DR." in prompts[a]
    assert "Answer with a TL;DR." not in prompts[b]


async def test_progress_reaches_the_caller_while_a_wiki_is_being_read():
    """A consultation can run for dozens of reader turns. With nothing streamed
    back the chat shows an empty running card for the whole time, which reads as
    a hang — so each wiki announces itself on the caller's live tool-log channel."""
    spec = make_spec(default_user="u")
    a = _collection(spec, "Process", use_wiki=True)
    b = _collection(spec, "Equipment", use_wiki=True)
    lines: list[bytes] = []

    consult = make_wiki_consultant(_Reader(), spec, [a, b])
    assert consult is not None
    await consult("q", lines.append)

    assert [x.decode() for x in lines] == [
        "Reading the Process wiki…\n",
        "Reading the Equipment wiki…\n",
    ]
