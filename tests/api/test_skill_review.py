"""The AI review a skill passes through before it reaches the skill hub (plan P3).

It runs on the SAME runner the turn runs on — the reviewer is a sub-agent of
the publishing turn, so it inherits the turn's engine (model, endpoint, the
failover chain and its 429 wait) with no second LLM wiring to keep in step.
Structural traps were refused before this ran (`validate_skill_payload`); the
reviewer only ANNOTATES — it never blocks (D4). What does block is the review
not happening at all: no model means the system is broken, and a skill hub that
published anyway would be the hole the operator asked us not to open (Q9).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone, RunError
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.api.skill_review import (
    REVIEW_FILE_CAP,
    REVIEW_TOTAL_CAP,
    REVIEWER,
    SkillReviewUnavailable,
    render_review_prompt,
    review_skill,
)
from workspace_app.apps.skill_hub import SkillHubReview
from workspace_app.resources.agent_config import AgentConfig

SKILL_MD = (
    b"---\nname: triage-reflow\ndescription: Triage reflow defects from the log.\n---\n\n"
    b"# How\n\n1. Read `app.log` with `read_file`.\n2. Run `scripts/reflow.py` with `exec`.\n"
)
PAYLOAD = {
    "SKILL.md": SKILL_MD,
    "references/glossary.md": b"reflow: the solder step\n",
    "scripts/reflow.py": b"print('reflow')\n",
    "assets/board.png": b"\x89PNG\r\n\x1a\n\x00\x00binary",
}


class _Recorder(ScriptedAgentRunner):
    """`ScriptedAgentRunner` that also records what it was driven with."""

    def __init__(self, events: list[AgentEvent]) -> None:
        super().__init__(events)
        self.prompt: str | None = None
        self.ctx: AgentToolContext | None = None

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.prompt = prompt
        self.ctx = ctx
        async for ev in super().run(prompt, ctx):
            yield ev


def _parent(model: str = "gpt-4o") -> AgentToolContext:
    return AgentToolContext(
        investigation_id="inv-1",
        agent_config=AgentConfig(
            name="main",
            model=model,
            system_prompt="you are the main agent",
            allowed_tools=["exec", "read_file", "save_skill"],
        ),
        history=[{"role": "user", "content": "publish my skill"}],
    )


def _said(text: str) -> _Recorder:
    return _Recorder([MessageDelta(text=text), RunDone()])


# ── it runs as a sub-agent of the publishing turn ────────────────────────────


async def test_the_reviewer_is_a_sub_agent_of_the_turn_with_its_own_prompt_and_no_tools():
    runner = _said('{"verdict": "ok", "notes": []}')

    review = await review_skill(runner, _parent(), "triage-reflow", PAYLOAD)

    assert review == SkillHubReview(verdict="ok", notes=[], model="gpt-4o")
    child = runner.ctx
    assert child is not None and child.agent_config is not None
    assert child.agent_config.system_prompt == REVIEWER.body
    assert child.agent_config.allowed_tools == [], "a reviewer reads; it does not act"
    assert child.history == [], "the publishing conversation is not the reviewer's business"
    assert child.agent_config.model == "gpt-4o", "same engine as the turn — no second LLM wiring"


async def test_the_reviewer_reads_the_whole_folder_but_not_binary_bytes():
    runner = _said('{"verdict": "ok", "notes": []}')

    await review_skill(runner, _parent(), "triage-reflow", PAYLOAD)

    prompt = runner.prompt or ""
    assert "triage-reflow" in prompt
    assert "Triage reflow defects from the log." in prompt
    assert "reflow: the solder step" in prompt, "references ship with the skill; review them"
    assert "print('reflow')" in prompt
    assert "assets/board.png" in prompt, "a binary file is listed …"
    assert "\x89PNG" not in prompt, "… but its bytes are not pasted into a prompt"


def test_a_long_reference_is_cut_at_the_file_cap_and_says_so():
    """The reviewer is one prompt on the turn's engine; a 2 MB reference must not
    become a 2 MB prompt. `SKILL.md` is exempt — its body is what is reviewed,
    and validation already capped it."""
    big = ("x" * REVIEW_FILE_CAP + "TAIL-NOT-SHOWN").encode()
    prompt = render_review_prompt("s", {"SKILL.md": SKILL_MD, "references/big.md": big})

    assert "TAIL-NOT-SHOWN" not in prompt
    assert "truncated, 14 more characters" in prompt


def test_skill_md_is_shown_whole_however_long_it_is_within_its_own_cap():
    """The body IS the thing under review; cutting it would review half a
    skill and note nothing about the half that was cut."""
    long_md = SKILL_MD + b"z" * REVIEW_FILE_CAP + b"BODY-END"
    prompt = render_review_prompt("s", {"SKILL.md": long_md})

    assert "BODY-END" in prompt
    assert "truncated" not in prompt


def test_past_the_total_cap_the_rest_of_the_folder_is_listed_not_shown():
    per = REVIEW_FILE_CAP
    n = REVIEW_TOTAL_CAP // per + 1
    payload = {"SKILL.md": SKILL_MD}
    for i in range(n):
        payload[f"references/r{i:02d}.md"] = f"BODY-{i:02d} ".encode() + b"y" * per
    payload["references/zz-last.md"] = b"LAST-BODY"

    prompt = render_review_prompt("s", payload)

    assert "LAST-BODY" not in prompt
    assert "references/zz-last.md" in prompt and "review budget reached" in prompt
    assert "BODY-00" in prompt, "the files that fit are still shown"


# ── what it says comes back as notes ─────────────────────────────────────────


async def test_notes_come_back_as_notes_even_when_wrapped_in_prose_and_a_fence():
    runner = _said(
        "Here is my review:\n```json\n"
        '{"verdict": "notes", "notes": ["the description never says WHEN to use it",'
        ' "step 2 needs `exec`, which the body does not say"]}\n```\nHope that helps.'
    )

    review = await review_skill(runner, _parent(), "triage-reflow", PAYLOAD)

    assert review.verdict == "notes"
    assert review.notes == [
        "the description never says WHEN to use it",
        "step 2 needs `exec`, which the body does not say",
    ]


async def test_the_verdict_is_derived_from_the_notes_not_trusted_from_the_model():
    """A model that writes notes and stamps `ok` on them has contradicted itself;
    the notes are the review, so they decide. The other direction too: `notes`
    with nothing in it is `ok`."""
    contradicted = _said('{"verdict": "ok", "notes": ["vague description"]}')
    assert (await review_skill(contradicted, _parent(), "s", PAYLOAD)).verdict == "notes"

    empty = _said('{"verdict": "notes", "notes": []}')
    assert (await review_skill(empty, _parent(), "s", PAYLOAD)).verdict == "ok"


async def test_a_review_written_in_prose_is_kept_as_one_note_not_thrown_away():
    """The reviewer DID review; it just did not answer in the shape it was asked
    for. Dropping its words would publish a skill as `ok` on the strength of a
    format slip — the opposite of what the review is for."""
    runner = _said("The description is too vague to ever trigger. Say what the user would ask.")

    review = await review_skill(runner, _parent(), "s", PAYLOAD)

    assert review.verdict == "notes"
    assert review.notes == [
        "The description is too vague to ever trigger. Say what the user would ask."
    ]


async def test_non_string_junk_in_notes_is_dropped_not_stringified():
    runner = _said('{"notes": ["real note", 42, null, {"x": 1}, ""]}')

    review = await review_skill(runner, _parent(), "s", PAYLOAD)

    assert review.notes == ["real note"]


async def test_a_reply_that_says_notes_but_carries_none_as_strings_keeps_its_words():
    """Review round 1: four shapes of "there are notes" all came back `ok`
    with nothing — the exact format slip the parser exists to keep. When the
    object says `notes` and no string notes were found, the whole reply is
    the note, as it already was when no object was found at all."""
    from workspace_app.api.skill_review import parse_review

    shapes = [
        '{"verdict":"notes","notes":[{"file":"SKILL.md","note":"hardcoded path"}]}',
        '{"verdict":"notes","notes":"SKILL.md: hardcoded path"}',
        '{"verdict":"notes"} — the description never says when to use it.',
    ]
    for reply in shapes:
        review = parse_review(reply, model="m")
        assert review.verdict == "notes", reply
        assert (
            review.notes and "hardcoded path" in review.notes[0] or "never says" in review.notes[0]
        )


async def test_notes_that_are_present_but_not_strings_are_kept_whatever_the_verdict_says():
    """Round 2's mirror of the round-1 slip: `{"verdict":"ok","notes":[{…}]}`
    read as a clean bill with the notes dropped. Notes that are THERE and not
    strings are notes; the verdict never overrules them."""
    from workspace_app.api.skill_review import parse_review

    said = '{"verdict":"ok","notes":[{"file":"SKILL.md","note":"hardcoded"}]}'
    review = parse_review(said, model="m")

    assert review.verdict == "notes" and "hardcoded" in review.notes[0]


async def test_when_a_reply_holds_a_draft_and_a_final_object_the_final_wins():
    """A reasoning model that writes `Draft: {…ok…} Final: {…notes…}` in the
    answer channel: the LAST review-shaped object is the answer."""
    from workspace_app.api.skill_review import parse_review

    reply = 'Draft: {"verdict":"ok","notes":[]}\nFinal: {"verdict":"notes","notes":["n1"]}'
    assert parse_review(reply, model="m").notes == ["n1"]


# ── no review, no publish ────────────────────────────────────────────────────


async def test_a_failed_review_raises_instead_of_letting_the_skill_through():
    """`run_agent_task` tells the parent agent a sub-agent failed so it can work
    around it. There is nothing to work around here: an unreviewed skill is
    exactly what must not reach the skill hub, so the failure is RAISED."""
    runner = _Recorder(
        [RunError(message="giving up after 3 attempts: APIConnectionError"), RunDone()]
    )

    with pytest.raises(SkillReviewUnavailable, match="APIConnectionError"):
        await review_skill(runner, _parent(), "s", PAYLOAD)


async def test_a_reviewer_that_says_nothing_is_a_failed_review():
    runner = _Recorder([RunDone()])

    with pytest.raises(SkillReviewUnavailable):
        await review_skill(runner, _parent(), "s", PAYLOAD)


# ── the 429 wait is the runner's, inherited ──────────────────────────────────


class _ScriptedEngine(LitellmAgentRunner):
    """The REAL runner with its engine call scripted, so the review goes through
    the same retry loop a turn does. Same double as test_litellm_runner's."""

    def __init__(self, scripts: list[object], **kw: object) -> None:
        super().__init__(**kw)  # ty: ignore[invalid-argument-type]
        self._scripts = list(scripts)

    async def _run_once(self, prompt, ctx, feedback):  # noqa: ANN001
        script = self._scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for ev in script:  # ty: ignore[not-iterable]
            yield ev


