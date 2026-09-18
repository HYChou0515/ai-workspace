"""The AI review a skill passes through on its way to the skill hub (plan P3).

Two-level review (D4): the STRUCTURAL traps — the ones the loader would skip
over in silence — are refused before this runs (`validate_skill_payload`);
what is left is judgement, and judgement never blocks. The reviewer reads the
folder the way the installing agent will and writes down what would send the
next person into a debugging loop: a description the agent can't trigger on, a
step naming a tool or file the skill doesn't ship, a script with the author's
own paths in it. Its notes are carried on the entry as `SkillHubReview`, for
the publishing tool to report.

It is a sub-agent of the publishing TURN, not a role with its own LLM wiring:
`drive_subagent` runs it on the turn's runner with the turn's `AgentConfig`
(resolved by `AppCatalog.resolve`), so the model, the endpoint, the failover
chain and the 429 wait are all the turn's own. That is also why a review that
cannot happen RAISES (Q9): the turn's engine being unreachable means the system
is broken, and the one thing a broken system must not do is publish an
unreviewed skill because nobody was there to read it. There is no "unreviewed"
verdict.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..agent.context import AgentToolContext
from ..apps.skill_hub import SkillHubReview
from ..apps.subagents import SubagentDef
from ..kb.llm_json import balanced_objects, try_object
from .events import AgentEvent
from .runner import AgentRunner
from .subagent_run import drive_subagent

# A text file is shown up to this many characters; the rest is summarised.
# SKILL.md is exempt: the body IS what is being reviewed, and publishing
# validates before it reviews (plan: 結構驗 → 掃 tool → AI 審), so by the time
# a body gets here it is under `SKILL_BODY_CAP` already.
REVIEW_FILE_CAP = 20_000
# After this many characters of file content the remaining files are listed by
# name only. One prompt, bounded, whatever the folder holds.
REVIEW_TOTAL_CAP = 100_000


class SkillReviewUnavailable(RuntimeError):
    """The review did not happen: the reviewer's turn failed or said nothing.
    Publishing stops here — never with an unreviewed entry."""


_REVIEW_PROMPT = """\
You are reviewing a skill that someone is about to publish to the skill hub, \
where every user of this platform can find it and install it into their own \
workspace.

A skill is a folder. `SKILL.md` has a frontmatter (`name`, `description`) and a \
body: the instructions an AI agent follows once it decides, from the description \
alone, that the user's request matches. `references/` holds documents the body \
tells the agent to read, `scripts/` holds programs the agent runs, `assets/` \
holds files the agent uses.

Your job is NOT to approve or reject — the structure was already checked. Your \
job is to save the next person a debugging loop. Read it twice: as the agent \
that will follow it, and as the user who installs it without ever meeting the \
author. Write down what would go wrong or leave either of them confused.

Look for:
1. The description. It is the ONLY thing the agent sees before deciding to use \
the skill. Would it know WHEN to use this from the description alone? Flag one \
that is vague, far too broad, or says what the skill does but not when to use it.
2. Instructions the agent cannot follow. Steps that refer to files, tools, \
commands, environment variables, credentials or services the skill neither ships \
nor says how to obtain; steps that contradict each other; steps that only work on \
the author's own machine or with the author's own paths.
3. Scripts. Hardcoded absolute paths, embedded secrets, dependencies the body \
never mentions, a script the body never tells the agent to run.
4. Anything that would make the skill silently do nothing, or the wrong thing.

Do not comment on style, tone or length. Do not repeat the skill back. Do not \
list what is fine.