def _rate_limited(headers: dict[str, str]) -> Exception:
    import litellm

    return litellm.exceptions.RateLimitError(
        message="rate limit exceeded",
        llm_provider="openai",
        model="gpt",
        response=httpx.Response(
            429, headers=headers, request=httpx.Request("POST", "http://x/v1/chat/completions")
        ),
    )


async def test_a_rate_limited_review_waits_the_stated_window_like_any_turn():
    """Q9: 429 is waited out, not treated as a failure — and it is the RUNNER's
    wait, reached by running on it, not a second implementation."""
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    runner = _ScriptedEngine(
        [_rate_limited({"retry-after": "5"}), [MessageDelta(text='{"notes": []}')]],
        sleep=sleep,
    )

    review = await review_skill(runner, _parent(), "s", PAYLOAD)

    assert slept == [5.0]
    assert review.verdict == "ok"


async def test_a_rate_limit_that_never_clears_is_a_failed_review_not_an_unreviewed_publish():
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    runner = _ScriptedEngine(
        [_rate_limited({"retry-after": "50"}), _rate_limited({"retry-after": "50"})],
        sleep=sleep,
        rate_limit_budget_s=60.0,
    )

    with pytest.raises(SkillReviewUnavailable, match="請求過於頻繁"):
        await review_skill(runner, _parent(), "s", PAYLOAD)
    assert slept == [50.0], "it waited once, the second wait would exceed the budget"