Answer with ONE JSON object and nothing else:
{"verdict": "ok" | "notes", "notes": ["...", "..."]}
Use "ok" with an empty list when you found nothing worth telling the publisher. \
Each note is one sentence that names the file and says what to change. At most 8 \
notes, most important first, written in the language the SKILL.md body is \
written in.
"""

REVIEWER = SubagentDef(
    name="skill-hub-reviewer",
    description="Reads a skill folder before it is published and notes what would trip "
    "the next person up.",
    tools=[],
    body=_REVIEW_PROMPT,
)


def render_review_prompt(folder: str, payload: Mapping[str, bytes]) -> str:
    """The reviewer's user message: the file list, then every text file's
    content — `SKILL.md` first and whole, the rest capped — with binary files
    named but never pasted."""
    order = ["SKILL.md"] + sorted(p for p in payload if p != "SKILL.md")
    shown: list[tuple[str, str]] = []
    listing: list[str] = []
    total = 0
    for rel in order:
        if rel not in payload:
            continue
        data = payload[rel]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            listing.append(f"- {rel} (binary, {len(data)} bytes — not shown)")
            continue
        if rel != "SKILL.md" and total >= REVIEW_TOTAL_CAP:
            listing.append(f"- {rel} ({len(data)} bytes — not shown, review budget reached)")
            continue
        if rel != "SKILL.md" and len(text) > REVIEW_FILE_CAP:
            text = (
                text[:REVIEW_FILE_CAP]
                + f"\n… (truncated, {len(text) - REVIEW_FILE_CAP} more characters)"
            )
        listing.append(f"- {rel} ({len(data)} bytes)")
        shown.append((rel, text))
        total += len(text)
    parts = [f"Skill folder: `{folder}/`", "", "Files:", *listing]
    for rel, text in shown:
        parts += ["", f"## {rel}", "", text.rstrip("\n")]
    return "\n".join(parts) + "\n"


def parse_review(answer: str, *, model: str) -> SkillHubReview:
    """The reviewer's reply as a `SkillHubReview`.

    The verdict is DERIVED from the notes, not read from the model: a reply that
    writes notes and stamps `ok` on them has contradicted itself, and the notes
    are the review. A reply with no review-shaped object in it at all is kept
    as one note rather than dropped — the reviewer did review, it just did not
    answer in the shape it was asked for, and publishing that as `ok` would
    turn a format slip into a clean bill."""
    shaped = [
        o
        for o in map(try_object, balanced_objects(answer))
        if o is not None and ("notes" in o or "verdict" in o)
    ]
    # The LAST review-shaped object: a model that drafts and then finalises
    # ("Draft: {…} Final: {…}") means the final one.
    obj = shaped[-1] if shaped else None
    if obj is None:
        return SkillHubReview(verdict="notes", notes=[answer.strip()], model=model)
    raw = obj.get("notes")
    notes = (
        [n.strip() for n in raw if isinstance(n, str) and n.strip()]
        if isinstance(raw, list)
        else []
    )
    # When no note came through as a string, the (notes, verdict) table is:
    #   notes GIVEN and not empty (a list of dicts, one bare string) → a
    #     format slip, whatever the verdict says — round 1 gated this on
    #     `verdict == "notes"`, and `{"verdict":"ok","notes":[{…}]}` read as
    #     a clean bill with the notes dropped (review round 2);
    #   notes ABSENT and `verdict: notes` → the notes are the prose beside the
    #     object, another slip;
    #   notes given and EMPTY (`[]`, `""`, blank strings) → "notes: none",
    #     whatever the verdict says: the notes decide.
    # A slip must not read as a clean bill: the whole reply is the note, as it
    # is when no object was found at all.
    given = raw is not None
    empty = raw == "" or (
        isinstance(raw, list) and all(isinstance(n, str) and not n.strip() for n in raw)
    )
    if not notes and ((given and not empty) or (not given and obj.get("verdict") == "notes")):
        notes = [answer.strip()]
    return SkillHubReview(verdict="notes" if notes else "ok", notes=notes, model=model)


async def review_skill(
    runner: AgentRunner,
    parent_ctx: AgentToolContext,
    folder: str,
    payload: Mapping[str, bytes],
    *,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> SkillHubReview:
    """Review `payload` (the `folder/` a publish reads from the workspace) as a
    sub-agent of the turn `parent_ctx` belongs to. `on_event` relays the
    reviewer's work into the publishing turn's tool card. Raises
    `SkillReviewUnavailable` when the review did not happen."""
    answer, failure = await drive_subagent(
        runner, parent_ctx, REVIEWER, render_review_prompt(folder, payload), on_event=on_event
    )
    if failure is not None:
        raise SkillReviewUnavailable(failure)
    if not answer:
        raise SkillReviewUnavailable("the reviewer ended its turn without writing anything")
    cfg = parent_ctx.agent_config
    assert cfg is not None  # `drive_subagent` refused a turn without one already
    return parse_review(answer, model=cfg.model)
